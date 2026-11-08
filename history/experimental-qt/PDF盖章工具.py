# -*- coding: utf-8 -*-
"""
PDF 盖章工具（独立窗口程序 · 单文件版）
=====================================================================
双击打开就是独立窗口（不打开浏览器、不依赖网页），文件选择用系统原生
对话框，关闭窗口即结束程序，后台无残留。

默认把结果输出到「源文件所在目录\\已签章\\」，原件不动；也可切换成
「覆盖原文件（自动备份）」或「自定义输出目录」。

规则：整幅透明章图层 1:1 叠加、不裁剪不缩放、右下角对齐、逐页按图幅
定位；盖章后自动逐页校验，并可一键从备份恢复。

依赖：PyMuPDF + PySide6（exe 版已全部内置，目标机器无需装任何东西）。
"""

import glob
import io
import os
import shutil
import sys
import threading
import traceback

if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

import pymupdf
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QComboBox, QLabel, QRadioButton, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QProgressBar, QPlainTextEdit, QFileDialog,
    QMessageBox, QDialog, QCheckBox, QDoubleSpinBox, QButtonGroup,
    QAbstractItemView, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QFont

BACKUP_DIRNAME = "_未盖章原件"
STAMPED_DIRNAME = "已签章"
ANCHORS = [("右下角对齐（推荐）", "bottom-right"),
           ("左上角原点对齐", "top-left"),
           ("右上角对齐", "top-right"),
           ("左下角对齐", "bottom-left"),
           ("页面居中", "center")]


# --------------------------------------------------------------------------- #
# PDF 处理逻辑
# --------------------------------------------------------------------------- #
def fmt(r):
    return "({:.0f},{:.0f})-({:.0f},{:.0f})".format(r.x0, r.y0, r.x1, r.y1)


def open_doc(path):
    d = pymupdf.open(path)
    if d.needs_pass:
        d.close()
        raise ValueError("PDF 已加密，无法处理")
    return d


def color_bbox(page, dpi=60, kind="red", step=3, clip=None):
    pm = page.get_pixmap(dpi=dpi, clip=clip, alpha=False)
    w, h, n = pm.width, pm.height, pm.n
    s, st = pm.samples, pm.stride
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(0, h, step):
        row = s[y * st: y * st + w * n]
        for x in range(0, w, step):
            i = x * n
            r, g, b = row[i], row[i + 1], row[i + 2]
            if kind == "blue":
                hit = b > 110 and b - r > 40 and b - g > 20
            elif kind == "red":
                hit = r > 130 and r - g > 55 and r - b > 55
            else:
                hit = ((r > 130 and r - g > 55 and r - b > 55) or
                       (b > 110 and b - r > 40 and b - g > 20))
            if hit:
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
    if maxx < 0:
        return None
    sc = 72.0 / dpi
    ox, oy = (clip.x0, clip.y0) if clip else (page.rect.x0, page.rect.y0)
    return pymupdf.Rect(minx * sc + ox, miny * sc + oy,
                        (maxx + 1) * sc + ox, (maxy + 1) * sc + oy)


def detect_color(page, dpi=60, color="auto"):
    for kind in ((color,) if color != "auto" else ("red", "blue")):
        box = color_bbox(page, dpi, kind)
        if box:
            return box, kind
    return None, None


def ink_ratio(page, clip, color="auto", dpi=80, step=2):
    pm = page.get_pixmap(dpi=dpi, clip=clip, alpha=False)
    w, h, n = pm.width, pm.height, pm.n
    s, st = pm.samples, pm.stride
    hits = total = 0
    for y in range(0, h, step):
        row = s[y * st: y * st + w * n]
        for x in range(0, w, step):
            i = x * n
            r, g, b = row[i], row[i + 1], row[i + 2]
            total += 1
            if color == "blue":
                hit = b > 110 and b - r > 40 and b - g > 20
            elif color == "auto":
                hit = ((r > 130 and r - g > 55 and r - b > 55) or
                       (b > 110 and b - r > 40 and b - g > 20))
            else:
                hit = r > 130 and r - g > 55 and r - b > 55
            if hit:
                hits += 1
    return hits / total if total else 0.0


