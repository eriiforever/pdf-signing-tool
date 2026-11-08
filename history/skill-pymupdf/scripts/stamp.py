# -*- coding: utf-8 -*-
"""
PDF 盖章工具：把「整幅图幅大小的透明章图层 PDF」1:1 叠加到目标 PDF 的页面上。

核心原则（务必遵守，见 SKILL.md）：
  1. 章源是一整幅透明图层（如 A2 1689.12x1195.92pt），章本身只在图层某一处。
     绝对不要裁剪、缩放或旋转图层 —— 整幅叠加，章的相对位置自然保留。
  2. 默认以图层右下角对齐页面右下角（bottom-right），逐页读取页面尺寸定位。
  3. 混合图幅（A2 / 加宽 / 加高）必须逐页取 page.rect，不写死坐标。

子命令：
  inspect  分析章图层与目标 PDF，只打印不写入
  apply    盖章（默认原地覆盖并自动备份原件）
  verify   逐页校验章是否存在、位置是否一致
  restore  从备份目录恢复未盖章原件

依赖：PyMuPDF >= 1.23   (python -m pip install PyMuPDF)
"""

import argparse
import glob
import json
import os
import shutil
import sys

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        sys.exit("缺少依赖 PyMuPDF。请先执行：python -m pip install PyMuPDF")

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BACKUP_DIRNAME = "_未盖章原件"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def expand_targets(targets, directory, pattern="*.pdf"):
    """展开 --target（可含通配符）与 --dir，返回去重排序后的绝对路径列表。"""
    found = []
    for t in targets or []:
        hits = glob.glob(t) if any(c in t for c in "*?[") else [t]
        for h in hits:
            if os.path.isfile(h) and h.lower().endswith(".pdf"):
                found.append(os.path.abspath(h))
    if directory:
        if not os.path.isdir(directory):
            sys.exit("目录不存在：" + directory)
        for h in glob.glob(os.path.join(directory, pattern)):
            if os.path.isfile(h) and BACKUP_DIRNAME not in os.path.dirname(h):
                found.append(os.path.abspath(h))
    files = sorted(set(found))
    if not files:
        sys.exit("没有找到目标 PDF。请用 --target 指定文件或 --dir 指定目录。")
    return files


def color_bbox(page, dpi=60, kind="red", step=3):
    """渲染页面，返回指定颜色像素的包围盒（页面坐标 pt）；无匹配返回 None。"""
    pm = page.get_pixmap(dpi=dpi, alpha=False)
    w, h, n = pm.width, pm.height, pm.n
    s, st = pm.samples, pm.stride
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(0, h, step):
        row = s[y * st: y * st + w * n]
        for x in range(0, w, step):
            i = x * n
            r, g, b = row[i], row[i + 1], row[i + 2]
            if kind == "red":
                hit = r > 130 and r - g > 55 and r - b > 55
            elif kind == "blue":
                hit = b > 110 and b - r > 40 and b - g > 20
            else:  # 任意明显彩色（红或蓝）
                hit = (r > 130 and r - g > 55 and r - b > 55) or (b > 110 and b - r > 40 and b - g > 20)
            if hit:
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
    if maxx < 0:
        return None
    sc = 72.0 / dpi
    # pixmap 原点对应页面左上，需加上 mediabox 原点偏移，统一成页面绝对坐标
    ox, oy = page.rect.x0, page.rect.y0
    return pymupdf.Rect(minx * sc + ox, miny * sc + oy,
                        (maxx + step) * sc + ox, (maxy + step) * sc + oy)


def detect_color(page, dpi=60, color="auto"):
    if color != "auto":
        return color_bbox(page, dpi, color), color
    for kind in ("red", "blue"):
        box = color_bbox(page, dpi, kind)
        if box:
            return box, kind
    return None, None


def region_ink_ratio(page, clip, dpi=80, kind="red", step=2):
    """只统计指定区域内的彩色像素占比，用于判断该章位是否已经有章。
    比整页检测可靠得多：工程图里的红色标注/文字不会落进章位小区域内。"""
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
            if kind == "blue":
                hit = b > 110 and b - r > 40 and b - g > 20
            elif kind == "auto":
                hit = (r > 130 and r - g > 55 and r - b > 55) or (b > 110 and b - r > 40 and b - g > 20)
            else:
                hit = r > 130 and r - g > 55 and r - b > 55
            if hit:
                hits += 1
    return hits / total if total else 0.0


