# -*- coding: utf-8 -*-
"""核心逻辑回归测试。只用 pypdf，不依赖 tkinter / pypdfium2，可在 CI 里跑。"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject)

import stamp_gui as core


def make_seal(width=1689.12, height=1195.92):
    """合成一枚整幅透明章图层：内容是一个 Form XObject，放在右下角。"""
    w = PdfWriter()
    page = w.add_blank_page(width=width, height=height)
    form = DecodedStreamObject()
    form.set_data(b"q 0.8 0.1 0.1 rg 0 0 206 79 re f Q")
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0),
                                          FloatObject(206), FloatObject(79)]),
        NameObject("/Resources"): DictionaryObject(),
    })
    ref = w._add_object(form)
    res = page.get("/Resources")
    if res is None:
        res = DictionaryObject()
        page[NameObject("/Resources")] = res
    res[NameObject("/XObject")] = DictionaryObject({NameObject("/Seal0"): ref})
    cs = DecodedStreamObject()
    cs.set_data(b"q 1 0 0 1 1433 600 cm /Seal0 Do Q")
    page[NameObject("/Contents")] = w._add_object(cs)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def make_target(sizes):
    w = PdfWriter()
    for pw, ph in sizes:
        w.add_blank_page(width=pw, height=ph)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="stamp_test_")
        self.seal = make_seal()
        self.sizes = [(1689.12, 1195.92), (2112.0, 1195.92), (595.0, 841.0)]
        self.src = os.path.join(self.td, "drawing.pdf")
        with open(self.src, "wb") as f:
            f.write(make_target(self.sizes))

    def test_parse_pages(self):
        self.assertEqual(core.parse_pages("all", 3), [0, 1, 2])
        self.assertEqual(core.parse_pages("first", 3), [0])
        self.assertEqual(core.parse_pages("2-3", 3), [1, 2])
        self.assertEqual(core.parse_pages("1,3", 3), [0, 2])

    def test_stamp_and_skip(self):
        out_dir = os.path.join(self.td, "out")
        dst, n, pl, skipped = core.stamp_file(
            self.src, self.seal, "all", "custom", out_dir, log=lambda m: None)
        self.assertEqual(n, 3)
        self.assertEqual(skipped, [])
        self.assertEqual(len(pl), 3)
        self.assertAlmostEqual(pl[0][3], 0.0, places=1)          # A2 页：整幅重合
        self.assertAlmostEqual(pl[1][3], 2112.0 - 1689.12, places=1)  # 加宽页右移
        _, _, _, skipped2 = core.stamp_file(
            dst, self.seal, "all", "custom", out_dir,
            log=lambda m: None, skip_stamped=True)
        self.assertEqual(skipped2, [1, 2, 3], "二次盖章应全部跳过，不能叠第二层")

    def test_offset(self):
        out_dir = os.path.join(self.td, "out2")
        _, _, pl, _ = core.stamp_file(
            self.src, self.seal, "first", "custom", out_dir,
            log=lambda m: None, dx=10, dy=-5)
        self.assertAlmostEqual(pl[0][3], 10.0, places=1)
        self.assertAlmostEqual(pl[0][4], -5.0, places=1)

    def test_output_path_rename(self):
        dst, _ = core.output_path(self.src, "stamped", None, "overwrite")
        self.assertTrue(dst.endswith("drawing.pdf"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(b"x")
        dst2, _ = core.output_path(self.src, "stamped", None, "rename")
        self.assertTrue(dst2.endswith("drawing_已签章2.pdf"))


if __name__ == "__main__":
    unittest.main()
