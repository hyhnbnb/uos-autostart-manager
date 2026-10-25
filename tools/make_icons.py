#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成应用图标（纯 Pillow，无需外部工具）。

产物：
    assets/icon.svg
    assets/icon-<size>.png   (16/24/32/48/64/128/256)

用法：
    python tools/make_icons.py
"""
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
MASTER = 1024
SIZES = (16, 24, 32, 48, 64, 128, 256)

BLUE = (43, 125, 233)
PURPLE = (107, 78, 230)
WHITE = (255, 255, 255)


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def make_master():
    """绘制 1024x1024 主图：圆角渐变底 + 白色电源符号。"""
    img = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))

    # 渐变背景
    grad = Image.new("RGB", (1, MASTER))
    gd = ImageDraw.Draw(grad)
    for y in range(MASTER):
        gd.point((0, y), _lerp(BLUE, PURPLE, y / float(MASTER - 1)))
    grad = grad.resize((MASTER, MASTER), Image.BILINEAR)

    # 圆角遮罩（UOS 风格大圆角）
    mask = Image.new("L", (MASTER, MASTER), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, MASTER - 1, MASTER - 1], radius=int(MASTER * 0.22), fill=255)

    img.paste(grad, (0, 0), mask)

    # 高光（顶部轻微白雾，增加立体感）
    gloss = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    gd2 = ImageDraw.Draw(gloss)
    gd2.ellipse(
        [-MASTER * 0.45, -MASTER * 0.85, MASTER * 1.45, MASTER * 0.35],
        fill=(255, 255, 255, 34),
    )
    gloss.putalpha(Image.composite(gloss.getchannel("A"), Image.new("L", (MASTER, MASTER), 0), mask))
    img = Image.alpha_composite(img, gloss)

    draw = ImageDraw.Draw(img)
    cx, cy = MASTER / 2.0, MASTER * 0.53
    radius = MASTER * 0.235
    width = int(MASTER * 0.075)

    # 电源符号：缺口向上的圆弧
    draw.arc(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        start=300,
        end=240,
        fill=WHITE,
        width=width,
    )
    # 竖线
    draw.rounded_rectangle(
        [cx - width / 2.0, cy - radius * 1.42, cx + width / 2.0, cy - radius * 0.05],
        radius=width / 2.0,
        fill=WHITE,
    )

    # 底部小圆点（“启动”语义，避免图标过于单调）
    dot_r = MASTER * 0.032
    dot_y = MASTER * 0.845
    draw.ellipse([cx - dot_r, dot_y - dot_r, cx + dot_r, dot_y + dot_r], fill=(255, 255, 255, 220))

    return img


SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2b7de9"/>
      <stop offset="1" stop-color="#6b4ee6"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="256" height="256" rx="56" ry="56" fill="url(#bg)"/>
  <path d="M128 66 v52" stroke="#ffffff" stroke-width="19" stroke-linecap="round" fill="none"/>
  <path d="M79 100 a58 58 0 1 0 98 0" stroke="#ffffff" stroke-width="19"
        stroke-linecap="round" fill="none" transform="translate(0,8)"/>
  <circle cx="128" cy="216" r="8" fill="#ffffff" opacity="0.9"/>
</svg>
"""


def main():
    if not os.path.isdir(ASSETS):
        os.makedirs(ASSETS)
    master = make_master()
    written = []
    for size in SIZES:
        out = os.path.join(ASSETS, "icon-%d.png" % size)
        master.resize((size, size), Image.LANCZOS).save(out, "PNG", optimize=True)
        written.append(out)
    svg_path = os.path.join(ASSETS, "icon.svg")
    with open(svg_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(SVG)
    written.append(svg_path)
    for path in written:
        print("wrote %s (%d bytes)" % (os.path.relpath(path, ROOT), os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