def placement(page_rect, sw, sh, anchor, dx, dy):
    x0, y0, pw, ph = page_rect.x0, page_rect.y0, page_rect.width, page_rect.height
    pos = {"bottom-right": (pw - sw, ph - sh), "top-left": (0.0, 0.0),
           "top-right": (pw - sw, 0.0), "bottom-left": (0.0, ph - sh),
           "center": ((pw - sw) / 2.0, (ph - sh) / 2.0)}
    ox, oy = pos.get(anchor, pos["bottom-right"])
    return pymupdf.Rect(x0 + ox + dx, y0 + oy + dy,
                        x0 + ox + sw + dx, y0 + oy + sh + dy)


def parse_pages(spec, n):
    if spec == "all":
        return list(range(n))
    if spec in ("first", "first-page"):
        return [0]
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    idxs = sorted({i for i in out if 0 <= i < n})
    if not idxs:
        raise ValueError("页码无效或超出范围（共 {} 页）".format(n))
    return idxs


def inspect_seal(path, dpi=50):
    d = open_doc(path)
    try:
        if len(d) != 1:
            return None
        p = d[0]
        box, kind = detect_color(p, dpi=dpi, color="auto")
        if not box or not p.get_images(full=True):
            return None
        w, h = p.rect.width, p.rect.height
        return {"path": path, "name": os.path.basename(path), "w": w, "h": h,
                "x0": p.rect.x0, "y0": p.rect.y0,
                "box": [box.x0, box.y0, box.x1, box.y1], "color": kind,
                "ratio": (box.width * box.height) / (w * h)}
    finally:
        d.close()


def is_seal_candidate(info):
    return bool(info and info["ratio"] < 0.10 and 400 < info["w"] < 2600
                and 400 < info["h"] < 2000)


def looks_like_seal(path):
    try:
        d = open_doc(path)
        single = len(d) == 1
        d.close()
        return single and is_seal_candidate(inspect_seal(path))
    except Exception:
        return False


def file_summary(path):
    d = open_doc(path)
    try:
        sizes = {}
        for p in d:
            k = "{}×{}".format(round(p.rect.width), round(p.rect.height))
            sizes[k] = sizes.get(k, 0) + 1
        return len(d), ", ".join("{}({}页)".format(k, v) for k, v in sorted(sizes.items()))
    finally:
        d.close()


def verify_file(path, color="auto"):
    d = open_doc(path)
    try:
        missing = []
        for i, p in enumerate(d):
            if not detect_color(p, dpi=60, color=color)[0]:
                missing.append(i)
        return missing
    finally:
        d.close()


def output_path(f, st):
    """按输出方式计算结果路径。"""
    if st["mode"] == "stamped":
        return os.path.join(os.path.dirname(f), STAMPED_DIRNAME, os.path.basename(f))
    if st["mode"] == "custom" and st["outdir"]:
        return os.path.join(st["outdir"], os.path.basename(f))
    return f


# --------------------------------------------------------------------------- #
# 全局状态
# --------------------------------------------------------------------------- #
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.seals, self.seal_index, self.files = [], -1, []
        self.logs = ["欢迎使用。先添加章和文件，建议先点「预览效果」确认位置。"]
        self.busy = False
        self.on_log = None
        self.on_progress = None

    def log(self, msg):
        with self.lock:
            self.logs.append(str(msg))
            if len(self.logs) > 400:
                del self.logs[:-400]
        if self.on_log:
            try:
                self.on_log(str(msg))
            except Exception:
                pass

    def progress(self, done, total):
        if self.on_progress:
            try:
                self.on_progress(done, total)
            except Exception:
                pass

    def seal(self):
        with self.lock:
            return self.seals[self.seal_index] if 0 <= self.seal_index < len(self.seals) else None


