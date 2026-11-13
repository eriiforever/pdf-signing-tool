# -*- coding: utf-8 -*-
"""
PDF 盖章工具 · 图形界面版 v2.2
=====================================================================
正式交付是单文件 exe（PyInstaller 打包），双击就是原生窗口：
不装 Python、不装 Qt、不开浏览器、没有黑框，关窗即退出。

  * 窗口用 Python 自带的 tkinter（标准库）
  * PDF 叠加用纯 Python pypdf（不用 PyMuPDF）
  * 章图层内置一份示例；换章可放进 exe 旁的 seals\\ 或点「浏览」

盖章规则（与原项目一致，不可改）：
  1. 章 PDF 是整幅图幅大小的透明图层；整幅 1:1 叠加，绝不裁剪/缩放/旋转。
  2. 图层右下角对齐页面右下角，逐页读取页面尺寸，不写死坐标。
  3. 盖章前原件不动；覆盖模式下先备份到「_未盖章原件」，已存在不覆盖。

用法：
  双击 PDF盖章工具.exe                 → 打开窗口（选文件、选章、选页码）
  把 PDF 拖到 exe 图标上               → 打开窗口并自动带入文件列表
  源码模式：双击 stamp.bat / 把 PDF 拖到 stamp.bat（无窗口直盖）
  命令行：python stamp_gui.py a.pdf --pages first --out stamped

默认输出到「源文件所在目录\\已签章\\」，原件不动。
"""

import argparse
import csv
import glob
import io
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback

FROZEN = getattr(sys, "frozen", False)   # PyInstaller 打包后的单文件 exe
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)   # exe 解压出的内置资源目录
HERE = APP_DIR
VENDOR = os.path.join(APP_DIR, "_vendor")
if os.path.isdir(VENDOR):
    sys.path.insert(0, VENDOR)          # 源码模式优先用随包的绿色 pypdf

try:
    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                               EncodedStreamObject, NameObject, NumberObject)
except ImportError:
    sys.exit("缺少 pypdf：请确认 _vendor/pypdf 完整，或执行 pip install pypdf")

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        # 不改编码，跟随 Windows 控制台/系统区域设置（GBK），避免 cmd 与
        # 重定向到文件时出现中文乱码；只放宽不可编码字符的处理。
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

STAMPED_DIRNAME = "已签章"
BACKUP_DIRNAME = "_未盖章原件"
SEALS_DIR = os.path.join(APP_DIR, "seals")
BUILTIN_SEAL = os.path.join(BUNDLE_DIR, "示意章-测试.pdf")
CONFIG_PATH = os.path.join(APP_DIR, "stamp_config.json")
PAGE_ALL, PAGE_FIRST = "all", "first"
VERSION = "2.5"                          # 同步维护 version_info.txt 中的文件版本

DEFAULT_CONFIG = {
    "seal": "",             # 上次使用的章图层
    "pages": PAGE_ALL,      # all / first / 自定义
    "out": "stamped",       # stamped / overwrite / custom
    "exists": "overwrite",  # overwrite / ask / rename
    "dx": 0.0,              # 章位微调（pt，正=右 / 上）
    "dy": 0.0,
    "skip_stamped": False,  # 跳过已经盖过这枚章的页（默认关闭，由用户勾选）
    "report": False,        # 盖章完成后生成 txt 报告（默认关闭）
    "seal_pages": {},       # 每个章文件记住的页码策略
    "check_update": True,   # 启动时检查新版本
}


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
def deref(obj):
    """解 pypdf 的间接引用。"""
    return obj.get_object() if hasattr(obj, "get_object") else obj


def parse_pages(spec, n):
    """all / first / 1,3,5 / 2-4  →  0 基页索引列表。"""
    if spec == PAGE_ALL:
        return list(range(n))
    if spec == PAGE_FIRST:
        return [0]
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a) - 1, int(b)))   # 用户输入 1 基页码
        else:
            out.append(int(part) - 1)
    idxs = sorted({i for i in out if 0 <= i < n})
    if not idxs:
        raise ValueError("页码范围没有有效页：{}（共 {} 页）".format(spec, n))
    return idxs


SEAL_KEYWORDS = ("章", "seal", "stamp")


def list_seals(extra_dirs=None):
    """章候选来源（自动发现，不递归）：
       * seals\\ 里的所有 PDF（原行为，保留）
       * 程序目录、当前目录、已添加图纸所在目录里文件名带「章 / seal / stamp」的 PDF
       * exe 内置的示例章
    extra_dirs 传图纸所在目录，添加文件后即可扫出同目录的章。
    """
    try:
        os.makedirs(SEALS_DIR, exist_ok=True)   # exe 旁自动建一个放章的目录
    except Exception:
        pass
    seal_root = os.path.normcase(os.path.abspath(SEALS_DIR))
    dirs = [SEALS_DIR, APP_DIR, os.getcwd()]
    for d in extra_dirs or []:
        if d and os.path.isdir(d):
            dirs.append(d)

    seen, found = set(), []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            entries = sorted(os.listdir(d))
        except Exception:
            continue
        is_seals = os.path.normcase(os.path.abspath(d)) == seal_root
        for name in entries:
            if not name.lower().endswith(".pdf"):
                continue
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            # seals\\ 全收；其它目录只收文件名带章字的，避免把图纸混进来
            if not is_seals:
                stem = os.path.splitext(name)[0].lower()
                if not any(k in stem for k in SEAL_KEYWORDS):
                    continue
            key = os.path.normcase(os.path.abspath(p))
            if key in seen:
                continue
            seen.add(key)
            found.append(p)

    # 示意章优先，避免本地既有真实章时被默认选中
    found.sort(key=lambda p: (0 if "示意" in os.path.basename(p) else 1,
                              os.path.basename(p)))
    if os.path.isfile(BUILTIN_SEAL):
        key = os.path.normcase(os.path.abspath(BUILTIN_SEAL))
        if key not in seen:
            found.insert(0, BUILTIN_SEAL)      # exe 内置示意章排第一
    return found


def default_seal():
    seals = list_seals()
    if not seals:
        raise RuntimeError("seals 目录里没有章 PDF。请把整幅透明章图层放进：{}".format(SEALS_DIR))
    return seals[0]


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in cfg:
                if k in data:
                    cfg[k] = data[k]
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def range_text(pages):
    """[1,2,3,5] -> '1-3, 5'；空 -> '无'。"""
    if not pages:
        return "无"
    nums = sorted({int(p) for p in pages})
    out, start, prev = [], nums[0], nums[0]
    for p in nums[1:]:
        if p == prev + 1:
            prev = p
            continue
        out.append(str(start) if start == prev else "{}-{}".format(start, prev))
        start = prev = p
    out.append(str(start) if start == prev else "{}-{}".format(start, prev))
    return ", ".join(out)


def write_report(rows, dirs, seal="", pages_spec="", dx=0.0, dy=0.0):
    """把本批盖章结果写成 txt 报告，返回路径；不用时不会调用。"""
    if not rows or not dirs:
        return None
    try:
        base = dirs[0]
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "盖章报告_{}.txt".format(
            time.strftime("%Y%m%d_%H%M%S")))
        lines = [
            "PDF 盖章报告",
            "生成时间：{}".format(time.strftime("%Y-%m-%d %H:%M:%S")),
            "章图层：{}".format(os.path.basename(seal) if seal else "-"),
            "页码策略：{}".format(pages_spec or "-"),
            "章位微调：dx={:g}  dy={:g}".format(float(dx), float(dy)),
            "",
        ]
        for idx, r in enumerate(rows, 1):
            lines.append("[{}] {}".format(idx, os.path.basename(r["input"])))
            lines.append("    输出：{}".format(r["output"]))
            lines.append("    页数：{}    盖章页：{}    跳过页：{}".format(
                r["pages"], range_text(r.get("stamped", [])),
                range_text(r.get("skipped", []))))
            lines.append("    大小：{:,} 字节    耗时：{:.2f} 秒".format(
                r.get("size", 0), r.get("elapsed", 0.0)))
            lines.append("")
        ok = len([r for r in rows if r.get("status", "OK") == "OK"])
        lines.append("汇总：成功 {} 个，失败 {} 个".format(ok, len(rows) - ok))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except Exception:
        return None


_PDF_NUM = r"[-+]?\d*\.?\d+(?:e[-+]?\d+)?"
_PDF_TOK = re.compile(
    r"(?P<num>" + _PDF_NUM + r")"
    r"|(?P<name>/[^\s/\[\]<>(){}%]+)"
    r"|(?P<op>[A-Za-z'\"*][A-Za-z0-9'\"*]*)"
    r"|(?P<arr>[\[\]])"
)


def _mat_mul(m, n):
    """3x2 仿射矩阵相乘：先应用 m，再应用 n。"""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D,
            a * E + b * F + e, c * E + d * F + f]


