#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf-seal-stamp-lite — overlay a full-page transparent seal layer onto PDFs.

Zero GUI. Only depends on pypdf (pure Python). No seal file is bundled:
the caller must always supply one via --seal.

Commands
    inspect   analyze the seal layer and the target PDFs (no writing)
    apply     stamp pages and verify the result
    verify    check that every targeted page really carries the seal layer
    restore   copy the untouched original back from the backup folder

Every command supports --json and uses stable exit codes:
    0 success / 1 error / 2 verification failed
"""

import argparse
import glob
import io
import json
import os
import shutil
import sys

try:
    from pypdf import PdfReader, PdfWriter, Transformation
except ImportError:
    sys.exit("pypdf is required: python -m pip install pypdf")

STAMPED_DIR = "已签章"
BACKUP_DIR = "_未盖章原件"
EXIT_OK, EXIT_FAIL, EXIT_VERIFY = 0, 1, 2
PAGE_ALL, PAGE_FIRST = "all", "first"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def deref(obj):
    return obj.get_object() if hasattr(obj, "get_object") else obj


def parse_pages(spec, n):
    """all / first / 1,3,5 / 2-4 (1-based input) -> sorted 0-based indexes."""
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
            out.extend(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    idxs = sorted({i for i in out if 0 <= i < n})
    if not idxs:
        raise ValueError("no valid page in range: %s (document has %d pages)" % (spec, n))
    return idxs


def xobj_names(page):
    res = deref(page.get("/Resources"))
    xobjs = deref(res.get("/XObject")) if res else None
    return set(str(k) for k in xobjs.keys()) if xobjs else set()


def page_content(page):
    c = deref(page.get("/Contents"))
    if c is None:
        return b""
    if hasattr(c, "get_data"):
        return c.get_data()
    if isinstance(c, (list, tuple)):
        return b"\n".join(deref(x).get_data() for x in c)
    return b""


def page_has_seal(page, names):
    if not names:
        return True
    content = page_content(page)
    return any(n.encode("latin-1", "replace") in content for n in names)


def seal_markers(seal_page):
    """Structural fingerprints of the seal layer's XObjects."""
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


def page_markers(page):
    res = deref(page.get("/Resources"))
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


def recompress(page):
    try:
        page.compress_content_streams()
    except Exception:
        pass


def output_path(src, out_mode, custom_dir, exists_mode):
    name = os.path.basename(src)
    src_dir = os.path.dirname(os.path.abspath(src))
    if out_mode == "overwrite":
        return src, src_dir
    if out_mode == "stamped":
        dst_dir = os.path.join(src_dir, STAMPED_DIR)
    elif out_mode == "custom":
        if not custom_dir:
            raise ValueError("--out custom requires --dir")
        dst_dir = custom_dir
    else:
        raise ValueError("unknown out mode: " + out_mode)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, name)
    if exists_mode == "rename" and os.path.exists(dst):
        stem, ext = os.path.splitext(name)
        k = 2
        while True:
            cand = os.path.join(dst_dir, "%s_已签章%d%s" % (stem, k, ext))
            if not os.path.exists(cand):
                dst = cand
                break
            k += 1
    return dst, dst_dir


def expand_targets(items, directory, pattern="*.pdf"):
    found = []
    for item in items or []:
        if os.path.isdir(item):
            found.extend(sorted(glob.glob(os.path.join(item, "*.pdf"))))
        elif any(ch in item for ch in "*?"):
            found.extend(sorted(glob.glob(item)))
        elif os.path.isfile(item):
            found.append(os.path.abspath(item))
    if directory:
        found.extend(sorted(glob.glob(os.path.join(directory, pattern))))
    out = []
    for p in found:
        if p.lower().endswith(".pdf") and p not in out:
            out.append(os.path.abspath(p))
    return out


