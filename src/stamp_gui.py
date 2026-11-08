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
import glob
import io
import os
import queue
import shutil
import sys
import threading
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
PAGE_ALL, PAGE_FIRST = "all", "first"
VERSION = "2.2"                          # 同步维护 version_info.txt 中的文件版本


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
              exists_mode="overwrite"):
    """盖一个 PDF，返回 (输出路径, 页数, 逐页放置记录)。"""
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

    idxs = set(parse_pages(pages_spec, len(doc.pages)))
    writer = PdfWriter()
    placements = []
    added_by_page = {}
    for i, page in enumerate(doc.pages):
        if i not in idxs:
            writer.add_page(page)
            recompress(writer.pages[-1])
            continue
        pw, ph = float(page.mediabox.width), float(page.mediabox.height)
        # 整幅图层右下角对齐页面右下角；不缩放、不裁剪、不旋转
        tx = (pw - sw) + float(page.mediabox.left) - sx0
        ty = (ph - sh) + float(page.mediabox.bottom) - sy0
        rot = int(deref(page.get("/Rotate")) or 0)
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
    bad = [i + 1 for i in sorted(idxs)
           if not page_has_seal(check.pages[i], added_by_page.get(i, set()))]
    if bad:
        raise RuntimeError("校验失败：第 {} 页未见章图层 {}".format(bad, name))
    unnamed = [i + 1 for i in sorted(idxs) if not added_by_page.get(i)]
    if unnamed:
        log("    校验通过：{} 页；第 {} 页为纯内容流章，仅完成页数校验".format(
            len(check.pages), unnamed))
    else:
        log("    校验通过：{} 页，{} 个盖章页均检出章图层对象".format(
            len(check.pages), len(idxs)))
    return dst, len(check.pages), placements


def batch(files, seal_path, pages_spec, out_mode, custom_dir, log=print,
          exists_mode="overwrite"):
    with open(seal_path, "rb") as f:
        seal_bytes = f.read()
    ok, fail = 0, []
    for k, src in enumerate(files, 1):
        log("[{}/{}] {}".format(k, len(files), os.path.basename(src)))
        try:
            dst, n, _ = stamp_file(src, seal_bytes, pages_spec, out_mode, custom_dir,
                                  log, exists_mode=exists_mode)
            log("    -> {}  ({} 页)".format(dst, n))
            ok += 1
        except Exception as e:
            fail.append((src, str(e)))
            log("    失败：{}".format(e))
    log("完成：成功 {} 个，失败 {} 个".format(ok, len(fail)))
    for src, err in fail:
        log("  x {} : {}".format(os.path.basename(src), err))
    return ok, fail