def _point(m, x, y):
    """按 PDF 约定变换一个点：X = a*x + c*y + e，Y = b*x + d*y + f。"""
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def seal_ink_bbox(seal_page, depth=0):
    """章图层里"章实际画在哪"。返回 (x0, y0, x1, y1)，解析不出返回 None。

    整幅透明层通常远大于章本身：示意章只占右下角一小块，用图层边缘对齐会
    把小图幅图纸的章推到纸外。这里顺着内容流的 CTM 递归算出所有 XObject 的
    真实落位，拿到章的墨迹外框。图像天然占 1x1 单位方框（由 cm 缩放），
    Form 用自身 BBox 并沿用它的 Matrix。
    """
    x0 = y0 = 1e9
    x1 = y1 = -1e9

    def walk(content, xobjs, parent, level):
        nonlocal x0, y0, x1, y1
        stack = []
        ctm = list(parent)
        nums = []
        for m in _PDF_TOK.finditer(content):
            if m.group("num"):
                nums.append(float(m.group("num")))
            elif m.group("name"):
                nums.append(m.group("name"))
            elif m.group("arr"):
                continue
            else:
                op = m.group("op")
                if op == "q":
                    stack.append(list(ctm))
                elif op == "Q":
                    if stack:
                        ctm = stack.pop()
                elif op == "cm":
                    v = [x for x in nums if isinstance(x, float)]
                    if len(v) >= 6:
                        ctm = _mat_mul(v[-6:], ctm)
                elif op == "Do":
                    nm = [x for x in nums if isinstance(x, str)]
                    if nm and nm[-1] in xobjs:
                        obj = deref(xobjs[nm[-1]])
                        is_form = str(deref(obj.get("/Subtype"))) == "/Form"
                        box = [0, 0, 1, 1]
                        fmat = [1, 0, 0, 1, 0, 0]
                        if is_form:
                            bb = deref(obj.get("/BBox"))
                            if bb and len(bb) == 4:
                                box = [float(v) for v in bb]
                            fm = deref(obj.get("/Matrix"))
                            if fm and len(fm) >= 6:
                                fmat = [float(v) for v in fm][:6]
                        # Form 的 BBox 是自身坐标系，先过它的 Matrix 再进 CTM
                        eff = _mat_mul(fmat, ctm) if is_form else ctm
                        for px, py in ((box[0], box[1]), (box[2], box[3])):
                            X, Y = _point(eff, px, py)
                            x0, y0 = min(x0, X), min(y0, Y)
                            x1, y1 = max(x1, X), max(y1, Y)
                        # Form 里面还有内容：递进它自己的资源树
                        inner = deref(obj.get("/Contents")) if is_form else None
                        if inner is not None and level < 6 and hasattr(inner, "get_data"):
                            sub = deref(obj.get("/Resources"))
                            walk(inner.get_data().decode("latin-1", "replace"),
                                 (deref(sub.get("/XObject")) if sub else None) or {},
                                 eff, level + 1)
                nums = []

    res = deref(seal_page.get("/Resources"))
    content_obj = deref(seal_page.get("/Contents"))
    if content_obj is None:
        return None
    # /Contents 可能是单个流，也可能是流数组，统一取成一段文本
    try:
        if hasattr(content_obj, "get_data"):
            text = content_obj.get_data().decode("latin-1", "replace")
        elif isinstance(content_obj, (list, tuple)):
            text = "\n".join(deref(x).get_data().decode("latin-1", "replace")
                             for x in content_obj)
        else:
            return None
    except Exception:
        return None
    try:
        walk(text,
             (deref(res.get("/XObject")) if res else None) or {},
             [1, 0, 0, 1, 0, 0], depth)
    except Exception:
        return None
    if x0 == 1e9:
        return None
    return (x0, y0, x1, y1)


def seal_markers(seal_page):
    """章图层 XObject 的结构指纹（图像宽高 / Form BBox），用于识别"这页已经盖过这枚章"。"""
    res = deref(seal_page.get("/Resources"))
    xobjs = deref(res.get("/XObject")) if res else None
    marks = []
    for name in (xobjs or {}):
        obj = deref(xobjs[name])
        sub = str(deref(obj.get("/Subtype")))
        if sub == "/Image":
            marks.append((sub, int(obj.get("/Width", 0)), int(obj.get("/Height", 0))))
        elif sub == "/Form":
            bb = deref(obj.get("/BBox"))
            marks.append((sub, tuple(round(float(v), 1) for v in bb) if bb else ()))
        else:
            marks.append((sub,))
    return marks


def page_has_any_marker(page, markers):
    """页面资源里是否已存在这些章指纹之一。"""
    if not markers:
        return False
    res = deref(page.get("/Resources"))
    xobjs = deref(res.get("/XObject")) if res else None
    if not xobjs:
        return False
    have = []
    for name in xobjs:
        obj = deref(xobjs[name])
        sub = str(deref(obj.get("/Subtype")))
        if sub == "/Image":
            have.append((sub, int(obj.get("/Width", 0)), int(obj.get("/Height", 0))))
        elif sub == "/Form":
            bb = deref(obj.get("/BBox"))
            have.append((sub, tuple(round(float(v), 1) for v in bb) if bb else ()))
        else:
            have.append((sub,))
    return any(m in have for m in markers)


def xobj_names(page):
    """页面 /Resources 里的 XObject 名字集合（章可能是 /Image，也可能是 /Form）。"""
    res = deref(page.get("/Resources"))
    xobjs = deref(res.get("/XObject")) if res else None
    return set(str(k) for k in xobjs.keys()) if xobjs else set()


def page_content(page):
    """取页面内容流原始字节（/Contents 可能是单个流或流数组）。"""
    c = deref(page.get("/Contents"))
    if c is None:
        return b""
    if hasattr(c, "get_data"):
        return c.get_data()
    if isinstance(c, (list, tuple)):
        return b"\n".join(deref(x).get_data() for x in c)
    return b""


def page_has_seal(page, names):
    """该页内容流是否真的引用了章图层新增的 XObject。"""
    if not names:
        return True                        # 纯内容流章，无法按名字校验
    content = page_content(page)
    return any(n.encode("latin-1", "replace") in content for n in names)


def recompress(page):
    """pypdf 重写页面时会丢掉内容流的 Flate 压缩，重新压回去。
    不这么做的话 6 MB 图纸会变成 26~32 MB。"""
    try:
        page.compress_content_streams()
    except Exception:
        pass


def validate_pages(spec):
    """只校验页码格式，不构造页列表（避免大页数验证时越界或吃内存）。"""
    if spec in (PAGE_ALL, PAGE_FIRST):
        return
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if int(a) < 1 or int(b) < int(a):
                raise ValueError("页码范围不对：" + part)
        elif int(part) < 1:
            raise ValueError("页码不对：" + part)


