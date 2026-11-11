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
                           FloatObject, NameObject, TextStringObject)

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

    def test_ink_bbox(self):
        """章在整幅图层里的真实落位要能解析出来（示意章只占右下角一小块）。"""
        from pypdf import PdfReader
        page = PdfReader(io.BytesIO(self.seal)).pages[0]
        box = core.seal_ink_bbox(page)
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box[0], 1433.0, places=1)
        self.assertAlmostEqual(box[1], 600.0, places=1)
        self.assertAlmostEqual(box[2], 1639.0, places=1)
        self.assertAlmostEqual(box[3], 679.0, places=1)

    def test_ink_bbox_nested_form_matrix(self):
        """Form 自带 Matrix 的章层（AutoCAD 导出常见）：位置要按 Matrix 算，不能落到原点。"""
        from pypdf import PdfReader
        w = PdfWriter()
        page = w.add_blank_page(width=1689.12, height=1195.92)
        inner = DecodedStreamObject()
        inner.set_data(b"q 1 1 1 rg 0 0 10 10 re f Q")
        inner.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0),
                                              FloatObject(10), FloatObject(10)]),
            NameObject("/Resources"): DictionaryObject(),
        })
        outer = DecodedStreamObject()
        outer.set_data(b"q /In Do Q")
        outer.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0),
                                              FloatObject(206), FloatObject(79)]),
            NameObject("/Matrix"): ArrayObject([FloatObject(1), FloatObject(0),
                                                FloatObject(0), FloatObject(1),
                                                FloatObject(1400), FloatObject(500)]),
            NameObject("/Resources"): DictionaryObject({
                NameObject("/XObject"): DictionaryObject({NameObject("/In"): w._add_object(inner)})}),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Fx"): w._add_object(outer)})})
        cs = DecodedStreamObject()
        cs.set_data(b"q /Fx Do Q")
        page[NameObject("/Contents")] = w._add_object(cs)
        buf = io.BytesIO()
        w.write(buf)
        page2 = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
        box = core.seal_ink_bbox(page2)
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box[0], 1400.0, places=1)
        self.assertAlmostEqual(box[1], 500.0, places=1)
        self.assertAlmostEqual(box[2], 1606.0, places=1)
        self.assertAlmostEqual(box[3], 579.0, places=1)

    def test_small_page_not_pushed_off(self):
        """A4 页比章图层小，章必须落在纸内，不能被推到页面外。"""
        small = os.path.join(self.td, "a4.pdf")
        with open(small, "wb") as f:
            f.write(make_target([(595.0, 841.0)]))
        out_dir = os.path.join(self.td, "out3")
        _, _, pl, _ = core.stamp_file(
            small, self.seal, "all", "custom", out_dir, log=lambda m: None)
        # 墨迹 206x79，贴右下角
        self.assertAlmostEqual(pl[0][3] + 1639.0, 595.0, places=1)
        self.assertAlmostEqual(pl[0][4] + 600.0, 0.0, places=1)

    def test_a2_and_larger_untouched(self):
        """图幅不小于章图层时（A2 及更大），平移必须与旧公式完全一致。"""
        out_dir = os.path.join(self.td, "out4")
        _, _, pl, _ = core.stamp_file(
            self.src, self.seal, "all", "custom", out_dir, log=lambda m: None)
        for rec in pl[:2]:
            pw = rec[1]
            self.assertAlmostEqual(rec[3], pw - 1689.12, places=2)
            self.assertAlmostEqual(rec[4], 0.0, places=2)

    def test_find_and_extract_seal(self):
        """从带电子签章的图纸里提取章：结构要完整，图像字节要一模一样。"""
        import hashlib
        from pypdf import PdfReader

        src = os.path.join(self.td, "signed.pdf")
        with open(src, "wb") as f:
            f.write(make_signed_pdf())

        items = core.find_signatures(src)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["page"], 1)
        self.assertAlmostEqual(items[0]["size"][0], 204.0, places=1)
        self.assertAlmostEqual(items[0]["size"][1], 99.0, places=1)

        out_dir = os.path.join(self.td, "seals")
        saved = core.extract_seals_to(src, items, out_dir=out_dir, log=lambda m: None)
        self.assertEqual(len(saved), 1)

        def deepest_image(obj, depth=0):
            obj = obj.get_object() if hasattr(obj, "get_object") else obj
            if depth > 12:
                return None
            if isinstance(obj, dict):
                if str(obj.get("/Subtype")) == "/Image":
                    return obj
                for k, v in obj.items():
                    if str(k) in ("/Parent", "/P"):
                        continue
                    got = deepest_image(v, depth + 1)
                    if got is not None:
                        return got
            elif isinstance(obj, list):
                for v in obj:
                    got = deepest_image(v, depth + 1)
                    if got is not None:
                        return got
            return None

        src_img = deepest_image(PdfReader(src).pages[0]["/Annots"][0].get_object()["/AP"])
        new_img = deepest_image(PdfReader(saved[0][0]).pages[0]["/Resources"])
        self.assertIsNotNone(src_img)
        self.assertIsNotNone(new_img)
        self.assertEqual(hashlib.sha256(src_img.get_data()).hexdigest(),
                         hashlib.sha256(new_img.get_data()).hexdigest())

    def test_issue_seal_filter(self):
        """出图章/注册章要被选中，审图章默认排除。"""
        self.assertTrue(core._is_issue_seal({"size": (204.0, 99.0)}))
        self.assertTrue(core._is_issue_seal({"size": (170.0, 85.0)}))
        self.assertFalse(core._is_issue_seal({"size": (207.0, 82.0)}))


def make_signed_pdf():
    """造一份带电子签章（/FT /Sig + 外观图像）的图纸。"""
    w = PdfWriter()
    page = w.add_blank_page(width=1695.0, height=1202.0)

    img = DecodedStreamObject()
    img.set_data(bytes([255, 0, 0, 255, 255, 255, 255, 255, 255, 255, 0, 0]))
    img.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): FloatObject(2),
        NameObject("/Height"): FloatObject(2),
        NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
        NameObject("/BitsPerComponent"): FloatObject(8),
    })
    img_ref = w._add_object(img)

    form = DecodedStreamObject()
    form.set_data(b"q 204 0 0 99 0 0 cm /IM Do Q")
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0),
                                          FloatObject(204), FloatObject(99)]),
        NameObject("/FormType"): FloatObject(1),
        NameObject("/Resources"): DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/IM"): img_ref})}),
    })
    form_ref = w._add_object(form)

    sig = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Sig"),
        NameObject("/T"): TextStringObject("Signature1"),
        NameObject("/Rect"): ArrayObject([FloatObject(1091), FloatObject(200),
                                          FloatObject(1295), FloatObject(299)]),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): form_ref}),
    })
    page[NameObject("/Annots")] = ArrayObject([w._add_object(sig)])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


if __name__ == "__main__":
    unittest.main()
