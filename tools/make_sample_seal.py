# -*- coding: utf-8 -*-
"""生成脱敏示意章：A2 整幅透明图层，原章位画红框 + 「测试章」。
不依赖 reportlab / PyMuPDF，只用 Pillow + pypdf。"""
import os
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import (DecodedStreamObject, DictionaryObject,
                           NameObject, NumberObject)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "..", "samples", "示意章-测试.pdf")

# 示例章位：A2 图层 1689.12 x 1195.92 pt，
# 框 (1433.28, 599.76) - (1639.20, 678.96)，距右 49.92 pt / 距下 599.76 pt
PAGE_W, PAGE_H = 1689.12, 1195.92
BOX_X, BOX_Y, BOX_W, BOX_H = 1433.28, 599.76, 205.92, 79.20
PW, PH = 412, 158                      # 图像像素（2 倍密度）

img = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
red = (200, 30, 30, 255)
d.rectangle((2, 2, PW - 3, PH - 3), outline=red, width=4)
d.line((8, 40, PW - 9, 40), fill=red, width=2)

try:
    font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", 48)
except Exception:
    font = ImageFont.load_default()
text = "测试章"
bb = d.textbbox((0, 0), text, font=font)
tw, th = bb[2] - bb[0], bb[3] - bb[1]
d.text(((PW - tw) / 2 - bb[0], 62 - bb[1]), text, font=font, fill=red)

try:
    small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 17)
except Exception:
    small = ImageFont.load_default()
sub = "SAMPLE - NOT A REAL SEAL"
bb2 = d.textbbox((0, 0), sub, font=small)
d.text(((PW - (bb2[2] - bb2[0])) / 2 - bb2[0], PH - 34), sub, font=small, fill=red)

rgb = Image.new("RGB", img.size, (255, 255, 255))
rgb.paste(img, mask=img.split()[3])
rgb_bytes, alpha_bytes = rgb.tobytes(), img.split()[3].tobytes()

w = PdfWriter()
page = w.add_blank_page(width=PAGE_W, height=PAGE_H)

mask = DecodedStreamObject()
mask.set_data(alpha_bytes)
mask = mask.flate_encode()
mask.update({
    NameObject("/Type"): NameObject("/XObject"),
    NameObject("/Subtype"): NameObject("/Image"),
    NameObject("/Width"): NumberObject(PW),
    NameObject("/Height"): NumberObject(PH),
    NameObject("/ColorSpace"): NameObject("/DeviceGray"),
    NameObject("/BitsPerComponent"): NumberObject(8),
})
mask_ref = w._add_object(mask)

im = DecodedStreamObject()
im.set_data(rgb_bytes)
im = im.flate_encode()
im.update({
    NameObject("/Type"): NameObject("/XObject"),
    NameObject("/Subtype"): NameObject("/Image"),
    NameObject("/Width"): NumberObject(PW),
    NameObject("/Height"): NumberObject(PH),
    NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
    NameObject("/BitsPerComponent"): NumberObject(8),
    NameObject("/SMask"): mask_ref,
})
im_ref = w._add_object(im)

res = page.get("/Resources")
if res is None:
    res = DictionaryObject()
    page[NameObject("/Resources")] = res
res[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): im_ref})

stream = DecodedStreamObject()
stream.set_data(b"q\n%.2f 0 0 %.2f %.2f %.2f cm\n/Im0 Do\nQ\n" %
                (BOX_W, BOX_H, BOX_X, BOX_Y))
page[NameObject("/Contents")] = w._add_object(stream)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
w.write(OUT)
print("生成:", OUT, os.path.getsize(OUT), "字节")