# --------------------------------------------------------------------------- #
# 命令行 / 拖拽模式（不需要 tkinter，任何 Python 都能跑）
# --------------------------------------------------------------------------- #
def run_cli(argv):
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
    args = ap.parse_args(argv)

    files = expand_files(args.files)
    if not files:
        print("没有可盖章的 PDF。双击 stamp.bat 打开窗口，或把 PDF 拖到 stamp.bat 上。")
        return 1
    try:
        seal = args.seal or default_seal()
        if not os.path.isfile(seal):
            raise RuntimeError("章 PDF 不存在：" + seal)
        log = print
        log("章：{}".format(seal))
        log("输出：{} | 页码：{} | 同名：{}".format(args.out, args.pages, args.exists))
        _, fail = batch(files, seal, args.pages, args.out, args.dir, log,
                        exists_mode=args.exists)
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
            self.q = queue.Queue()
            root.title("PDF 盖章工具  v{}".format(VERSION))
            vw, vh = root.winfo_screenwidth(), root.winfo_screenheight()
            w = max(700, min(920, vw - 80))
            # 内容所需高度约 750px；窗口及最小尺寸都不低于它，日志区才不会被挤掉
            h = max(760, min(860, vh - 80))
            root.geometry("{}x{}+{}+{}".format(w, h,
                          max(20, (vw - w) // 2), max(20, (vh - h) // 3)))
            root.minsize(700, 760)
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
            ttk.Label(g1, text="自动扫描 seals\\、程序目录及图纸所在目录中带「章」字的 PDF；请确认选的是整幅透明图层",
                      foreground="#888").pack(anchor="w", pady=(6, 0))

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
            self.out_var = tk.StringVar(value="stamped")
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
            self.exists_var = tk.StringVar(value="overwrite")
            ttk.Radiobutton(ex, text="直接覆盖（默认）", variable=self.exists_var,
                           value="overwrite").pack(side="left")
            ttk.Radiobutton(ex, text="提醒我", variable=self.exists_var,
                           value="ask").pack(side="left", padx=10)
            ttk.Radiobutton(ex, text="自动加后缀", variable=self.exists_var,
                           value="rename").pack(side="left")

            # 5. 执行
            g5 = ttk.Frame(f)
            g5.pack(fill="x", pady=(2, 6))
            self.go = ttk.Button(g5, text="开始盖章", command=self.start, width=14)
            self.go.pack(side="left")
            self.pb = ttk.Progressbar(g5, mode="determinate", maximum=100)
            self.pb.pack(side="left", fill="x", expand=True, padx=10)

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
            root.update_idletasks()
            self._dnd = enable_file_drop(root, self.on_drop_files)
            self.root.after(100, self.poll)

        # ---- 小工具 ----
        def log(self, msg):
            self.txt.insert("end", str(msg) + "\n")
            self.txt.see("end")

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
                self.seal_var.set(labels[0])
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

            self.busy = True
            self.go.configure(state="disabled")
            self.pb["value"] = 0
            self.log("=" * 60)
            args = (list(self.files), seal, pages_spec,
                    self.out_var.get(), self.dir_var.get().strip(), exists_mode)
            threading.Thread(target=self.worker, args=args, daemon=True).start()

        def worker(self, files, seal, pages_spec, out_mode, custom_dir, exists_mode):
            def qlog(m):
                self.q.put(("log", m))
            try:
                with open(seal, "rb") as f:
                    seal_bytes = f.read()
                total = len(files)
                ok = 0
                out_dirs = []
                for k, src in enumerate(files, 1):
                    self.q.put(("log", "[{}/{}] {}".format(k, total, os.path.basename(src))))
                    dst, n, _ = stamp_file(src, seal_bytes, pages_spec,
                                           out_mode, custom_dir, qlog,
                                           exists_mode=exists_mode)
                    out_dirs.append(os.path.dirname(dst))
                    self.q.put(("log", "    -> {}  ({} 页)".format(dst, n)))
                    ok += 1
                    self.q.put(("progress", k * 100.0 / total))
                self.q.put(("done", (ok, out_dirs)))   # 统一 2 元组，poll 里按 kind, payload 解包
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
                        ok_count, dirs = payload
                        self.busy = False
                        self.go.configure(state="normal")
                        self.log("完成，成功 {} 个。".format(ok_count))
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
        app.q.put(("done", (2, [])))
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
                dst, n, _ = stamp_file(blank, sb, PAGE_ALL, "custom", td,
                                       log=lambda m: None)
                status.append("STAMP OK pages=%d size=%d" % (n, os.path.getsize(dst)))
                # 同名策略：覆盖应回同一路径；改名应换成带后缀的文件
                again, _, _ = stamp_file(blank, sb, PAGE_ALL, "custom", td,
                                        log=lambda m: None, exists_mode="overwrite")
                renamed, _, _ = stamp_file(blank, sb, PAGE_ALL, "custom", td,
                                           log=lambda m: None, exists_mode="rename")
                status.append("EXISTS OK same=%s renamed=%s" % (
                    os.path.normcase(again) == os.path.normcase(dst),
                    os.path.basename(renamed)))
                # 若自检参数里带了真实 PDF，再跑一遍端到端盖章
                sample = next((a for a in sys.argv[1:]
                               if a.lower().endswith(".pdf") and os.path.isfile(a)), None)
                if sample:
                    app.add_paths([sample])       # 模拟把图纸加进窗口，触发自动重扫
                    labels = list(app.seal_box["values"])
                    status.append("SCAN OK count=%d values=%s" %
                                  (len(labels), "|".join(labels)))
                    td2 = tempfile.mkdtemp(prefix="stamp_self_")
                    real, rn, _ = stamp_file(sample, sb, PAGE_ALL, "custom", td2,
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
    if FROZEN:
        # 单文件 exe：双击开空窗口；把 PDF 拖到图标上则自动带入文件列表
        return run_gui(preset_files=expand_files([a for a in argv if os.path.isfile(a)]))
    if argv:
        return run_cli(argv)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