def open_seal(path):
    if not path:
        raise ValueError("a seal layer PDF is required: pass --seal path/to/seal.pdf")
    if not os.path.isfile(path):
        raise ValueError("seal file not found: " + path)
    with open(path, "rb") as f:
        raw = f.read()
    doc = PdfReader(io.BytesIO(raw))
    if doc.is_encrypted:
        raise ValueError("seal PDF is encrypted")
    if len(doc.pages) < 1:
        raise ValueError("seal PDF has no page")
    return raw, doc.pages[0]


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def stamp_one(src, seal_bytes, pages_spec, out_mode, custom_dir, exists_mode,
              log=lambda m: None):
    name = os.path.basename(src)
    with open(src, "rb") as f:
        data = f.read()
    doc = PdfReader(io.BytesIO(data))
    if doc.is_encrypted:
        raise ValueError("target PDF is encrypted: " + name)

    seal_doc = PdfReader(io.BytesIO(seal_bytes))
    seal_page = seal_doc.pages[0]
    sw, sh = float(seal_page.mediabox.width), float(seal_page.mediabox.height)
    sx0, sy0 = float(seal_page.mediabox.left), float(seal_page.mediabox.bottom)

    idxs = set(parse_pages(pages_spec, len(doc.pages)))
    writer = PdfWriter()
    added_by_page = {}
    placements = []
    for i, page in enumerate(doc.pages):
        if i in idxs:
            pw, ph = float(page.mediabox.width), float(page.mediabox.height)
            tx = (pw - sw) + float(page.mediabox.left) - sx0
            ty = (ph - sh) + float(page.mediabox.bottom) - sy0
            before = xobj_names(page)
            page.merge_transformed_page(
                seal_page, Transformation().translate(tx, ty),
                over=True, expand=False)
            added_by_page[i] = xobj_names(page) - before
            placements.append({"page": i + 1, "width": pw, "height": ph,
                               "translate": [round(tx, 2), round(ty, 2)]})
            log("page %d: %.0fx%.0f translate(%.2f,%.2f)" % (i + 1, pw, ph, tx, ty))
        writer.add_page(page)
        recompress(writer.pages[-1])

    dst, _ = output_path(src, out_mode, custom_dir, exists_mode)
    if out_mode == "overwrite":
        bdir = os.path.join(os.path.dirname(os.path.abspath(src)), BACKUP_DIR)
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, name)
        if not os.path.exists(backup):
            shutil.copy2(src, backup)
            log("backup: " + backup)

    tmp = dst + ".tmp"
    try:
        writer.write(tmp)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    with open(dst, "rb") as f:
        check = PdfReader(io.BytesIO(f.read()))
    if len(check.pages) != len(doc.pages):
        raise ValueError("page count changed after stamping: " + name)
    missing = [i + 1 for i in sorted(idxs)
               if not page_has_seal(check.pages[i], added_by_page.get(i, set()))]
    if missing:
        raise RuntimeError("seal missing on pages: %s" % missing)
    return {"input": os.path.abspath(src), "output": os.path.abspath(dst),
            "pages": len(check.pages), "stamped_pages": sorted(i + 1 for i in idxs),
            "placements": placements, "size": os.path.getsize(dst)}


def cmd_inspect(args):
    subjects = expand_targets(args.target, args.dir)
    seal_bytes, seal_page = open_seal(args.seal)
    sw, sh = float(seal_page.mediabox.width), float(seal_page.mediabox.height)
    result = {"ok": True, "seal": {"path": os.path.abspath(args.seal),
                                   "pages": len(PdfReader(io.BytesIO(seal_bytes)).pages),
                                   "width": round(sw, 2), "height": round(sh, 2),
                                   "markers": len(seal_markers(seal_page))},
              "targets": []}
    for src in subjects:
        doc = PdfReader(src)
        sizes = [[round(float(p.mediabox.width), 1), round(float(p.mediabox.height), 1)]
                 for p in doc.pages]
        result["targets"].append({"path": os.path.abspath(src), "pages": len(doc.pages),
                                  "page_sizes": sizes})
    if not args.json:
        print("seal layer: %s  %.2f x %.2f pt" % (args.seal, sw, sh))
        for t in result["targets"]:
            print("%s  (%d pages)  sizes=%s" % (t["path"], t["pages"], t["page_sizes"]))
    else:
        print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def cmd_apply(args):
    targets = expand_targets(args.target, args.dir)
    if not targets:
        raise ValueError("no target PDF found")
    seal_bytes, _ = open_seal(args.seal)
    results, failures = [], []
    for src in targets:
        try:
            results.append(stamp_one(src, seal_bytes, args.pages, args.out,
                                     args.dir_out, args.exists, log=lambda m: None))
        except Exception as e:
            failures.append({"input": os.path.abspath(src), "error": str(e)})
    payload = {"ok": not failures, "applied": results, "failed": failures}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for r in results:
            print("%s -> %s (%d pages, stamped %d)" % (
                os.path.basename(r["input"]), r["output"], r["pages"],
                len(r["stamped_pages"])))
        for f in failures:
            print("FAILED %s: %s" % (os.path.basename(f["input"]), f["error"]))
    return EXIT_OK if not failures else EXIT_FAIL


