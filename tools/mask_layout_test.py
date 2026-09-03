# -*- coding: utf-8 -*-
"""蒙版排版保真测试（不翻译）：把 OCR 原文按原行框渲染进蒙版窗口。

用途：验证渲染层对 OCR 几何（行位/行高/行距）的还原度，是排查「译文行距过宽 /
区域容纳不下」的基线——
- 若「原文原样排版」行距就不对 → 问题在渲染/几何层（_render / _compute_line_geom）；
- 若原文排版正确、只有译文行距宽 → 问题在行分布层（_distribute_translation）。

用法（项目根目录）：
    .venv/Scripts/python.exe tools/mask_layout_test.py             # 默认 pho/*.png
    .venv/Scripts/python.exe tools/mask_layout_test.py 图.png ...  # 指定图片
    .venv/Scripts/python.exe tools/mask_layout_test.py --hold      # 窗口不自动关
    .venv/Scripts/python.exe tools/mask_layout_test.py --auto-close 20
输出：每行几何报告（y0/y1/h/gap/fs/字符数）+ 弹出蒙版窗口（默认 10 秒自动关）。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from PIL import Image

from winocr.services.ocr.rapidocr import RapidOcrEngine
from winocr.ui.tk.mask_const import BAR_H, FONT_FLOOR
from winocr.ui.tk.mask_render import (_compute_line_geom, _distribute_translation,
                                      _fit_font_size, _global_fs_cap,
                                      _line_fs_cap, _median_height)
from winocr.ui.tk.mask_window import MaskWindow


def print_layout_report(ocr_result, bw, bh, pp):
    """复现 _render 的几何/字号计算，打印每行数据供分析。"""
    geom = _compute_line_geom(ocr_result.lines, ocr_result.line_boxes)
    n = len(geom)
    tr_lines = _distribute_translation(ocr_result.text, n)
    usable_w = max(bw - 16, 1)
    avg_line_h = bh / max(n, 1)
    heights = [g["h"] for g in geom]
    fs_cap = _global_fs_cap(heights, avg_line_h, pp)
    base_h = _median_height(heights)
    print(f"fs_cap={fs_cap} base_h={base_h} (正文统一上限 / 标题≥{base_h*1.6:.0f}px 放行)")
    print(f"{'i':>3} {'y0':>5} {'y1':>5} {'h':>4} {'gap':>4} {'fs':>3} "
          f"{'chars':>5}  text")
    for i, g in enumerate(geom):
        line_txt = tr_lines[i] if i < len(tr_lines) else ""
        fs = _fit_font_size(line_txt, usable_w, pp,
                            _line_fs_cap(g["h"], base_h, fs_cap, pp))
        print(f"{i:>3} {g['y0']:>5.0f} {g['y1']:>5.0f} {g['h']:>4.0f} "
              f"{g['gap_before']:>4.0f} {fs:>3} {len(line_txt):>5}  {line_txt[:32]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="*", help="测试图片路径（默认 pho/*.png）")
    ap.add_argument("--hold", action="store_true", help="窗口不自动关闭")
    ap.add_argument("--auto-close", type=int, default=10,
                    help="窗口自动关闭秒数（默认 10）")
    args = ap.parse_args()

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = args.images or sorted(glob.glob(os.path.join(root_dir, "pho", "*.png")))
    if not paths:
        print("未找到测试图片", file=sys.stderr)
        return 1

    engine = RapidOcrEngine()
    root = tk.Tk()
    root.withdraw()
    # 测试模式：禁掉 _kick，窗口不翻译，只渲染我们喂进去的 OCR 原文
    MaskWindow._kick = lambda self: None
    pipeline = SimpleNamespace(services={}, run_async=lambda *a, **k: None,
                               translate=lambda *a, **k: None)

    win_y = 60
    for p in paths:
        img = Image.open(p).convert("RGB")
        ocr = engine.recognize(img)
        print(f"\n=== {os.path.basename(p)}  {img.size[0]}x{img.size[1]}  "
              f"lines={len(ocr.lines)}  elapsed={ocr.elapsed:.2f}s ===")
        if not (ocr.lines and ocr.line_boxes):
            print("（无行框结果，跳过）")
            continue
        # 窗口 Canvas 区 = 原图 1:1（bbox 高 = 图高 + BAR_H 标题栏占位）
        bbox = (60, win_y, 60 + img.size[0], win_y + img.size[1] + BAR_H)
        w = MaskWindow(root, bbox, pipeline, None)
        w._render(ocr, ocr.text)          # 原样排版：译文 = OCR 原文
        print_layout_report(ocr, w._bw, w._bh, w._px_per_pt)
        win_y += img.size[1] + BAR_H + 40

    if args.hold:
        root.mainloop()
    else:
        root.after(args.auto_close * 1000, root.destroy)
        root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