S = State()


def add_seal_paths(paths):
    added = 0
    for p in paths:
        try:
            info = inspect_seal(p)
        except Exception as e:
            S.log("章读取失败 {}：{}".format(os.path.basename(p), e))
            continue
        if not is_seal_candidate(info):
            S.log("跳过（不像整幅透明章图层）：{}".format(os.path.basename(p)))
            continue
        with S.lock:
            if any(s["path"] == p for s in S.seals):
                continue
            S.seals.append(info)
            if S.seal_index < 0:
                S.seal_index = 0
            added += 1
    if added:
        S.log("已添加 {} 个章。".format(added))


def add_file_paths(paths):
    with S.lock:
        excluded = {s["path"] for s in S.seals} | set(S.files)
    to_add = []
    for p in paths:
        p = os.path.abspath(p)
        if not p.lower().endswith(".pdf") or not os.path.isfile(p):
            continue
        if BACKUP_DIRNAME in os.path.dirname(p) or p in excluded or p in to_add:
            continue
        if looks_like_seal(p):
            S.log("跳过章图层（不作为待盖章文件）：{}".format(os.path.basename(p)))
            continue
        to_add.append(p)
    with S.lock:
        S.files.extend(to_add)
    if to_add:
        S.log("已添加 {} 个待盖章文件。".format(len(to_add)))


def import_paths(paths):
    """加文件时自动把章图层归到章列表，其余作为目标。"""
    seal_like = [p for p in paths if p.lower().endswith(".pdf") and looks_like_seal(p)]
    if seal_like:
        add_seal_paths(seal_like)
    add_file_paths(paths)


def autoload_seals():
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
    found = []
    for p in sorted(glob.glob(os.path.join(base, "*.pdf"))):
        name = os.path.basename(p)
        if "章" not in name and "seal" not in name.lower():
            continue
        if looks_like_seal(p):
            found.append(p)
    if found:
        add_seal_paths(found)
        S.log("已自动载入程序同目录的 {} 个章 PDF。".format(len(found)))


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
def render_preview(seal, path, st):
    doc, sdoc = open_doc(path), open_doc(seal["path"])
    try:
        idxs = parse_pages(st["spec"], len(doc))
        i = idxs[0]
        p = doc[i]
        r = placement(p.rect, seal["w"], seal["h"], st["anchor"], st["dx"], st["dy"])
        p.show_pdf_page(r, sdoc, 0, overlay=True)
        dpi = int(min(110, max(16, 900.0 / p.rect.width * 72)))
        return p.get_pixmap(dpi=dpi, alpha=False).tobytes("png"), i + 1, os.path.basename(path)
    finally:
        doc.close(); sdoc.close()


