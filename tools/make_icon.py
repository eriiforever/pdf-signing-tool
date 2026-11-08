# -*- coding: utf-8 -*-
"""生成程序图标：红框印章风格，无真实单位信息。需 Pillow。"""
from PIL import Image, ImageDraw, ImageFont

img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, 248, 248), radius=48, fill=(255, 255, 255, 255),
                    outline=(178, 34, 34, 255), width=8)
d.ellipse((36, 36, 220, 220), outline=(200, 30, 30, 255), width=14)
try:
    font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", 112)
    t = "章"
    bb = d.textbbox((0, 0), t, font=font)
    d.text(((256 - (bb[2] - bb[0])) / 2 - bb[0],
            (256 - (bb[3] - bb[1])) / 2 - bb[1] - 6), t, font=font,
           fill=(200, 30, 30, 255))
except Exception:
    d.rectangle((96, 96, 160, 160), fill=(200, 30, 30, 255))
img.save("stamp.ico", format="ICO",
         sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
