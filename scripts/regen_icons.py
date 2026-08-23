"""PA 桌面图标预处理: 去除白底 → 多尺寸 PNG + icon.ico (Pillow)。

输入: pictures/桌面图标.jpg (1254x1254 白底蓝标)
输出: frontend/build/
  - icon-source.png   (1024x1024 透明 PNG)
  - icon-1024/512/256/128/64/48/32/24/16.png
  - icon.ico          (16/24/32/48/64/128/256 七尺寸合集)
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

SRC = Path(r"D:\Private agent\pictures\桌面图标.jpg")
OUT_DIR = Path(r"D:\Private agent\frontend\build")

# 抠图阈值
WHITE_FULL = 245    # 亮度 > 245 → 完全透明
WHITE_EDGE = 220    # 亮度 220~245 → 渐变 Alpha(抗锯齿)
SIZES = [1024, 512, 256, 128, 64, 48, 32, 24, 16]
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def remove_white_bg(img: Image.Image) -> Image.Image:
    """亮度阈值去白底: >245 全透明, 220~245 渐变 Alpha, 其余保留。"""
    img = img.convert("RGBA")
    pixels = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            brightness = (r + g + b) / 3.0
            if brightness > WHITE_FULL:
                pixels[x, y] = (r, g, b, 0)
            elif brightness > WHITE_EDGE:
                alpha = int((WHITE_FULL - brightness) / (WHITE_FULL - WHITE_EDGE) * 255)
                alpha = max(0, min(255, alpha))
                pixels[x, y] = (r, g, b, alpha)
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.open(SRC)
    # 1. 去白底(原始分辨率先抠, 再缩放, 保证边缘质量)
    transparent = remove_white_bg(img)
    # 2. 主源图 1024
    source_1024 = transparent.resize((1024, 1024), Image.LANCZOS)
    source_1024.save(OUT_DIR / "icon-source.png", "PNG")
    print("icon-source.png 1024x1024 保存完成")
    # 3. 多尺寸 PNG
    for s in SIZES:
        resized = transparent.resize((s, s), Image.LANCZOS)
        resized.save(OUT_DIR / f"icon-{s}.png", "PNG")
    print(f"多尺寸 PNG 生成: {SIZES}")
    # 4. icon.ico 七尺寸合集
    base_256 = transparent.resize((256, 256), Image.LANCZOS)
    ico_frames = [base_256.resize(s, Image.LANCZOS) for s in ICO_SIZES]
    base_256.save(OUT_DIR / "icon.ico", sizes=ICO_SIZES, append_images=ico_frames[1:])
    print("icon.ico 七尺寸合集生成完成")


if __name__ == "__main__":
    main()