def enable_file_drop(root, on_paths):
    """Windows：把顶层窗口注册为文件拖放目标（WM_DROPFILES），零第三方依赖。
    拖进来的路径通过 on_paths(list) 回调；失败时返回 None，界面其它功能不受影响。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u, s = ctypes.windll.user32, ctypes.windll.shell32
        HWND = wintypes.HWND
        u.GetAncestor.argtypes = [HWND, ctypes.c_uint]
        u.GetAncestor.restype = HWND
        u.GetWindowLongPtrW.argtypes = [HWND, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_void_p
        u.SetWindowLongPtrW.argtypes = [HWND, ctypes.c_int, ctypes.c_void_p]
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, HWND, ctypes.c_uint,
                                      wintypes.WPARAM, wintypes.LPARAM]
        u.CallWindowProcW.restype = ctypes.c_ssize_t
        s.DragAcceptFiles.argtypes = [HWND, wintypes.BOOL]
        s.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                     wintypes.LPWSTR, ctypes.c_uint]
        s.DragQueryFileW.restype = ctypes.c_uint
        s.DragFinish.argtypes = [ctypes.c_void_p]

        hwnd = u.GetAncestor(root.winfo_id(), 2)          # GA_ROOT 顶层窗口
        if not hwnd:
            return None
        s.DragAcceptFiles(hwnd, True)
        WM_DROPFILES, GWL_WNDPROC = 0x0233, -4
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, HWND, ctypes.c_uint,
                                     wintypes.WPARAM, wintypes.LPARAM)
        old = u.GetWindowLongPtrW(hwnd, GWL_WNDPROC)
        # 关键：窗口过程运行在 Windows 消息循环里，这里绝不能调用 Tk/Tcl
        # （after_idle 等会重入解释器直接崩溃）。只入队，由 Tk 侧的 poll 消费。
        pending = []

        def proc(h, msg, wp, lp):
            if msg == WM_DROPFILES:
                try:
                    hdrop = ctypes.c_void_p(wp)
                    paths = []
                    count = s.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                    for i in range(count):
                        buf = ctypes.create_unicode_buffer(2048)
                        s.DragQueryFileW(hdrop, i, buf, 2048)
                        if buf.value:
                            paths.append(buf.value)
                    s.DragFinish(hdrop)
                    pending.extend(paths)          # 纯 Python 层，不触碰 Tcl
                except Exception:
                    pass
                return 0
            try:
                return u.CallWindowProcW(ctypes.c_void_p(old), h, msg, wp, lp)
            except Exception:
                return 0

        cb = WNDPROC(proc)
        u.SetWindowLongPtrW(hwnd, GWL_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))
        return {"hwnd": hwnd, "old": old, "callback": cb,
                "dispatch": proc, "pending": pending}
    except Exception:
        return None


def restore_file_drop(dnd):
    """关闭窗口前还原原窗口过程，避免回调对象被回收后崩溃。"""
    if not dnd:
        return
    try:
        import ctypes
        ctypes.windll.user32.SetWindowLongPtrW(
            dnd["hwnd"], -4, ctypes.c_void_p(dnd["old"]))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 从已签章的图纸里提取电子章
# --------------------------------------------------------------------------- #
def find_signatures(src):
    """找出 PDF 里的电子签章（/FT /Sig），返回可提取的章列表。

    每一项：{"page": 页序号(1 基), "name": 签名字段名, "time": 签署时间,
             "rect": [x0,y0,x1,y1], "size": (宽,高)}
    只做识别，不动任何字节。
    """
    with open(src, "rb") as f:
        doc = PdfReader(io.BytesIO(f.read()))
    out = []
    for pno, page in enumerate(doc.pages, 1):
        for annot in (deref(page.get("/Annots")) or []):
            a = deref(annot)
            if str(deref(a.get("/FT"))) != "/Sig":
                continue
            rect = deref(a.get("/Rect"))
            if not rect or len(rect) != 4:
                continue
            r = [float(v) for v in rect]
            w, h = r[2] - r[0], r[3] - r[1]
            if w <= 2 or h <= 2:                 # 占位的空签名域，没有章面
                continue
            if not deref(a.get("/AP")):           # 没有外观，无从提取
                continue
            sig = deref(a.get("/V"))
            when = ""
            if sig is not None:
                when = str(deref(sig.get("/M")) or "")
            out.append({
                "page": pno,
                "name": str(deref(a.get("/T")) or ""),
                "time": when,
                "rect": r,
                "size": (w, h),
            })
    return out


def _clone_into(writer, obj, memo):
    """把 obj（含它引用的整棵树）深拷贝进 writer，返回可写入的引用。

    pypdf 的 _add_object 只挂顶层对象，嵌套的 /Resources 之类仍指向原文档，
    写出来就是空壳；这里手动递归，同一对象只拷一次（memo 去重）。
    """
    obj = deref(obj)
    if obj is None:
        return None
    key = id(obj)
    if key in memo:
        return memo[key]
    # 流对象（含 Form XObject、图像）必须先处理：它是字典的子类，
    # 当字典重建会把 /Contents 的字节丢掉，章就成了空壳。
    if hasattr(obj, "get_data") and isinstance(obj, DictionaryObject):
        out = DecodedStreamObject()
        ref = writer._add_object(out)
        memo[key] = ref
        raw = getattr(obj, "_data", None)
        keep_encoded = (raw is not None
                        and isinstance(obj, EncodedStreamObject)
                        and obj.get("/Filter") is not None)
        for k, v in obj.items():
            if str(k) in ("/Length", "/P", "/Parent"):
                continue
            if keep_encoded and str(k) in ("/Filter", "/DecodeParms"):
                continue                       # 下面按原编码整体搬回
            out[NameObject(str(k))] = _clone_into(writer, v, memo)
        if keep_encoded:
            # 原流是压缩过的：连 /Filter 带字节原样搬，既不重编码也不膨胀
            out_filter = obj.get("/Filter")
            out[NameObject("/Filter")] = _clone_into(writer, out_filter, memo)
            dp = obj.get("/DecodeParms")
            if dp is not None:
                out[NameObject("/DecodeParms")] = _clone_into(writer, dp, memo)
            out._data = raw
            out[NameObject("/Length")] = NumberObject(len(raw))
        else:
            try:
                out.set_data(obj.get_data())
            except Exception:
                out.set_data(b"")
        return ref
    if isinstance(obj, DictionaryObject):
        out = DictionaryObject()
        ref = writer._add_object(out)
        memo[key] = ref
        for k, v in obj.items():
            if k in ("/P", "/Parent", "/Annots", "/StructParent"):
                continue                       # 指回页面/文档的引用不能带过来
            out[NameObject(str(k))] = _clone_into(writer, v, memo)
        return ref
    if isinstance(obj, (list, tuple)):
        # 数组一律内联：/Mask、/ColorSpace 这类若写成间接引用，
        # 部分阅读器（含 pdfium）会当成异常结构，整张图渲染成色块。
        arr = ArrayObject()
        for v in obj:
            arr.append(_clone_into(writer, v, memo))
        return arr
    if isinstance(obj, (int, float, str, bytes)):
        return obj
    # 其余（NumberObject / NameObject / TextStringObject / BooleanObject…）
    try:
        return obj.clone(writer)
    except Exception:
        return obj


def extract_seal(src, rect, layer_w=None, layer_h=None, page_no=1,
                 log=print):
    """把一个电子签章的外观原样提成整幅透明章图层，返回 PDF 字节。

    关键点：不渲染、不解码、不重编码。把签名的 /AP /N 外观对象连同它引用的
    整棵资源树搬进新页面，按原 Rect 落位。原文件的色键遮罩（/Mask）因此
    完整保留，章的边缘过渡像素一个不丢。
    """
    with open(src, "rb") as f:
        doc = PdfReader(io.BytesIO(f.read()))
    page = doc.pages[page_no - 1]
    target = None
    for annot in (deref(page.get("/Annots")) or []):
        a = deref(annot)
        if str(deref(a.get("/FT"))) != "/Sig":
            continue
        r = [float(v) for v in (deref(a.get("/Rect")) or [0, 0, 0, 0])]
        if all(abs(r[i] - rect[i]) < 0.5 for i in range(4)):
            target = a
            break
    if target is None:
        raise RuntimeError("没找到对应的签章注释")

    ap = deref(target.get("/AP"))
    apn = deref(ap.get("/N")) if ap else None
    if apn is None:
        raise RuntimeError("该签章没有外观对象，无法提取")

    x0, y0, x1, y1 = [float(v) for v in target["/Rect"]]
    w, h = x1 - x0, y1 - y0
    pw, ph = float(page.mediabox.width), float(page.mediabox.height)
    lw = float(layer_w or max(pw, 1689.12))    # 图层尺寸默认跟原页一致
    lh = float(layer_h or max(ph, 1195.92))
    log("    章 {:.0f}x{:.0f}pt，原页 {:.0f}x{:.0f} → 图层 {:.0f}x{:.0f}".format(
        w, h, pw, ph, lw, lh))

    writer = PdfWriter()
    new_page = writer.add_blank_page(width=lw, height=lh)
    ref = _clone_into(writer, apn, {})         # 原样搬运整棵外观资源树
    new_page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/S1"): ref})
    })
    # 外观对象本身就是 w×h 尺寸的画布（Form 的 BBox 就是它），
    # 落到页面上只需平移，不能再用 cm 缩放，否则章会被放大成色块。
    tx = lw - w
    ty = 0.0
    cs = DecodedStreamObject()
    cs.set_data(("q 1 0 0 1 %.4f %.4f cm /S1 Do Q" % (tx, ty)).encode("latin-1"))
    new_page[NameObject("/Contents")] = writer._add_object(cs)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _is_issue_seal(item):
    """出图章(约 204x99) / 注册章(约 170x85)：按尺寸判断，排除审图章(约 207x82)。
    宽高比接近 2:1 的是出图章，接近 2:1 但更小的是注册章；这里简单地用
    "不是审图章那种扁长条"来筛，宁可多提要用户自己删，也不漏掉该要的。"""
    w, h = item["size"]
    ratio = w / h if h else 0
    return 1.85 <= ratio <= 2.30 and not (90 <= h <= 95 and ratio > 2.4)


def seal_suggest_name(src, rect, index=1):
    """给提取出来的章起个能分清的默认名（从原文件名 + 尺寸推断）。"""
    stem = os.path.splitext(os.path.basename(src))[0]
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    tag = "章%d" % index
    if abs(h - 99) < 8 and abs(w - 204) < 10:
        tag = "出图章"
    elif abs(h - 85) < 8 and abs(w - 170) < 10:
        tag = "注册章"
    return "%s-%s-%.0fx%.0f" % (tag, stem, w, h)


def extract_seals_to(src, items, out_dir=None, names=None, log=print):
    """批量提取，返回 [(路径, 字节大小)]。默认写进 seals\\。"""
    out_dir = out_dir or SEALS_DIR
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for i, it in enumerate(items):
        data = extract_seal(src, it["rect"], page_no=it["page"], log=log)
        nm = (names or {}).get(i) or seal_suggest_name(src, it["rect"], i + 1)
        nm = re.sub(r'[\\/:*?"<>|]', "_", nm)
        if not nm.lower().endswith(".pdf"):
            nm += ".pdf"
        path = os.path.join(out_dir, nm)
        k = 2
        while os.path.exists(path):
            path = os.path.join(out_dir, "{}_{}{}".format(
                os.path.splitext(nm)[0], k, ".pdf"))
            k += 1
        with open(path, "wb") as f:
            f.write(data)
        log("    已保存 {}".format(os.path.basename(path)))
        saved.append((path, os.path.getsize(path)))
    return saved


def render_preview(src, seal_bytes, dx=0.0, dy=0.0, max_width=900):
    """生成首页叠章预览 PNG。需要 pypdfium2，缺失时返回 None（其它功能不受影响）。"""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return None
    td = tempfile.mkdtemp(prefix="stamp_preview_")
    try:
        tmp_pdf, _, _, _ = stamp_file(
            src, seal_bytes, PAGE_FIRST, "custom", td, log=lambda m: None,
            dx=dx, dy=dy, skip_stamped=False)
        doc = pdfium.PdfDocument(tmp_pdf)
        page = doc[0]
        width = float(page.get_size()[0]) or 1.0
        scale = max(0.15, min(2.0, float(max_width) / width))
        img = page.render(scale=scale).to_pil()
        out = os.path.join(tempfile.gettempdir(), "stamp_preview.png")
        img.save(out)
        doc.close()
        return out
    except Exception:
        return None
    finally:
        shutil.rmtree(td, ignore_errors=True)


def check_update(current_version, timeout=6):
    """查询 GitHub 最新正式版，有新版本返回版本号，否则/失败返回 None。"""
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.github.com/repos/eriiforever/pdf-signing-tool/releases/latest",
            headers={"User-Agent": "pdf-seal-stamper/" + current_version})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = str(data.get("tag_name", "")).strip().lstrip("vV")
        if not tag:
            return None

        def key(v):
            nums = [int(x) for x in re.findall(r"\d+", v)[:3]]
            return tuple(nums + [0] * (3 - len(nums)))
        return tag if key(tag) > key(current_version) else None
    except Exception:
        return None


def expand_files(items):
    """拖拽 / 命令行参数展开成 PDF 列表，支持目录。"""
    out = []
    for item in items:
        if os.path.isdir(item):
            out.extend(sorted(glob.glob(os.path.join(item, "*.pdf"))))
        elif item.lower().endswith(".pdf") and os.path.isfile(item):
            out.append(os.path.abspath(item))
    return [p for i, p in enumerate(out) if p not in out[:i]]


# --------------------------------------------------------------------------- #
# 盖章核心
# --------------------------------------------------------------------------- #
def output_path(src, out_mode, custom_dir, exists_mode="overwrite"):
    """算输出路径。exists_mode: overwrite=直接覆盖同名 / rename=自动加后缀。
    返回 (dst, dst_dir)。out_mode=overwrite 时 dst 就是源文件本身。"""
    name = os.path.basename(src)
    src_dir = os.path.dirname(os.path.abspath(src))
    if out_mode == "overwrite":
        return src, src_dir
    if out_mode == "stamped":
        dst_dir = os.path.join(src_dir, STAMPED_DIRNAME)
    elif out_mode == "custom":
        if not custom_dir:
            raise ValueError("自定义输出模式需要指定目录")
        dst_dir = custom_dir
    else:
        raise ValueError("未知输出模式：" + out_mode)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, name)
    if exists_mode == "rename" and os.path.exists(dst):
        stem, ext = os.path.splitext(name)
        k = 2
        while True:
            cand = os.path.join(dst_dir, "{}_已签章{}{}".format(stem, k, ext))
            if not os.path.exists(cand):
                dst = cand
                break
            k += 1
    return dst, dst_dir


def stamp_file(src, seal_bytes, pages_spec, out_mode, custom_dir, log=print,
              exists_mode="overwrite", dx=0.0, dy=0.0, skip_stamped=False,
              markers=None):
    """盖一个 PDF，返回 (输出路径, 页数, 逐页放置记录, 被跳过的已盖章页)。"""
    name = os.path.basename(src)
    with open(src, "rb") as f:
        data = f.read()                    # 先读进内存，避免 Windows 文件占用
    doc = PdfReader(io.BytesIO(data))
    if doc.is_encrypted:
        raise RuntimeError("PDF 已加密，跳过：" + name)

    seal_doc = PdfReader(io.BytesIO(seal_bytes))
    seal_page = seal_doc.pages[0]          # 全篇共用一个章对象，避免每页复制一份章图像
    sw, sh = float(seal_page.mediabox.width), float(seal_page.mediabox.height)
    sx0, sy0 = float(seal_page.mediabox.left), float(seal_page.mediabox.bottom)
    log("    章图层 {:.2f} x {:.2f} pt | 共 {} 页".format(sw, sh, len(doc.pages)))
    # 章在整幅图层里的实际位置。图层通常远大于章，按图层边缘对齐会让章
    # 飘到页面中部（图幅比图层大时）或纸外（图幅比图层小时），一律以墨迹为准。
    ink = seal_ink_bbox(seal_page) or (sx0, sy0, sx0 + sw, sy0 + sh)
    ix0, iy0, ix1, iy1 = ink
    log("    章墨迹 {:.1f} x {:.1f} pt @ ({:.1f},{:.1f})".format(
        ix1 - ix0, iy1 - iy0, ix0, iy0))

    idxs = set(parse_pages(pages_spec, len(doc.pages)))
    if markers is None:
        markers = seal_markers(seal_page)
    writer = PdfWriter()
    placements = []
    added_by_page = {}
    skipped = []
    for i, page in enumerate(doc.pages):
        if i not in idxs:
            writer.add_page(page)
            recompress(writer.pages[-1])
            continue
        if skip_stamped and page_has_any_marker(page, markers):
            writer.add_page(page)
            recompress(writer.pages[-1])
            skipped.append(i + 1)
            log("    p{:<3d} 已有该章，跳过".format(i + 1))
            continue
        pw, ph = float(page.mediabox.width), float(page.mediabox.height)
        pl, pb = float(page.mediabox.left), float(page.mediabox.bottom)
        rot = int(deref(page.get("/Rotate")) or 0)
        # 有 /Rotate 的页先把旋转烘进内容流，否则章会跟着页面躺倒
        if rot % 360:
            try:
                page.transfer_rotation_to_content()
                rot = 0
                pw, ph = float(page.mediabox.width), float(page.mediabox.height)
                pl, pb = float(page.mediabox.left), float(page.mediabox.bottom)
            except Exception:
                pass
        # 整幅图层的右下角对齐页面右下角；不缩放、不裁剪；dx/dy 为微调。
        # 纵向必须按"纸底"算：旧写法 (ph - sh) 实际是右上角对齐，图幅比
        # 图层高时（A1/A0）章会落在页面中部，不是右下角。
        tx = (pl + pw) - (sx0 + sw) + float(dx)
        ty = pb - sy0 + float(dy)
        # 安全网：章的墨迹整个落在纸外时，按墨迹拉回右下角（正常章层不会触发）
        if (ix1 + tx < pl or ix0 + tx > pl + pw
                or iy1 + ty < pb or iy0 + ty > pb + ph):
            log("    p{:<3d} 章落在纸外，按墨迹拉回右下角".format(i + 1))
            tx = (pl + pw) - ix1 + float(dx)
            ty = pb - iy0 + float(dy)
        before = xobj_names(page)
        page.merge_transformed_page(
            seal_page, Transformation().translate(tx, ty),
            over=True, expand=False,
        )
        added_by_page[i] = xobj_names(page) - before
        writer.add_page(page)
        recompress(writer.pages[-1])
        placements.append((i + 1, pw, ph, tx, ty, rot))
        log("    p{:<3d} {:>5.0f}x{:<5.0f} 平移({:.1f},{:.1f}){}".format(
            i + 1, pw, ph, tx, ty, " [页面旋转{}°]".format(rot) if rot else ""))

    dst, dst_dir = output_path(src, out_mode, custom_dir, exists_mode)
    if out_mode == "overwrite":
        bdir = os.path.join(os.path.dirname(os.path.abspath(src)), BACKUP_DIRNAME)
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, name)
        if not os.path.exists(backup):
            shutil.copy2(src, backup)      # 已存在不覆盖，返工的安全底线
    if os.path.basename(dst) != name:
        log("    同名已存在，改用：" + os.path.basename(dst))
    elif os.path.exists(dst) and dst != src:
        log("    同名已存在，直接覆盖：" + os.path.basename(dst))

    tmp = dst + ".tmp"
    try:
        writer.write(tmp)
        os.replace(tmp, dst)              # 原子落盘，不留半截文件
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    # 逐页校验：页数一致 + 每个盖章页的内容流确实引用了章图层对象
    with open(dst, "rb") as f:
        check = PdfReader(io.BytesIO(f.read()))
    if len(check.pages) != len(doc.pages):
        raise RuntimeError("校验失败：页数不一致 {}".format(name))
    effective = [i for i in sorted(idxs) if (i + 1) not in skipped]
    bad = [i + 1 for i in effective
           if not page_has_seal(check.pages[i], added_by_page.get(i, set()))]
    if bad:
        raise RuntimeError("校验失败：第 {} 页未见章图层 {}".format(bad, name))
    unnamed = [i + 1 for i in effective if not added_by_page.get(i)]
    if skipped:
        log("    跳过 {} 页已盖章：{}".format(len(skipped), skipped))
    if unnamed:
        log("    校验通过：{} 页；第 {} 页为纯内容流章，仅完成页数校验".format(
            len(check.pages), unnamed))
    else:
        log("    校验通过：{} 页，{} 个盖章页均检出章图层对象".format(
            len(check.pages), len(effective)))
    return dst, len(check.pages), placements, skipped


def batch(files, seal_path, pages_spec, out_mode, custom_dir, log=print,
          exists_mode="overwrite", dx=0.0, dy=0.0, skip_stamped=False,
          report=False):
    with open(seal_path, "rb") as f:
        seal_bytes = f.read()
    try:
        markers = seal_markers(PdfReader(io.BytesIO(seal_bytes)).pages[0])
    except Exception:
        markers = None
    ok, fail = 0, []
    rows, out_dirs = [], []
    for k, src in enumerate(files, 1):
        t0 = time.time()
        log("[{}/{}] {}".format(k, len(files), os.path.basename(src)))
        try:
            dst, n, _, skipped = stamp_file(
                src, seal_bytes, pages_spec, out_mode, custom_dir,
                log, exists_mode=exists_mode, dx=dx, dy=dy,
                skip_stamped=skip_stamped, markers=markers)
            out_dirs.append(os.path.dirname(dst))
            idxs = parse_pages(pages_spec, n)
            rows.append({"input": os.path.abspath(src),
                         "output": os.path.abspath(dst), "pages": n,
                         "stamped": [i + 1 for i in idxs if (i + 1) not in skipped],
                         "skipped": skipped, "size": os.path.getsize(dst),
                         "elapsed": time.time() - t0})
            log("    -> {}  ({} 页{})".format(
                dst, n, "，跳过 {} 页".format(len(skipped)) if skipped else ""))
            ok += 1
        except Exception as e:
            fail.append((src, str(e)))
            log("    失败：{}".format(e))
    if report and rows:
        path = write_report(rows, out_dirs, seal_path, pages_spec, dx, dy)
        if path:
            log("盖章报告：" + path)
    log("完成：成功 {} 个，失败 {} 个".format(ok, len(fail)))
    for src, err in fail:
        log("  x {} : {}".format(os.path.basename(src), err))
    return ok, fail


# --------------------------------------------------------------------------- #
# 命令行 / 拖拽模式（不需要 tkinter，任何 Python 都能跑）
# --------------------------------------------------------------------------- #
def run_cli(argv, _log=None):
    ap = argparse.ArgumentParser(
        description="PDF 盖章工具 v{}（拖拽 / 命令行）".format(VERSION))
    ap.add_argument("--version", action="version",
                     version="PDF 盖章工具 v{}".format(VERSION))
    ap.add_argument("files", nargs="*", help="要盖章的 PDF，或装 PDF 的文件夹")
    ap.add_argument("--seal", help="章图层 PDF，默认取 seals/ 里第一个")
    ap.add_argument("--pages", default=PAGE_ALL,
                     help="all(每页) / first(仅首页) / 1,3,5 / 2-4，默认 all")
    ap.add_argument("--out", default="stamped",
                     choices=["stamped", "overwrite", "custom"],
                     help="stamped=源文件目录\\已签章(默认) / overwrite=覆盖原件(自动备份) / custom=--dir")
    ap.add_argument("--dir", help="--out custom 时的输出目录")
    ap.add_argument("--exists", default="overwrite", choices=["overwrite", "rename"],
                     help="同名文件：overwrite=直接覆盖(默认) / rename=自动加后缀")
    ap.add_argument("--dx", type=float, default=0.0, help="章位水平微调（pt，正=右）")
    ap.add_argument("--dy", type=float, default=0.0, help="章位垂直微调（pt，正=上）")
    ap.add_argument("--skip-stamped", action=argparse.BooleanOptionalAction,
                     default=False, help="跳过已盖过这枚章的页（默认关）")
    ap.add_argument("--report", action=argparse.BooleanOptionalAction,
                     default=False, help="在输出目录生成 txt 盖章报告（默认关）")
    ap.add_argument("--extract", metavar="已签章PDF",
                     help="从这份已签章的图纸里提取电子章，存进 seals\\（不盖章）")
    ap.add_argument("--extract-all", action="store_true",
                     help="配合 --extract：连审图章等全部提取（默认只提出图章/注册章）")
    args = ap.parse_args(argv)

    if args.extract:
        try:
            log = _log or print
            items = find_signatures(args.extract)
            if not items:
                log("这份 PDF 里没找到电子签章。")
                return 1
            log("在 {} 里找到 {} 个电子章：".format(
                os.path.basename(args.extract), len(items)))
            for i, it in enumerate(items, 1):
                log("  {}) 页{} {}  {:.0f}x{:.0f}pt".format(
                    i, it["page"], it["name"], it["size"][0], it["size"][1]))
            if not args.extract_all:
                picked = [it for it in items if _is_issue_seal(it)]
                if picked:
                    items = picked
                    log("  只提出图章/注册章（要全提加 --extract-all）")
            saved = extract_seals_to(args.extract, items, log=log)
            log("完成：{} 个，存放在 {}".format(len(saved), SEALS_DIR))
            for p, sz in saved:
                log("  {}  ({:.0f} KB)".format(os.path.basename(p), sz / 1024.0))
            return 0
        except Exception:
            traceback.print_exc()
            return 1

    files = expand_files(args.files)
    if not files:
        print("没有可盖章的 PDF。双击 stamp.bat 打开窗口，或把 PDF 拖到 stamp.bat 上。")
        return 1
    try:
        seal = args.seal or default_seal()
        if not os.path.isfile(seal):
            raise RuntimeError("章 PDF 不存在：" + seal)
        log = _log or print
        log("章：{}".format(seal))
        log("输出：{} | 页码：{} | 同名：{} | 微调：({}, {}) | 跳过已盖：{} | 报告：{}".format(
            args.out, args.pages, args.exists, args.dx, args.dy,
            args.skip_stamped, args.report))
        _, fail = batch(files, seal, args.pages, args.out, args.dir, log,
                        exists_mode=args.exists, dx=args.dx, dy=args.dy,
                        skip_stamped=args.skip_stamped, report=args.report)
        return 1 if fail else 0
    except Exception:
        traceback.print_exc()
        return 1


# --------------------------------------------------------------------------- #
# 窗口模式（tkinter 标准库）
# --------------------------------------------------------------------------- #
def run_gui(preset_files=None, selftest=False):
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        msg = "当前 Python 没有 tkinter，打不开窗口。\n请使用打包好的 PDF盖章工具.exe，或把 PDF 拖到 stamp.bat 上。"
        try:
            print(msg)
        except Exception:
            pass
        try:                       # pythonw 没有控制台，仍弹一个系统提示
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "PDF 盖章工具", 0x10)
        except Exception:
            pass
        return 1

    class App:
        def __init__(self, root):
            import tkinter as tk
            from tkinter import ttk, messagebox
            self.mb = messagebox
            self.root = root
            self.files = []
            self.busy = False
            self.cfg = load_config()
            self.q = queue.Queue()
            root.title("PDF 盖章工具  v{}".format(VERSION))
            vw, vh = root.winfo_screenwidth(), root.winfo_screenheight()
            w = max(700, min(920, vw - 80))
            # 内容所需高度约 790px；窗口及最小尺寸都不低于它，日志区才不会被挤掉
            h = max(800, min(880, vh - 80))
            root.geometry("{}x{}+{}+{}".format(w, h,
                          max(20, (vw - w) // 2), max(20, (vh - h) // 3)))
            root.minsize(700, 800)
            self.win_h = h
            root.protocol("WM_DELETE_WINDOW", self.on_close)
            try:                                # 窗口左上角也用印章图标
                for cand in (os.path.join(BUNDLE_DIR, "stamp.ico"),
                             os.path.join(APP_DIR, "stamp.ico")):
                    if os.path.isfile(cand):
                        root.iconbitmap(cand)
                        break
            except Exception:
                pass

            f = ttk.Frame(root, padding=12)
            f.pack(fill="both", expand=True)

            head = ttk.Frame(f)
            head.pack(fill="x", pady=(0, 10))
            ttk.Label(head, text="PDF 盖章工具",
                      font=("Microsoft YaHei", 15, "bold")).pack(side="left")
            ttk.Label(head, text="v{}".format(VERSION),
                      font=("Microsoft YaHei", 10, "bold"),
                      foreground="#A32D2D").pack(side="left", padx=(6, 0))
            ttk.Label(head, text="整幅图层 1:1 · 右下角对齐 · 逐页定位",
                      foreground="#888").pack(side="left", padx=12)

            # 1. 章
            g1 = ttk.LabelFrame(f, text=" 1  章图层（整幅透明 PDF，1:1 叠加） ", padding=8)
            g1.pack(fill="x", pady=(0, 8))
            row_seal = ttk.Frame(g1)
            row_seal.pack(fill="x")
            self.seal_var = tk.StringVar()
            self.seal_box = ttk.Combobox(row_seal, textvariable=self.seal_var, state="readonly")
            self.seal_box.pack(side="left", fill="x", expand=True, padx=(0, 8))
            ttk.Button(row_seal, text="浏览…", command=self.choose_seal).pack(side="right")
            ttk.Button(row_seal, text="刷新", command=lambda: self.refresh_seals(verbose=True),
                       width=6).pack(side="right", padx=6)
            self.seal_box.bind("<Button-1>", lambda e: self.refresh_seals())   # 点开下拉前重扫
            self.seal_box.bind("<<ComboboxSelected>>", self.on_seal_changed)   # 换章时套用它的页码策略
            row_ext = ttk.Frame(g1)
            row_ext.pack(fill="x", pady=(6, 0))
            ttk.Button(row_ext, text="从已签章图纸提取章…", command=self.extract_seal_dialog,
                       width=22).pack(side="left")
            ttk.Label(row_ext, text="选一份盖过章的图纸，把你单位自己的电子章提到 seals\\",
                      foreground="#888").pack(side="left", padx=8)
            ttk.Label(g1, text="自动扫描 seals\\、程序目录及图纸所在目录中带「章」字的 PDF；请确认选的是整幅透明图层",
                      foreground="#888").pack(anchor="w", pady=(4, 0))

            # 2. 文件
            g2 = ttk.LabelFrame(f, text=" 2  待盖章图纸（可直接把 PDF 或文件夹拖进窗口） ", padding=8)
            g2.pack(fill="both", expand=True, pady=(0, 8))
            bar = ttk.Frame(g2)
            bar.pack(fill="x", pady=(0, 6))
            ttk.Button(bar, text="添加 PDF…", command=self.add_files).pack(side="left")
            ttk.Button(bar, text="移除选中", command=self.remove_selected).pack(side="left", padx=6)
            ttk.Button(bar, text="清空", command=self.clear_files).pack(side="left")
            self.lb = tk.Listbox(g2, selectmode="extended", height=5)
            self.lb.pack(side="left", fill="both", expand=True)
            sb = ttk.Scrollbar(g2, orient="vertical", command=self.lb.yview)
            sb.pack(side="right", fill="y")
            self.lb.configure(yscrollcommand=sb.set)

            # 3. 页码
            g3 = ttk.LabelFrame(f, text=" 3  页码 ", padding=8)
            g3.pack(fill="x", pady=(0, 8))
            self.pages_var = tk.StringVar(value=PAGE_ALL)
            ttk.Radiobutton(g3, text="每页都盖（出图章）", variable=self.pages_var,
                           value=PAGE_ALL, command=self.sync_pages).pack(side="left")
            ttk.Radiobutton(g3, text="仅首页（公章）", variable=self.pages_var,
                           value=PAGE_FIRST, command=self.sync_pages).pack(side="left", padx=14)
            ttk.Radiobutton(g3, text="自定义", variable=self.pages_var,
                           value="custom", command=self.sync_pages).pack(side="left")
            self.pages_entry = ttk.Entry(g3, width=16, state="disabled")
            self.pages_entry.pack(side="left", padx=6)
            ttk.Label(g3, text="如 1,3,5 或 2-4", foreground="#777").pack(side="left")

            # 4. 输出
            g4 = ttk.LabelFrame(f, text=" 4  输出 ", padding=8)
            g4.pack(fill="x", pady=(0, 8))
            self.out_var = tk.StringVar(value=self.cfg.get("out", "stamped"))
            ttk.Radiobutton(g4, text="源文件目录\\已签章（原件不动，推荐）",
                           variable=self.out_var, value="stamped",
                           command=self.sync_out).pack(anchor="w")
            ttk.Radiobutton(g4, text="覆盖原文件（先备份到 _未盖章原件）",
                           variable=self.out_var, value="overwrite",
                           command=self.sync_out).pack(anchor="w")
            row = ttk.Frame(g4)
            row.pack(fill="x", pady=(2, 0))
            ttk.Radiobutton(row, text="自定义目录", variable=self.out_var,
                            value="custom", command=self.sync_out).pack(side="left")
            self.dir_var = tk.StringVar()
            self.dir_entry = ttk.Entry(row, textvariable=self.dir_var, state="disabled")
            self.dir_entry.pack(side="left", fill="x", expand=True, padx=6)
            self.dir_btn = ttk.Button(row, text="选目录", command=self.choose_dir, state="disabled")
            self.dir_btn.pack(side="right")
            ex = ttk.Frame(g4)
            ex.pack(fill="x", pady=(6, 0))
            ttk.Label(ex, text="同名文件：").pack(side="left")
            self.exists_var = tk.StringVar(value=self.cfg.get("exists", "overwrite"))
            ttk.Radiobutton(ex, text="直接覆盖（默认）", variable=self.exists_var,
                           value="overwrite").pack(side="left")
            ttk.Radiobutton(ex, text="提醒我", variable=self.exists_var,
                           value="ask").pack(side="left", padx=10)
            ttk.Radiobutton(ex, text="自动加后缀", variable=self.exists_var,
                           value="rename").pack(side="left")
            adv = ttk.Frame(g4)
            adv.pack(fill="x", pady=(6, 0))
            ttk.Label(adv, text="章位微调：dx").pack(side="left")
            self.dx_var = tk.StringVar(value=str(self.cfg.get("dx", 0)))
            ttk.Spinbox(adv, from_=-100, to=100, increment=0.5, width=6,
                        textvariable=self.dx_var).pack(side="left", padx=(2, 8))
            ttk.Label(adv, text="dy").pack(side="left")
            self.dy_var = tk.StringVar(value=str(self.cfg.get("dy", 0)))
            ttk.Spinbox(adv, from_=-100, to=100, increment=0.5, width=6,
                        textvariable=self.dy_var).pack(side="left", padx=(2, 8))
            ttk.Label(adv, text="pt（正=右 / 上）", foreground="#888").pack(side="left")
            self.skip_var = tk.BooleanVar(value=bool(self.cfg.get("skip_stamped", False)))
            self.report_var = tk.BooleanVar(value=bool(self.cfg.get("report", False)))
            chk = ttk.Frame(adv)
            chk.pack(side="right")
            ttk.Checkbutton(chk, text="生成盖章报告", variable=self.report_var).pack(side="left")
            ttk.Checkbutton(chk, text="跳过已盖章页", variable=self.skip_var).pack(
                side="left", padx=(10, 0))

            # 5. 执行
            g5 = ttk.Frame(f)
            g5.pack(fill="x", pady=(2, 6))
            self.go = ttk.Button(g5, text="开始盖章", command=self.start, width=14)
            self.go.pack(side="left")
            self.preview_btn = ttk.Button(g5, text="预览首页", command=self.preview, width=10)
            self.preview_btn.pack(side="left", padx=(6, 0))
            self.pb = ttk.Progressbar(g5, mode="determinate", maximum=100)
            self.pb.pack(side="left", fill="x", expand=True, padx=10)
            ttk.Button(g5, text="另存日志", command=self.save_log, width=10).pack(side="right")

            # 6. 运行日志：弹性面板 + 双向滚动条，默认窗口下至少显示 6 行
            g6 = ttk.LabelFrame(f, text=" 运行日志 ", padding=(6, 4))
            g6.pack(fill="both", expand=True)
            self.txt = tk.Text(g6, height=6, wrap="none", font=("Consolas", 9))
            vsb = ttk.Scrollbar(g6, orient="vertical", command=self.txt.yview)
            hsb = ttk.Scrollbar(g6, orient="horizontal", command=self.txt.xview)
            self.txt.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            self.txt.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            hsb.grid(row=1, column=0, sticky="ew")
            g6.rowconfigure(0, weight=1)
            g6.columnconfigure(0, weight=1)
            ttk.Label(f, text="关闭窗口即结束程序；把 PDF 拖到程序图标可自动带入文件列表。",
                      foreground="#888").pack(anchor="w", pady=(4, 0))

            self.log("PDF 盖章工具 v{}  ·  就绪".format(VERSION))
            self.log("换章：把整幅透明章 PDF 放进 seals\\ 或与图纸同目录（文件名带「章」），或点「浏览」选择。")
            self.refresh_seals(verbose=True)   # 控件齐备后再填充下拉、写日志
            self.sync_out()
            self.sync_pages()
            root.update_idletasks()
            self._dnd = enable_file_drop(root, self.on_drop_files)
            self.root.after(100, self.poll)
            if self.cfg.get("check_update", True):
                threading.Thread(target=self._check_update, daemon=True).start()

        # ---- 小工具 ----
        def log(self, msg):
            self.txt.insert("end", str(msg) + "\n")
            self.txt.see("end")

        def _num(self, var, default=0.0):
            try:
                return float(str(var.get()).strip() or default)
            except Exception:
                return default

        def _dx(self):
            return self._num(getattr(self, "dx_var", None), 0.0)

        def _dy(self):
            return self._num(getattr(self, "dy_var", None), 0.0)

        def on_seal_changed(self, event=None):
            """换章时套用这枚章记住的页码策略。"""
            seal = self.seal_map.get(self.seal_var.get(), "")
            pages = (self.cfg.get("seal_pages") or {}).get(seal)
            if not pages:
                return
            if pages in (PAGE_ALL, PAGE_FIRST):
                self.pages_var.set(pages)
                self.pages_entry.configure(state="disabled")
            else:
                self.pages_var.set("custom")
                self.pages_entry.configure(state="normal")
                self.pages_entry.delete(0, "end")
                self.pages_entry.insert(0, pages)

        def preview(self):
            """渲染首页叠章效果，先看再盖。需要 pypdfium2。"""
            if not self.files:
                self.mb.showwarning("提示", "请先添加要盖章的 PDF")
                return
            seal = self.seal_map.get(self.seal_var.get(), self.seal_var.get())
            if not seal or not os.path.isfile(seal):
                self.mb.showwarning("提示", "请先选择章图层")
                return
            try:
                with open(seal, "rb") as f:
                    sb = f.read()
                self.log("生成预览：{}".format(os.path.basename(self.files[0])))
                png = render_preview(self.files[0], sb, self._dx(), self._dy())
                if not png:
                    self.mb.showinfo(
                        "无法预览",
                        "预览需要 pypdfium2（python -m pip install pypdfium2）。\n"
                        "不装也不影响盖章、校验等其它功能。")
                    return
                top = tk.Toplevel(self.root)
                top.title("预览 · " + os.path.basename(self.files[0]))
                img = tk.PhotoImage(file=png)
                lbl = ttk.Label(top, image=img)
                lbl.image = img            # 保持引用，否则图片会被回收
                lbl.pack(padx=8, pady=8)
                ttk.Label(top, text="仅预览第 1 页；实际按页码设置逐页盖章",
                          foreground="#888").pack(pady=(0, 8))
            except Exception as e:
                self.mb.showerror("预览失败", str(e))

        def save_log(self):
            from tkinter import filedialog
            p = filedialog.asksaveasfilename(
                title="保存运行日志", defaultextension=".txt",
                filetypes=[("文本文件", "*.txt")], initialfile="stamp_log.txt")
            if not p:
                return
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(self.txt.get("1.0", "end"))
                self.log("日志已保存：" + p)
            except Exception as e:
                self.mb.showerror("保存失败", str(e))

        def _check_update(self):
            try:
                new = check_update(VERSION)
                if new:
                    self.q.put(("update", new))
            except Exception:
                pass

        def refresh_seals(self, verbose=False):
            """重扫章候选：seals\\ + 程序目录/当前目录 + 已添加图纸所在目录（带「章」字）。
            只有候选真的变化才重写下拉并记日志，避免点开下拉时闪动、日志刷屏。"""
            current = self.seal_var.get()
            prev_map = getattr(self, "seal_map", {})
            chosen = prev_map.get(current, current)
            dirs = [os.path.dirname(p) for p in self.files]
            paths = [p for p in list_seals(dirs) if p not in self.files]
            self.seal_map = {}
            for p in paths:
                label = os.path.basename(p)
                if p == BUILTIN_SEAL:
                    label = "内置示例章（" + label + "）"
                elif label in self.seal_map:
                    label = os.path.basename(os.path.dirname(p)) + "\\" + label
                self.seal_map.setdefault(label, p)
            # 「浏览」手工选过的章刷新时不要冲掉
            if chosen and os.path.isfile(chosen) and chosen not in self.seal_map.values():
                self.seal_map.setdefault(os.path.basename(chosen), chosen)
            labels = list(self.seal_map.keys())
            changed = labels != getattr(self, "_last_seal_labels", None)
            if changed:
                self._last_seal_labels = labels
                self.seal_box["values"] = labels
            if current not in labels and labels:
                want = self.cfg.get("seal")
                match = next((lb for lb, p in self.seal_map.items() if p == want), None)
                self.seal_var.set(match or labels[0])
            if changed or verbose:
                if not labels:
                    self.log("提示：把章 PDF 放进 seals\\ 或与图纸同目录（文件名带「章」），也可点「浏览」。")
                else:
                    self.log("章候选 {} 个：已扫描 seals\\、程序目录与图纸所在目录。".format(len(labels)))

        def choose_seal(self):
            from tkinter import filedialog
            p = filedialog.askopenfilename(title="选择整幅透明章图层 PDF",
                                           filetypes=[("PDF", "*.pdf")])
            if p:
                label = os.path.basename(p)
                self.seal_map.setdefault(label, p)
                self.seal_box["values"] = list(self.seal_map.keys())
                self.seal_var.set(label)

        def extract_seal_dialog(self):
            """选一份盖过章的图纸，把里面的电子章原样提到 seals\\。"""
            from tkinter import filedialog
            p = filedialog.askopenfilename(
                title="选一份盖过电子章的图纸（从中提取章）",
                filetypes=[("PDF", "*.pdf")])
            if not p:
                return
            try:
                items = find_signatures(p)
            except Exception as e:
                self.mb.showerror("读取失败", str(e))
                return
            if not items:
                self.mb.showinfo("没找到章",
                                 "这份 PDF 里没有电子签章。\n\n"
                                 "只有用电子签章工具盖上去的章才能提取；\n"
                                 "扫描件或图片贴上去的章没有电子签名，提不出来。")
                return
            if not self.confirm_extract(p, items):
                return
            try:
                saved = extract_seals_to(p, items, log=self.log)
            except Exception as e:
                self.mb.showerror("提取失败", str(e))
                return
            self.refresh_seals()
            labels = [os.path.basename(sp) for sp, _ in saved]
            for lab in labels:
                self.seal_map.setdefault(lab, os.path.join(SEALS_DIR, lab))
            self.seal_box["values"] = list(self.seal_map.keys())
            if labels:
                self.seal_var.set(labels[0])
                self.on_seal_changed()
            self.mb.showinfo(
                "提取完成",
                "已提取 {} 个章到：\n{}\n\n{}".format(
                    len(saved), SEALS_DIR, "\n".join(labels)))

        def confirm_extract(self, src, items):
            """列出识别到的章，让用户勾选要提哪些（默认只勾出图章/注册章）。"""
            import tkinter as tk
            from tkinter import ttk as ttk2
            top = tk.Toplevel(self.root)
            top.title("选择要提取的章")
            top.transient(self.root)
            top.grab_set()
            top.resizable(False, False)
            fr = ttk2.Frame(top, padding=12)
            fr.pack(fill="both", expand=True)
            ttk2.Label(fr, text="在 {} 中找到 {} 个电子章：".format(
                os.path.basename(src), len(items))).pack(anchor="w")
            vars_ = []
            for i, it in enumerate(items):
                v = tk.BooleanVar(value=_is_issue_seal(it))
                vars_.append(v)
                ttk2.Checkbutton(
                    fr, variable=v,
                    text="第 {} 页 · {} · {:.0f}×{:.0f} pt".format(
                        it["page"], it["name"], it["size"][0], it["size"][1])
                ).pack(anchor="w", pady=2)
            ttk2.Label(fr, text="审图章一般不用提取，可自行取消勾选。",
                       foreground="#888").pack(anchor="w", pady=(6, 0))
            out = {"ok": False}
            bar = ttk2.Frame(fr)
            bar.pack(fill="x", pady=(10, 0))

            def ok():
                out["ok"] = True
                top.destroy()
            ttk2.Button(bar, text="开始提取", command=ok).pack(side="right")
            ttk2.Button(bar, text="取消", command=top.destroy).pack(side="right", padx=6)
            self.root.wait_window(top)
            if not out["ok"]:
                return False
            picked = [it for it, v in zip(items, vars_) if v.get()]
            if not picked:
                self.mb.showinfo("未选择", "没有勾选任何章。")
                return False
            items[:] = picked
            return True

        def on_drop_files(self, paths):
            """窗口内拖放：路径可以是 PDF，也可以是装 PDF 的文件夹。"""
            got = []
            for p in paths:
                if os.path.isdir(p):
                    got.extend(sorted(glob.glob(os.path.join(p, "*.pdf"))))
                elif p.lower().endswith(".pdf") and os.path.isfile(p):
                    got.append(os.path.abspath(p))
            self.add_paths(got)
            if got:
                self.log("拖入 {} 个 PDF".format(len(got)))
            elif paths:
                self.log("拖入的内容里没有可用的 PDF")

        def add_paths(self, paths):
            for p in paths:
                if p not in self.files:
                    self.files.append(p)
                    self.lb.insert("end", p)
            self.refresh_seals(verbose=True)   # 加图纸后自动重扫它所在目录的章

        def add_files(self):
            from tkinter import filedialog
            ps = filedialog.askopenfilenames(title="选择要盖章的 PDF（可多选）",
                                             filetypes=[("PDF", "*.pdf")])
            self.add_paths(ps)
            self.log("已选 {} 个文件".format(len(self.files)))

        def remove_selected(self):
            for i in reversed(self.lb.curselection()):
                self.lb.delete(i)
                del self.files[i]

        def clear_files(self):
            self.lb.delete(0, "end")
            self.files.clear()
            self.log("已清空")

        def sync_pages(self):
            self.pages_entry.configure(state="normal" if self.pages_var.get() == "custom" else "disabled")

        def sync_out(self):
            on = self.out_var.get() == "custom"
            self.dir_entry.configure(state="normal" if on else "disabled")
            self.dir_btn.configure(state="normal" if on else "disabled")

        def choose_dir(self):
            from tkinter import filedialog
            p = filedialog.askdirectory(title="选择输出目录")
            if p:
                self.dir_var.set(p)

        # ---- 执行 ----
        def spec(self):
            v = self.pages_var.get()
            if v == "custom":
                return self.pages_entry.get().strip() or PAGE_ALL
            return v

        def start(self):
            if self.busy:
                return
            if not self.files:
                self.mb.showwarning("提示", "请先添加要盖章的 PDF")
                return
            seal = self.seal_map.get(self.seal_var.get(), self.seal_var.get())
            if not seal or not os.path.isfile(seal):
                self.mb.showwarning("提示", "请把章 PDF 放进程序旁的 seals/ 目录，或点「浏览」选择")
                return
            try:
                pages_spec = self.spec()
                validate_pages(pages_spec)            # 只校验格式，不误判合法页码
            except Exception as e:
                self.mb.showerror("页码格式不对", str(e))
                return
            if self.out_var.get() == "custom" and not self.dir_var.get().strip():
                self.mb.showwarning("提示", "请选择自定义输出目录")
                return

            # 同名策略：选「提醒我」时，开始前一次性问清全部冲突，避免盖章线程里弹窗
            exists_mode = self.exists_var.get()
            if exists_mode == "ask" and self.out_var.get() != "overwrite":
                dups = []
                for src in self.files:
                    try:
                        dst, _ = output_path(src, self.out_var.get(),
                                            self.dir_var.get().strip(), "overwrite")
                        if os.path.exists(dst):
                            dups.append(dst)
                    except Exception:
                        pass
                if dups:
                    preview = "\n".join("  " + os.path.basename(p) for p in dups[:5])
                    if len(dups) > 5:
                        preview += "\n  … 等 {} 个".format(len(dups))
                    ans = self.mb.askyesnocancel(
                        "发现同名文件",
                        "输出目录里已有 {} 个同名文件：\n{}\n\n"
                        "是 = 全部覆盖   否 = 自动加后缀   取消 = 暂不盖章".format(
                            len(dups), preview))
                    if ans is None:
                        self.log("已取消，未开始盖章。")
                        return
                    exists_mode = "overwrite" if ans else "rename"
                    self.log("同名处理：{}".format("直接覆盖" if ans else "自动加后缀"))

            dx, dy = self._dx(), self._dy()
            skip_stamped = bool(self.skip_var.get())
            report = bool(self.report_var.get())
            # 记住这次的选择，下次打开直接沿用
            self.cfg.update({
                "seal": seal, "pages": pages_spec,
                "out": self.out_var.get(), "exists": exists_mode,
                "dx": dx, "dy": dy, "skip_stamped": skip_stamped, "report": report,
            })
            self.cfg.setdefault("seal_pages", {})[seal] = pages_spec
            save_config(self.cfg)

            self.busy = True
            self.go.configure(state="disabled")
            self.pb["value"] = 0
            self.log("=" * 60)
            if dx or dy:
                self.log("章位微调：dx={:g} dy={:g} pt".format(dx, dy))
            args = (list(self.files), seal, pages_spec,
                    self.out_var.get(), self.dir_var.get().strip(), exists_mode,
                    dx, dy, skip_stamped, report)
            threading.Thread(target=self.worker, args=args, daemon=True).start()

        def worker(self, files, seal, pages_spec, out_mode, custom_dir, exists_mode,
                   dx, dy, skip_stamped, report):
            def qlog(m):
                self.q.put(("log", m))
            try:
                with open(seal, "rb") as f:
                    seal_bytes = f.read()
                try:
                    markers = seal_markers(PdfReader(io.BytesIO(seal_bytes)).pages[0])
                except Exception:
                    markers = None
                total = len(files)
                ok = 0
                out_dirs = []
                rows = []
                for k, src in enumerate(files, 1):
                    t0 = time.time()
                    self.q.put(("log", "[{}/{}] {}".format(k, total, os.path.basename(src))))
                    dst, n, _, skipped = stamp_file(
                        src, seal_bytes, pages_spec, out_mode, custom_dir, qlog,
                        exists_mode=exists_mode, dx=dx, dy=dy,
                        skip_stamped=skip_stamped, markers=markers)
                    out_dirs.append(os.path.dirname(dst))
                    idxs = parse_pages(pages_spec, n)
                    rows.append({
                        "input": os.path.abspath(src),
                        "output": os.path.abspath(dst), "pages": n,
                        "stamped": [i + 1 for i in idxs if (i + 1) not in skipped],
                        "skipped": skipped, "size": os.path.getsize(dst),
                        "elapsed": time.time() - t0})
                    self.q.put(("log", "    -> {}  ({} 页{})".format(
                        dst, n, "，跳过 {} 页".format(len(skipped)) if skipped else "")))
                    ok += 1
                    self.q.put(("progress", k * 100.0 / total))
                report_path = write_report(rows, out_dirs, seal, pages_spec, dx, dy) \
                    if report else None
                self.q.put(("done", (ok, out_dirs, rows, report_path)))
            except Exception:
                self.q.put(("error", traceback.format_exc()))

        def poll(self):
            # 先消费窗口拖放进来的路径（在 Tk 主循环里处理，窗口过程只负责入队）
            dnd = getattr(self, "_dnd", None)
            if dnd and dnd["pending"]:
                paths = dnd["pending"][:]
                dnd["pending"].clear()
                try:
                    self.on_drop_files(paths)
                except Exception as e:
                    try:
                        self.log("拖入文件处理失败：{}".format(e))
                    except Exception:
                        pass
            try:
                while True:
                    kind, payload = self.q.get_nowait()
                    if kind == "log":
                        self.log(payload)
                    elif kind == "progress":
                        self.pb["value"] = payload
                    elif kind == "done":
                        ok_count, dirs, rows, report_path = payload
                        self.busy = False
                        self.go.configure(state="normal")
                        self.log("完成，成功 {} 个。".format(ok_count))
                        if report_path:
                            self.log("盖章报告：" + report_path)
                        unique = []
                        for d in dirs:
                            if d and os.path.isdir(d) and d not in unique:
                                unique.append(d)
                        if unique and self.mb.askyesno(
                                "完成", "盖章完成，成功 {} 个文件。\n\n是否打开输出文件夹？".format(ok_count)):
                            for d in unique:
                                try:
                                    os.startfile(d)       # Windows 资源管理器
                                except Exception as e:
                                    self.log("打开文件夹失败：{}".format(e))
                            self.log("已打开输出文件夹。")
                    elif kind == "update":
                        self.log("发现新版本 v{}，可在 GitHub Releases 下载".format(payload))
                    elif kind == "error":
                        self.busy = False
                        self.go.configure(state="normal")
                        self.log(payload)
                        self.mb.showerror("出错", payload.splitlines()[-1])
            except queue.Empty:
                pass
            except Exception as e:
                # 任何意外都不得让轮询停摆，也不能把窗口锁死在 busy 状态
                self.busy = False
                try:
                    self.go.configure(state="normal")
                except Exception:
                    pass
                try:
                    self.log("界面异常已自动恢复：{}".format(e))
                except Exception:
                    pass
            finally:
                try:
                    self.root.after(100, self.poll)
                except Exception:
                    pass

        def on_close(self):
            if self.busy:
                # 给一个强制出口，避免异常情况下永远关不掉窗口
                if self.mb.askyesno(
                        "正在盖章",
                        "当前批次尚未确认结束。\n\n若日志显示已全部盖章完成，可强制退出。\n\n确定强制退出吗？"):
                    restore_file_drop(getattr(self, "_dnd", None))
                    self.root.destroy()
                return
            restore_file_drop(getattr(self, "_dnd", None))
            self.root.destroy()

    root = tk.Tk()
    if selftest:
        root.withdraw()                        # 自检不弹窗，方便验证打包后的 exe
    app = App(root)
    if selftest:
        root.update_idletasks()
        # req <= win 说明所有控件（含日志区）在默认窗口里放得下，不会被裁掉
        status = ["VER v{}".format(VERSION), "GUI OK", "LAYOUT req=%d win=%d log=%d" % (
            root.winfo_reqheight(), app.win_h, app.txt.winfo_height())]
        status.append("TITLE=" + root.title())
        # 完成事件回归：模拟 worker 发出 done，验证 busy 与按钮被正确复位、窗口可关闭
        app.busy = True
        app.go.configure(state="disabled")
        app.q.put(("done", (2, [], [], None)))
        app.poll()
        status.append("DONE OK busy=%s go=%s" % (app.busy, str(app.go["state"])))
        # 窗口内拖放回归：构造真实 HDROP 投递 WM_DROPFILES，验证路径进入文件列表
        drop_path = next((a for a in sys.argv[1:]
                          if a.lower().endswith(".pdf") and os.path.isfile(a)), None)
        dnd = getattr(app, "_dnd", None)
        if dnd and drop_path:
            try:
                import ctypes

                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

                class DROPFILES(ctypes.Structure):
                    _fields_ = [("pFiles", ctypes.c_uint), ("pt", POINT),
                                ("fNC", ctypes.c_int), ("fWide", ctypes.c_int)]

                text = os.path.abspath(drop_path) + "\0\0"
                raw = text.encode("utf-16-le")
                size = ctypes.sizeof(DROPFILES) + len(raw)
                k = ctypes.windll.kernel32
                k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
                k.GlobalAlloc.restype = ctypes.c_void_p
                k.GlobalLock.argtypes = [ctypes.c_void_p]
                k.GlobalLock.restype = ctypes.c_void_p
                k.GlobalUnlock.argtypes = [ctypes.c_void_p]
                hg = k.GlobalAlloc(0x0042, size)
                ptr = k.GlobalLock(hg)
                df = DROPFILES.from_address(ptr)
                df.pFiles = ctypes.sizeof(DROPFILES)
                df.fWide = 1
                ctypes.memmove(ptr + ctypes.sizeof(DROPFILES), raw, len(raw))
                k.GlobalUnlock(hg)
                # 用 PostMessage 走真实消息循环，比直接调用回调更接近实际拖放
                pm = ctypes.windll.user32.PostMessageW
                pm.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                               ctypes.c_size_t, ctypes.c_ssize_t]
                pm(dnd["hwnd"], 0x0233, hg, 0)
                root.update()
                app.poll()                     # 由 Tk 侧消费 pending 队列
                status.append("DROP OK files=%d" % len(app.files))
            except Exception as e:
                status.append("DROP FAIL %s" % e)
        else:
            status.append("DROP FAIL not-registered")
        try:                                  # 真实跑一遍盖章链路，验证 exe 内依赖完整
            import tempfile
            seals = list_seals()
            if seals:
                td = tempfile.mkdtemp(prefix="stamp_self_")
                blank = os.path.join(td, "_blank.pdf")
                w = PdfWriter()
                w.add_blank_page(width=1695, height=1202)
                w.write(blank)
                with open(seals[0], "rb") as fh:
                    sb = fh.read()
                dst, n, _, _ = stamp_file(blank, sb, PAGE_ALL, "custom", td,
                                          log=lambda m: None)
                status.append("STAMP OK pages=%d size=%d" % (n, os.path.getsize(dst)))
                # 同名策略：覆盖应回同一路径；改名应换成带后缀的文件
                again, _, _, _ = stamp_file(blank, sb, PAGE_ALL, "custom", td,
                                            log=lambda m: None, exists_mode="overwrite")
                renamed, _, _, _ = stamp_file(blank, sb, PAGE_ALL, "custom", td,
                                              log=lambda m: None, exists_mode="rename")
                status.append("EXISTS OK same=%s renamed=%s" % (
                    os.path.normcase(again) == os.path.normcase(dst),
                    os.path.basename(renamed)))
                # 已盖章检测：对刚盖好的文件再跑一次，应全部跳过、不叠第二层
                _, _, _, skip_list = stamp_file(
                    dst, sb, PAGE_ALL, "custom", td, log=lambda m: None,
                    skip_stamped=True)
                status.append("SKIP OK skipped=%d" % len(skip_list))
                # 章位微调：dx=10 应让平移量正好大 10 pt
                sw0 = float(PdfReader(io.BytesIO(sb)).pages[0].mediabox.width)
                _, _, pl, _ = stamp_file(blank, sb, PAGE_ALL, "custom", td,
                                         log=lambda m: None, dx=10.0)
                status.append("DX OK shift=%.1f" % (pl[0][3] - (pl[0][1] - sw0)))
                status.append("PREVIEW %s" % ("OK" if render_preview(blank, sb) else "SKIP"))
                status.append("CFG OK keys=%d" % len(load_config()))
                rep = write_report(
                    [{"input": blank, "output": dst, "pages": n,
                      "stamped": [1], "skipped": [], "size": 1, "elapsed": 0.1}],
                    [td], "seal.pdf", "all", 0, 0)
                status.append("REPORT %s" % (
                    "OK" if rep and rep.endswith(".txt") else "FAIL"))
                # 若自检参数里带了真实 PDF，再跑一遍端到端盖章
                sample = next((a for a in sys.argv[1:]
                               if a.lower().endswith(".pdf") and os.path.isfile(a)), None)
                if sample:
                    app.add_paths([sample])       # 模拟把图纸加进窗口，触发自动重扫
                    labels = list(app.seal_box["values"])
                    status.append("SCAN OK count=%d values=%s" %
                                  (len(labels), "|".join(labels)))
                    td2 = tempfile.mkdtemp(prefix="stamp_self_")
                    real, rn, real_pl, _ = stamp_file(sample, sb, PAGE_ALL, "custom", td2,
                                                      log=lambda m: None)
                    status.append("REAL OK pages=%d size=%d out=%s" %
                                  (rn, os.path.getsize(real), real))
            else:
                status.append("NO SEAL")
        except Exception:
            status.append("STAMP FAIL\n" + traceback.format_exc())
        root.update_idletasks()
        root.destroy()
        text = "; ".join(status)
        try:
            print(text)
        except Exception:                      # windowed exe 没有控制台
            pass
        try:
            import tempfile
            with open(os.path.join(tempfile.gettempdir(), "stamp_gui_selftest.ok"),
                      "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception:
            pass
        return 0 if "STAMP OK" in text else 1
    if preset_files:
        app.add_paths(preset_files)
        app.log("已带入 {} 个文件，点「开始盖章」即可。".format(len(preset_files)))
    root.mainloop()
    return 0


def _cli_capture(argv):
    """windowed exe 没有控制台：跑完命令行后把输出弹窗显示，别让用户看不到结果。"""
    buf = []

    def log(*a):
        buf.append(" ".join(str(x) for x in a))

    code = run_cli(argv, _log=log)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, "\n".join(buf[-40:]) or "（无输出）", "PDF 盖章工具 v" + VERSION, 0x40)
    except Exception:
        pass
    return code


def main():
    argv = sys.argv[1:]
    if "--version" in argv:
        try:
            print("PDF 盖章工具 v{}".format(VERSION))
        except Exception:                      # windowed exe 没有控制台
            pass
        return 0
    if "--selftest" in argv or os.environ.get("STAMP_GUI_SELFTEST"):
        return run_gui(selftest=True)
    flags = [a for a in argv if a.startswith("--")]
    if FROZEN:
        # 带命令行参数（如 --extract）时走命令行，结果用弹窗显示；
        # 否则双击开窗，把 PDF 拖到图标上则自动带入文件列表。
        if flags:
            return _cli_capture(argv)
        return run_gui(preset_files=expand_files([a for a in argv if os.path.isfile(a)]))
    if argv:
        return run_cli(argv)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