def stamp_job(seal, files, st):
    total, sealed = len(files), 0
    sdoc = open_doc(seal["path"])
    try:
        for fi, f in enumerate(files):
            name = os.path.basename(f)
            if os.path.abspath(f) == os.path.abspath(seal["path"]):
                S.log("跳过（这是章文件本身）：{}".format(name))
                S.progress(fi + 1, total)
                continue
            doc = open_doc(f)
            n_pages = len(doc)
            try:
                idxs = parse_pages(st["spec"], n_pages)
            except ValueError as e:
                S.log("跳过 {}：{}".format(name, e))
                doc.close(); S.progress(fi + 1, total)
                continue

            dst = output_path(f, st)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if st["mode"] == "overwrite" and st["backup"]:
                backup = os.path.join(os.path.dirname(f), BACKUP_DIRNAME, name)
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                if not os.path.exists(backup):
                    shutil.copy2(f, backup)

            bx = pymupdf.Rect(seal["box"])
            skip = []
            for i in idxs:
                if not st["skip"]:
                    continue
                p = doc[i]
                r0 = placement(p.rect, seal["w"], seal["h"], st["anchor"], st["dx"], st["dy"])
                zone = pymupdf.Rect(
                    r0.x0 + (bx.x0 - seal["x0"]) - 4, r0.y0 + (bx.y0 - seal["y0"]) - 4,
                    r0.x0 + (bx.x1 - seal["x0"]) + 4, r0.y0 + (bx.y1 - seal["y0"]) + 4)
                zone &= p.rect
                if not zone.is_empty and ink_ratio(p, zone, seal["color"]) >= 0.04:
                    skip.append(i)
            applied = [i for i in idxs if i not in skip]
            for i in applied:
                p = doc[i]
                r = placement(p.rect, seal["w"], seal["h"], st["anchor"], st["dx"], st["dy"])
                p.show_pdf_page(r, sdoc, 0, overlay=True)
            tmp = dst + ".tmp.pdf"
            doc.save(tmp, garbage=3, deflate=True)
            doc.close()
            os.replace(tmp, dst)
            sealed += len(applied)

            bad = [i for i in verify_file(dst, seal["color"]) if i in set(applied)]
            where = os.path.basename(os.path.dirname(dst))
            S.log("OK {} → {} | 共 {} 页，本次盖 {} 页 | {}{}".format(
                name, where, n_pages, len(applied),
                "校验通过" if not bad else "警告：第 {} 页未检出章".format(bad),
                "（跳过已盖章页 {}）".format(skip) if skip else ""))
            S.progress(fi + 1, total)
    finally:
        sdoc.close()
    S.log("=== 全部完成：{} 个文件、{} 页盖章 ===".format(total, sealed))
    if st["mode"] == "stamped":
        S.log("结果在各源文件目录下的「{}」文件夹，原件未改动。".format(STAMPED_DIRNAME))
    elif st["mode"] == "custom":
        S.log("结果在 {}，原件未改动。".format(st["outdir"]))
    elif st["backup"]:
        S.log("原件已备份到各目录下的 {} 文件夹，可一键恢复。".format(BACKUP_DIRNAME))


def restore_job(files):
    n = 0
    for f in files:
        src = os.path.join(os.path.dirname(f), BACKUP_DIRNAME, os.path.basename(f))
        if os.path.isfile(src):
            shutil.copy2(src, f); n += 1
            S.log("已恢复：{}".format(os.path.basename(f)))
        else:
            S.log("未找到备份，跳过：{}".format(os.path.basename(f)))
    S.log("共恢复 {} 个文件。".format(n))