def cmd_verify(args):
    files = expand_targets(args.file or args.target, args.dir)
    if not files:
        raise ValueError("no file to verify")
    _, seal_page = open_seal(args.seal)
    marks = seal_markers(seal_page)
    report = []
    for src in files:
        doc = PdfReader(src)
        idxs = parse_pages(args.pages, len(doc.pages))
        missing = []
        for i in idxs:
            page_marks = page_markers(doc.pages[i])
            if marks and not any(m in page_marks for m in marks):
                missing.append(i + 1)
        report.append({"file": os.path.abspath(src), "pages": len(doc.pages),
                       "checked": len(idxs), "missing": missing,
                       "ok": not missing})
    ok = all(r["ok"] for r in report)
    payload = {"ok": ok, "files": report}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for r in report:
            print("%s: %s (%d/%d pages carry the seal)" % (
                os.path.basename(r["file"]), "OK" if r["ok"] else "MISSING",
                r["checked"] - len(r["missing"]), r["checked"]))
    return EXIT_OK if ok else EXIT_VERIFY


def cmd_restore(args):
    files = expand_targets(args.target, args.dir)
    if not files:
        raise ValueError("no target given")
    restored, failed = [], []
    for src in files:
        src = os.path.abspath(src)
        backup = os.path.join(os.path.dirname(src), BACKUP_DIR, os.path.basename(src))
        if not os.path.isfile(backup):
            failed.append({"file": src, "error": "backup not found: " + backup})
            continue
        shutil.copy2(backup, src)
        restored.append(src)
    payload = {"ok": not failed, "restored": restored, "failed": failed}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for p in restored:
            print("restored:", p)
        for f in failed:
            print("FAILED %s: %s" % (f["file"], f["error"]))
    return EXIT_OK if not failed else EXIT_FAIL


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="stamp.py",
        description="Overlay a full-page transparent seal layer onto PDFs "
                    "(pypdf only, no GUI). Seal files are always supplied by the caller.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, seal=True, targets=True):
        if seal:
            p.add_argument("--seal", help="seal layer PDF (full-page transparent)")
        if targets:
            p.add_argument("--target", action="append", default=[],
                           help="target PDF, folder or glob; repeatable")
            p.add_argument("--dir", help="folder with target PDFs")
        p.add_argument("--json", action="store_true", help="machine readable output")

    p = sub.add_parser("inspect", help="analyze seal layer and targets")
    common(p)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("apply", help="stamp and verify")
    common(p)
    p.add_argument("--pages", default=PAGE_ALL, help="all | first | 1,3,5 | 2-4")
    p.add_argument("--out", default="stamped", choices=["stamped", "overwrite", "custom"],
                   help="stamped=源目录/已签章 (default) | overwrite=backup then replace | custom")
    p.add_argument("--dir-out", help="output folder for --out custom")
    p.add_argument("--exists", default="overwrite", choices=["overwrite", "rename"],
                   help="what to do when the output name already exists")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("verify", help="check pages really carry the seal")
    common(p, targets=False)
    p.add_argument("--file", action="append", default=[], help="file to verify; repeatable")
    p.add_argument("--target", action="append", default=[], help="alias of --file")
    p.add_argument("--dir", help="folder to verify")
    p.add_argument("--pages", default=PAGE_ALL, help="all | first | 1,3,5 | 2-4")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restore", help="restore originals from the backup folder")
    common(p, seal=False)
    p.set_defaults(func=cmd_restore)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            print("error: %s" % e, file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