def placement(page_rect, seal_w, seal_h, anchor, dx, dy):
    """计算整幅章图层在页面上的放置矩形（不缩放）。"""
    x0, y0 = page_rect.x0, page_rect.y0
    pw, ph = page_rect.width, page_rect.height
    if anchor == "bottom-right":
        ox, oy = pw - seal_w, ph - seal_h
    elif anchor == "top-left":
        ox, oy = 0.0, 0.0
    elif anchor == "top-right":
        ox, oy = pw - seal_w, 0.0
    elif anchor == "bottom-left":
        ox, oy = 0.0, ph - seal_h
    elif anchor == "center":
        ox, oy = (pw - seal_w) / 2.0, (ph - seal_h) / 2.0
    else:
        sys.exit("未知锚点：" + anchor)
    return pymupdf.Rect(x0 + ox + dx, y0 + oy + dy,
                        x0 + ox + seal_w + dx, y0 + oy + seal_h + dy)


def parse_pages(spec, n):
    """all / first / 1,3,5 / 2-4 / 0  → 页索引列表（0 基）。"""
    if spec == "all":
        return list(range(n))
    if spec in ("first", "first-page"):
        return [0]
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)   # 用户用 1 基页码
    return sorted({i for i in out if 0 <= i < n})


def open_doc(path):
    doc = pymupdf.open(path)
    if doc.needs_pass:
        doc.close()
        sys.exit("PDF 已加密，无法处理：" + path)
    return doc