class Worker(QThread):
    job_done = Signal(bool, str)

    def __init__(self, fn, args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.fn(*self.args)
            self.job_done.emit(True, "完成")
        except Exception as e:
            S.log("出错：\n" + traceback.format_exc())
            self.job_done.emit(False, str(e))


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    log_signal = Signal(str)
    progress_signal = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.worker = None
        self.setWindowTitle("PDF 盖章工具")
        self.resize(1020, 830)
        self.setMinimumSize(900, 700)
        self.setAcceptDrops(True)
        self.log_signal.connect(self._append_log)
        self.progress_signal.connect(self._on_progress)
        S.on_log = lambda m: self.log_signal.emit(m)
        S.on_progress = lambda d, t: self.progress_signal.emit(d, t)
        self._build()
        for m in S.logs:
            self.logbox.appendPlainText(m)
        self.refresh_seals()
        self.refresh_files()

    # ---------------- UI ---------------- #
    def _build(self):
        root = QWidget(); self.setCentralWidget(root)
        v = QVBoxLayout(root); v.setSpacing(10); v.setContentsMargins(12, 12, 12, 12)

        head = QLabel("PDF 盖章工具  ·  整幅章图层 1:1 叠加  ·  关闭窗口即退出程序")
        head.setStyleSheet("font-size:16px;font-weight:bold;color:#1d4ed8;padding:2px 0 4px 0;")
        v.addWidget(head)

        # ① 章
        g1 = QGroupBox("① 选盖什么章"); l1 = QVBoxLayout(g1)
        r = QHBoxLayout()
        self.seal_combo = QComboBox(); self.seal_combo.setMinimumWidth(300)
        self.seal_combo.currentIndexChanged.connect(self._seal_changed)
        b = QPushButton("添加章 PDF…"); b.clicked.connect(self.add_seals)
        b2 = QPushButton("移除当前章"); b2.clicked.connect(self.remove_seal)
        r.addWidget(QLabel("当前章：")); r.addWidget(self.seal_combo, 1)
        r.addWidget(b); r.addWidget(b2); l1.addLayout(r)
        self.seal_info = QLabel(""); self.seal_info.setStyleSheet("color:#666;")
        self.seal_info.setWordWrap(True); l1.addWidget(self.seal_info)
        v.addWidget(g1)

        # ② 文件
        g2 = QGroupBox("② 要盖章的 PDF"); l2 = QVBoxLayout(g2)
        r = QHBoxLayout()
        for text, fn in (("添加 PDF…（可多选）", self.add_files),
                         ("添加整个文件夹…", self.add_folder),
                         ("移除选中", self.remove_selected),
                         ("清空", self.clear_files)):
            btn = QPushButton(text); btn.clicked.connect(fn); r.addWidget(btn)
        r.addStretch(1)
        self.file_count = QLabel(""); r.addWidget(self.file_count)
        l2.addLayout(r)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["文件名", "页数", "图幅", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 70); self.table.setColumnWidth(2, 230); self.table.setColumnWidth(3, 100)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(170)
        l2.addWidget(self.table)
        tip = QLabel("提示：也可以直接把 PDF 文件拖进这个窗口；文件夹里混着章 PDF 时会自动归到章列表。")
        tip.setStyleSheet("color:#888;"); l2.addWidget(tip)
        v.addWidget(g2, 1)

        # ③ 设置
        g3 = QGroupBox("③ 输出位置、盖哪些页、章的位置"); l3 = QVBoxLayout(g3)
        r = QHBoxLayout()
        r.addWidget(QLabel("输出到："))
        self.out_mode = QComboBox()
        self.out_mode.addItems(["源文件目录\\已签章（推荐，原件不动）",
                                "覆盖原文件（自动备份）", "自定义输出目录…"])
        self.out_mode.currentIndexChanged.connect(self._out_changed)
        r.addWidget(self.out_mode, 1)
        self.out_edit = QLineEdit(); self.out_edit.setPlaceholderText("自定义输出目录路径")
        self.out_edit.setEnabled(False); r.addWidget(self.out_edit, 1)
        self.out_browse = QPushButton("浏览…"); self.out_browse.setEnabled(False)
        self.out_browse.clicked.connect(self.choose_outdir); r.addWidget(self.out_browse)
        l3.addLayout(r)

        r = QHBoxLayout(); r.addWidget(QLabel("盖哪些页："))
        self.rb_all = QRadioButton("每一页"); self.rb_all.setChecked(True)
        self.rb_first = QRadioButton("仅首页"); self.rb_custom = QRadioButton("指定页码")
        self.pages_group = QButtonGroup(self)
        for i, rb in enumerate((self.rb_all, self.rb_first, self.rb_custom)):
            self.pages_group.addButton(rb, i); r.addWidget(rb)
        self.page_edit = QLineEdit(); self.page_edit.setPlaceholderText("如 1,3,5 或 2-4")
        self.page_edit.setMaximumWidth(190); self.page_edit.setEnabled(False)
        self.rb_custom.toggled.connect(self.page_edit.setEnabled)
        r.addWidget(self.page_edit); r.addStretch(1); l3.addLayout(r)

        r = QHBoxLayout(); r.addWidget(QLabel("章的位置："))
        self.anchor = QComboBox()
        for label, _ in ANCHORS:
            self.anchor.addItem(label)
        r.addWidget(self.anchor)
        r.addWidget(QLabel("水平微调")); self.dx = QDoubleSpinBox()
        self.dx.setRange(-9999, 9999); self.dx.setDecimals(1); self.dx.setMaximumWidth(90)
        r.addWidget(self.dx)
        r.addWidget(QLabel("垂直微调")); self.dy = QDoubleSpinBox()
        self.dy.setRange(-9999, 9999); self.dy.setDecimals(1); self.dy.setMaximumWidth(90)
        r.addWidget(self.dy)
        r.addWidget(QLabel("pt（正数=右/下）"))
        self.cb_backup = QCheckBox("覆盖时备份原件"); self.cb_backup.setChecked(True)
        self.cb_skip = QCheckBox("跳过已盖章的页"); self.cb_skip.setChecked(True)
        r.addWidget(self.cb_backup); r.addWidget(self.cb_skip); r.addStretch(1)
        l3.addLayout(r)
        v.addWidget(g3)

        # ④ 执行
        g4 = QGroupBox("④ 执行"); l4 = QVBoxLayout(g4)
        r = QHBoxLayout()
        self.btn_stamp = QPushButton("开始盖章 ▶"); self.btn_stamp.setObjectName("primary")
        self.btn_stamp.clicked.connect(self.do_stamp)
        self.btn_preview = QPushButton("预览效果"); self.btn_preview.clicked.connect(self.do_preview)
        b = QPushButton("从备份恢复原件"); b.clicked.connect(self.do_restore)
        b2 = QPushButton("打开输出文件夹"); b2.clicked.connect(self.open_output)
        for w in (self.btn_stamp, self.btn_preview, b, b2):
            r.addWidget(w)
        r.addStretch(1); self.status = QLabel("就绪")
        self.status.setStyleSheet("color:#059669;font-weight:bold;"); r.addWidget(self.status)
        l4.addLayout(r)
        self.bar = QProgressBar(); self.bar.setValue(0); l4.addWidget(self.bar)
        self.logbox = QPlainTextEdit(); self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(500); self.logbox.setMinimumHeight(130)
        self.logbox.setStyleSheet("font-family:Consolas;font-size:12px;background:#fafbfd;")
        l4.addWidget(self.logbox)
        v.addWidget(g4)

    # ---------------- 信号 ---------------- #
    def _append_log(self, msg):
        self.logbox.appendPlainText(msg)

    def _on_progress(self, done, total):
        self.bar.setValue(int(100 * done / total) if total else 0)
        self.status.setText("处理中 {}/{}".format(done, total))

    def _out_changed(self, i):
        custom = (i == 2)
        self.out_edit.setEnabled(custom); self.out_browse.setEnabled(custom)
        self.cb_backup.setEnabled(i == 1)

    def _seal_changed(self, i):
        info = S.seal()
        if not info:
            self.seal_info.setText("还没有章。章 PDF 应是整页透明图层（A2 出图章 / A4 公章）。")
            return
        self.seal_info.setText(
            "幅面 {:.1f}×{:.1f}pt ｜ 章在图层内 {} ｜ 距图层右边缘 {:.0f}pt ｜ {}".format(
                info["w"], info["h"], fmt(pymupdf.Rect(info["box"])),
                info["w"] - (info["box"][2] - info["x0"]), info["color"]))

    # ---------------- 列表 ---------------- #
    def refresh_seals(self):
        self.seal_combo.blockSignals(True)
        self.seal_combo.clear()
        with S.lock:
            names = [s["name"] for s in S.seals]
            idx = S.seal_index
        self.seal_combo.addItems(names)
        if 0 <= idx < len(names):
            self.seal_combo.setCurrentIndex(idx)
        self.seal_combo.blockSignals(False)
        self._seal_changed(idx)

    def refresh_files(self):
        with S.lock:
            files = list(S.files)
            mode = self.out_mode.currentIndex()
            custom = self.out_edit.text().strip().strip('"')
        st = {"mode": ("stamped", "overwrite", "custom")[mode], "outdir": custom}
        self.table.setRowCount(0)
        for f in files:
            try:
                n, desc = file_summary(f)
                dst = output_path(f, st)
                if st["mode"] == "overwrite":
                    state = "已处理" if os.path.isfile(os.path.join(
                        os.path.dirname(f), BACKUP_DIRNAME, os.path.basename(f))) else "待盖章"
                else:
                    state = "已输出" if os.path.isfile(dst) else "待盖章"
                row = [os.path.basename(f), str(n), desc, state]
            except Exception as e:
                row = [os.path.basename(f), "?", "读取失败", str(e)[:20]]
            i = self.table.rowCount(); self.table.insertRow(i)
            for j, val in enumerate(row):
                it = QTableWidgetItem(val)
                if j in (1, 3):
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, j, it)
        self.file_count.setText("共 {} 个文件".format(len(files)))

    def selected_files(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        with S.lock:
            files = list(S.files)
        return [files[i] for i in rows if 0 <= i < len(files)] or files

    # ---------------- 添加 ---------------- #
    def add_seals(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择章 PDF（可多选）", "", "PDF 文件 (*.pdf)")
        if paths:
            add_seal_paths(paths); self.refresh_seals()

    def remove_seal(self):
        with S.lock:
            if 0 <= S.seal_index < len(S.seals):
                S.seals.pop(S.seal_index)
                S.seal_index = 0 if S.seals else -1
        self.refresh_seals()

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要盖章的 PDF（可多选）", "", "PDF 文件 (*.pdf)")
        if paths:
            import_paths(paths); self.refresh_seals(); self.refresh_files()

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含 PDF 的文件夹", "")
        if d:
            import_paths(sorted(glob.glob(os.path.join(d, "*.pdf"))))
            self.refresh_seals(); self.refresh_files()

    def choose_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", "")
        if d:
            self.out_edit.setText(d)

    def remove_selected(self):
        sel = set(self.selected_files()) if self.table.selectionModel().selectedRows() else set()
        if not sel:
            QMessageBox.information(self, "提示", "请先在列表里选中要移除的文件")
            return
        with S.lock:
            S.files = [f for f in S.files if f not in sel]
        self.refresh_files()

    def clear_files(self):
        with S.lock:
            S.files.clear()
        self.refresh_files()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        expanded = []
        for p in paths:
            expanded.extend(sorted(glob.glob(os.path.join(p, "*.pdf"))) if os.path.isdir(p) else [p])
        if expanded:
            import_paths(expanded); self.refresh_seals(); self.refresh_files()

    # ---------------- 参数 ---------------- #
    def settings(self):
        mode = ("stamped", "overwrite", "custom")[self.out_mode.currentIndex()]
        outdir = self.out_edit.text().strip().strip('"')
        if mode == "custom" and (not outdir or not os.path.isdir(outdir)):
            raise ValueError("请选择一个有效的自定义输出目录")
        if self.rb_all.isChecked():
            spec = "all"
        elif self.rb_first.isChecked():
            spec = "first"
        else:
            spec = self.page_edit.text().strip()
        parse_pages(spec, 1000000)   # 只校验格式
        return {"mode": mode, "outdir": outdir, "spec": spec,
                "anchor": ANCHORS[self.anchor.currentIndex()][1],
                "dx": self.dx.value(), "dy": self.dy.value(),
                "backup": self.cb_backup.isChecked(), "skip": self.cb_skip.isChecked()}

    def _ready(self, need_files=True):
        seal = S.seal()
        if not seal:
            QMessageBox.warning(self, "缺少章", "请先添加并选择一个章 PDF"); return None
        files = self.selected_files() if need_files else []
        if need_files and not files:
            QMessageBox.warning(self, "缺少文件", "请先添加要盖章的 PDF"); return None
        try:
            st = self.settings()
        except ValueError as e:
            QMessageBox.warning(self, "参数有误", str(e)); return None
        return seal, files, st

    # ---------------- 动作 ---------------- #
    def do_preview(self):
        r = self._ready()
        if not r:
            return
        seal, files, st = r
        try:
            png, page, name = render_preview(seal, files[0], st)
            pix = QPixmap(); pix.loadFromData(png, "PNG")
            pix = pix.scaled(1000, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            dlg = QDialog(self)
            dlg.setWindowTitle("预览：{} 第 {} 页（未写入文件）".format(name, page))
            lay = QVBoxLayout(dlg)
            lab = QLabel(); lab.setPixmap(pix); lay.addWidget(lab)
            lay.addWidget(QLabel("章的位置与实际盖章结果一致；不满意可调整「章的位置」后再预览。"))
            dlg.exec()
            S.log("已生成预览（第 {} 页），未修改任何文件。".format(page))
        except Exception as e:
            QMessageBox.critical(self, "预览失败", str(e))

    def _start(self, fn, args, label):
        if self.worker and self.worker.isRunning():
            return
        self.btn_stamp.setEnabled(False); self.btn_preview.setEnabled(False)
        self.bar.setValue(0); self.status.setText("处理中")
        S.log("\n=== {} ===".format(label))
        self.worker = Worker(fn, args)
        self.worker.job_done.connect(self._job_done)
        self.worker.start()

    def _job_done(self, ok, msg):
        self.btn_stamp.setEnabled(True); self.btn_preview.setEnabled(True)
        self.status.setText("完成" if ok else "出错")
        self.refresh_files()
        if not ok:
            QMessageBox.critical(self, "处理中断", msg)
        else:
            QMessageBox.information(self, "完成", "盖章完成，详情见下方日志。")

    def do_stamp(self):
        r = self._ready()
        if r:
            self._start(stamp_job, (r[0], r[1], r[2]), "开始盖章")

    def do_restore(self):
        r = self._ready(need_files=False)
        if not r:
            return
        st = r[2]
        if st["mode"] != "overwrite":
            QMessageBox.information(
                self, "无需恢复",
                "当前输出到新目录，原件没有被修改。\n如需清掉结果，请到「{}」或自定义输出目录手动删除。"
                .format(STAMPED_DIRNAME))
            return
        if QMessageBox.question(self, "确认", "用备份覆盖当前文件，恢复成未盖章原件？") \
                != QMessageBox.StandardButton.Yes:
            return
        self._start(restore_job, (self.selected_files(),), "从备份恢复")

    def open_output(self):
        files = self.selected_files()
        if not files:
            QMessageBox.information(self, "提示", "还没有文件"); return
        try:
            st = self.settings()
            d = os.path.dirname(output_path(files[0], st))
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            QMessageBox.warning(self, "打开失败", str(e))

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "正在处理", "盖章尚未完成，请等完成后再关闭窗口。")
            e.ignore()
        else:
            e.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet("""
        QGroupBox{font-weight:bold;border:1px solid #dfe3ea;border-radius:8px;
                  margin-top:12px;padding:14px 10px 10px 10px;}
        QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px;color:#2563eb;}
        QPushButton{padding:6px 12px;border:1px solid #d0d7e2;border-radius:6px;background:#fff;}
        QPushButton:hover{border-color:#2563eb;color:#2563eb;}
        QPushButton:disabled{color:#aaa;background:#f5f6f8;}
        QPushButton#primary{background:#2563eb;color:#fff;border-color:#2563eb;font-weight:bold;padding:8px 20px;}
        QTableWidget{gridline-color:#eef1f6;}
        QLineEdit,QComboBox,QDoubleSpinBox{padding:4px 6px;}
    """)
    autoload_seals()
    preload = [a for a in sys.argv[1:] if a.lower().endswith(".pdf") and os.path.isfile(a)]
    if preload:
        import_paths(preload)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