def fmt(r):
    return "({:.0f},{:.0f})-({:.0f},{:.0f})".format(r.x0, r.y0, r.x1, r.y1)


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #
def cmd_inspect(args):
    seal = open_doc(args.seal)
    sp = seal[0]
    sw, sh = sp.rect.width, sp.rect.height
    sx0, sy0, sx1, sy1 = sp.rect.x0, sp.rect.y0, sp.rect.x1, sp.rect.y1
    print("章图层：{}".format(args.seal))
    print("  页数 {} | 幅面 {:.2f} x {:.2f} pt | 旋转 {}".format(len(seal), sw, sh, sp.rotation))
    print("  图片对象：{}".format(len(sp.get_images(full=True))))
    box, kind = detect_color(sp, dpi=args.dpi, color=args.color)
    if box:
        print("  章(彩色像素)在图层内：{} | 尺寸 {:.1f} x {:.1f} pt | 颜色 {}".format(
            fmt(box), box.width, box.height, kind))
        print("  章距图层右边缘 {:.1f} pt、下边缘 {:.1f} pt".format(sx1 - box.x1, sy1 - box.y1))
    else:
        print("  未检测到彩色章像素（可能是黑白章或纯矢量描边），仍按整幅图层叠加。")
    seal.close()

    files = expand_targets(args.target, args.dir, args.pattern)
    for f in files:
        d = open_doc(f)
        print("\n目标：{}  ({} 页)".format(os.path.basename(f), len(d)))
        for i, p in enumerate(d):
            r = placement(p.rect, sw, sh, args.anchor, args.dx, args.dy)
            seal_box = None
            if box:
                # 章在图层内的相对偏移（与图层自身原点无关）
                offx0, offy0 = box.x0 - sx0, box.y0 - sy0
                offx1, offy1 = box.x1 - sx0, box.y1 - sy0
                seal_box = pymupdf.Rect(r.x0 + offx0, r.y0 + offy0,
                                        r.x0 + offx1, r.y0 + offy1)
            tail = "预计章位 {} | 右距 {:.0f}".format(fmt(seal_box), p.rect.x1 - seal_box.x1) if seal_box else ""
            rot = " [旋转{}°]".format(p.rotation) if p.rotation else ""
            print("  p{:<3d} {:>5.0f}x{:<5.0f} 图层 {} {}{}".format(
                i, p.rect.width, p.rect.height, fmt(r), tail, rot))
        d.close()


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #
def cmd_apply(args):
    seal = open_doc(args.seal)
    sp = seal[0]
    sw, sh = sp.rect.width, sp.rect.height
    sx0, sy0 = sp.rect.x0, sp.rect.y0
    files = expand_targets(args.target, args.dir, args.pattern)
    print("章图层 {:.2f} x {:.2f} pt | 锚点 {} | 微调 dx={} dy={}".format(
        sw, sh, args.anchor, args.dx, args.dy))

    # 章在图层内的相对范围，用于「该页是否已盖章」的局部检测
    layer_box = None
    if args.check_existing and not args.force:
        layer_box, _ = detect_color(sp, dpi=args.dpi, color=args.color)
        if not layer_box:
            print("提示：章图层中未检出彩色章，--check-existing 已跳过检测。")

    total = 0
    for f in files:
        doc = open_doc(f)
        n = len(doc)
        idxs = parse_pages(args.pages, n)
        backup = os.path.join(os.path.dirname(f), BACKUP_DIRNAME, os.path.basename(f))
        if args.out_dir:
            os.makedirs(args.out_dir, exist_ok=True)
            dst = os.path.join(args.out_dir, os.path.basename(f))
        else:
            dst = f
            if not args.no_backup:
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                if not os.path.exists(backup) or args.force_backup:
                    shutil.copy2(f, backup)

        skip = []
        if layer_box:
            offx0, offy0 = layer_box.x0 - sx0, layer_box.y0 - sy0
            offx1, offy1 = layer_box.x1 - sx0, layer_box.y1 - sy0
            for i in idxs:
                p = doc[i]
                r0 = placement(p.rect, sw, sh, args.anchor, args.dx, args.dy)
                zone = pymupdf.Rect(r0.x0 + offx0 - 4, r0.y0 + offy0 - 4,
                                    r0.x0 + offx1 + 4, r0.y0 + offy1 + 4)
                zone &= p.rect
                if not zone.is_empty and region_ink_ratio(p, zone, kind=args.color) >= args.stamp_threshold:
                    skip.append(i)
        applied = [i for i in idxs if i not in skip]

        if not args.dry_run:
            for i in applied:
                p = doc[i]
                r = placement(p.rect, sw, sh, args.anchor, args.dx, args.dy)
                p.show_pdf_page(r, seal, 0, overlay=True)
            tmp = dst + ".tmp.pdf"
            doc.save(tmp, garbage=3, deflate=True)
            doc.close()
            os.replace(tmp, dst)
        else:
            doc.close()

        total += len(applied)
        note = ""
        if skip:
            note = " | 跳过疑似已盖章页 {}（重做请先用 restore 恢复，或加 --force）".format(skip)
        print("{} {} | {} 页 -> 盖章 {} 页{}".format(
            "DRY-RUN" if args.dry_run else "OK", os.path.basename(dst), n, len(applied), note))
    seal.close()
    print("完成：{} 个文件，共 {} 页盖章。{}".format(
        len(files), total, "" if args.dry_run else "请运行 verify 校验。"))
    if not args.out_dir and not args.no_backup:
        print("原件备份目录：<目标目录>/{}/（已存在时不会被覆盖）".format(BACKUP_DIRNAME))


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #
def cmd_verify(args):
    files = expand_targets(args.target, args.dir, args.pattern)
    report = []
    ok = True
    for f in files:
        d = open_doc(f)
        entry = {"file": f, "pages": len(d), "missing": [], "items": []}
        for i, p in enumerate(d):
            box, kind = detect_color(p, dpi=args.dpi, color=args.color)
            item = {"page": i, "w": round(p.rect.width), "h": round(p.rect.height)}
            if box:
                item.update({"bbox": [round(v) for v in box],
                             "right_off": round(p.rect.x1 - box.x1),
                             "bottom_off": round(p.rect.y1 - box.y1),
                             "color": kind})
            else:
                entry["missing"].append(i)
                ok = False
            entry["items"].append(item)
        d.close()
        report.append(entry)
        stars = [it for it in entry["items"] if it["page"] in (0, entry["pages"] // 2, entry["pages"] - 1)]
        summary = " ; ".join(
            "p{} {}x{} 章{} 右距{}".format(it["page"], it["w"], it["h"],
                                          fmt(pymupdf.Rect(it["bbox"])) if "bbox" in it else "无",
                                          it.get("right_off", "-"))
            for it in stars)
        if not args.json:
            print("{} | {} 页 | 缺章页 {} | {}".format(
                os.path.basename(f), entry["pages"], entry["missing"] or "无", summary))
    if args.json:
        # 纯 JSON 输出，便于管道/程序消费；缺章时仍以退出码 2 表示失败
        print(json.dumps({"ok": ok, "files": report}, ensure_ascii=False, indent=2))
    else:
        print("\n{}：{} 个文件，{} 页。".format(
            "ALL PAGES STAMPED" if ok else "CHECK FAILED", len(files),
            sum(e["pages"] for e in report)))
    if not ok:
        sys.exit(2)


# --------------------------------------------------------------------------- #
# restore
# --------------------------------------------------------------------------- #
def cmd_restore(args):
    if not os.path.isdir(args.from_dir):
        sys.exit("备份目录不存在：" + args.from_dir)
    src = sorted(glob.glob(os.path.join(args.from_dir, args.pattern)))
    if not src:
        sys.exit("备份目录里没有匹配的 PDF")
    for s in src:
        dst = os.path.join(args.to_dir, os.path.basename(s))
        if not args.dry_run:
            os.makedirs(args.to_dir, exist_ok=True)
            shutil.copy2(s, dst)
        print("{} {} -> {}".format("DRY-RUN" if args.dry_run else "恢复", os.path.basename(s), dst))
    print("共 {} 个文件".format(len(src)))


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description="PDF 盖章：整幅透明章图层 1:1 叠加（PyMuPDF）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p, with_target=True):
        if with_target:
            p.add_argument("--target", nargs="*", default=[], help="目标 PDF，可多个，可含通配符")
            p.add_argument("--dir", help="批量处理目录下所有 PDF（排除备份目录）")
            p.add_argument("--pattern", default="*.pdf", help="--dir 的匹配模式，默认 *.pdf")
        p.add_argument("--dpi", type=int, default=60, help="检测用渲染精度，默认 60")
        p.add_argument("--color", choices=["auto", "red", "blue"], default="auto", help="章颜色检测")
        p.add_argument("--anchor", default="bottom-right",
                       choices=["bottom-right", "top-left", "top-right", "bottom-left", "center"],
                       help="图层对齐锚点，默认 bottom-right")
        p.add_argument("--dx", type=float, default=0.0, help="水平微调 pt（正=向右）")
        p.add_argument("--dy", type=float, default=0.0, help="垂直微调 pt（正=向下）")

    p = sub.add_parser("inspect", help="分析章图层与目标 PDF，只打印不写入")
    p.add_argument("--seal", required=True, help="章图层 PDF")
    add_common(p)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("apply", help="盖章")
    p.add_argument("--seal", required=True, help="章图层 PDF")
    add_common(p)
    p.add_argument("--pages", default="all", help="all | first | 1,3,5 | 2-4（页码 1 基）")
    p.add_argument("--out-dir", help="另存到该目录（不改动原件、不备份）")
    p.add_argument("--no-backup", action="store_true", help="不自动备份原件")
    p.add_argument("--force-backup", action="store_true", help="备份已存在时也覆盖（慎用）")
    p.add_argument("--check-existing", action="store_true", help="检测「章位区域」是否已有彩色章，有则跳过")
    p.add_argument("--stamp-threshold", type=float, default=0.04,
                   help="判定已盖章的彩色像素占比阈值，默认 0.04")
    p.add_argument("--force", action="store_true", help="已有章也照常盖（配合 --check-existing）")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("verify", help="逐页校验章的位置")
    add_common(p)
    p.add_argument("--json", action="store_true", help="附加输出 JSON 报告")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restore", help="从备份目录恢复未盖章原件")
    p.add_argument("--from-dir", required=True, help="备份目录，通常是 <目标目录>/_未盖章原件")
    p.add_argument("--to-dir", required=True, help="要恢复到的目录（原件所在目录）")
    p.add_argument("--pattern", default="*.pdf", help="备份文件匹配模式，默认 *.pdf")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
