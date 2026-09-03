# -*- coding: utf-8 -*-
"""蒙版排版保真契约测试（离屏、mock OCR、不依赖真实模型）。

锁定「OCR 原样排版」几何契约：
- 每行渲染 y = 原 OCR 行框垂直中心 - BAR_H（行位逐行对齐）；
- 渲染相邻行距 == 原始行框间距（行距保真，不放大不压缩）；
- 字号全局统一上限（行高中位数）——单行 OCR 框虚高不得把该行字号带飞。

配套真实样本验证工具：tools/mask_layout_test.py（真实 OCR + 原文渲染 + 几何报告）。
"""
import re
import tkinter as tk
from types import SimpleNamespace

from winocr.ui.tk.mask_const import BAR_H
from winocr.ui.tk.mask_render import (MaskRenderer, _global_fs_cap,
                                      _line_fs_cap)
from winocr.ui.tk.mask_window import MaskWindow


def _make_renderable():
    root = tk.Tk()
    root.withdraw()
    w = MaskWindow.__new__(MaskWindow)
    w._tk = tk
    w._alive = True
    w._px_per_pt = 1.0
    w._bw, w._bh = 200, 200
    w._bx, w._by = 0, 0
    w._last_img = None
    w.root = tk.Toplevel(root)
    w.root.withdraw()
    w._cv = tk.Canvas(w.root, bg="#ffffff")
    w._cache = SimpleNamespace()      # 渲染路径不碰缓存，占位即可
    w._renderer = MaskRenderer(w)
    return w, root


def _five_line_ocr(y0_start=32, line_h=20, gap=10):
    """5 行等行高等间距行框：y0 = 32, 62, 92, 122, 152（避开 BAR_H=22 顶栏）。"""
    ys = [y0_start + i * (line_h + gap) for i in range(5)]
    lines = ["aaaa", "bbbb", "cccc", "dddd", "eeee"]
    line_boxes = [
        [[(10, y0), (150, y0), (150, y0 + line_h), (10, y0 + line_h)]]
        for y0 in ys
    ]
    rec = SimpleNamespace(
        text="aaaa\nbbbb\ncccc\ndddd\neeee",
        lines=lines, line_boxes=line_boxes, para_ids=[0] * 5)
    return rec, ys, line_h


def test_layout_line_positions_match_ocr_boxes():
    """每行渲染 y 必须等于原 OCR 行框垂直中心 - BAR_H。"""
    rec, ys, line_h = _five_line_ocr()
    w, root = _make_renderable()
    w._render(rec, rec.text)          # 原样排版：译文 = OCR 原文
    texts = [w._cv.itemcget(i, "text") for i in w._cv.find_all()]
    assert sum(1 for t in texts if t) >= 5, "5 行原文应全部渲染"
    items = sorted((i for i in w._cv.find_all() if w._cv.itemcget(i, "text")),
                   key=lambda i: w._cv.coords(i)[1])
    expected = [y0 + line_h / 2 - BAR_H for y0 in ys]
    got = [w._cv.coords(i)[1] for i in items]
    for g, e in zip(got, expected):
        assert abs(g - e) < 2.0, f"行位偏移：渲染 y={g:.1f} vs 原行中心 {e:.1f}"
    root.destroy()


def test_layout_preserves_line_gaps():
    """渲染相邻行距必须等于原始行框间距（gap=10 + line_h=20 → 30px），不放大。"""
    rec, ys, line_h = _five_line_ocr()
    w, root = _make_renderable()
    w._render(rec, rec.text)
    items = sorted((i for i in w._cv.find_all() if w._cv.itemcget(i, "text")),
                   key=lambda i: w._cv.coords(i)[1])
    got = [w._cv.coords(i)[1] for i in items]
    gaps = [got[i + 1] - got[i] for i in range(len(got) - 1)]
    for g in gaps:
        assert abs(g - (line_h + 10)) < 3.0, f"行距失真：{g:.1f} vs 原始 {line_h + 10}"
    root.destroy()


def test_layout_preserves_paragraph_gap():
    """段间大空隙必须保留：第 3 行 gap 拉大到 50，渲染行距应同步变大。"""
    rec, ys, line_h = _five_line_ocr()
    # 第 4 行 y0 从 122 → 142（gap 10 → 30+20=50 间距）：重建行框
    ys2 = [32, 62, 92, 162, 192]      # 92→162 间距 70（行高20+空隙50）
    rec.line_boxes = [
        [[(10, y0), (150, y0), (150, y0 + line_h), (10, y0 + line_h)]]
        for y0 in ys2
    ]
    w, root = _make_renderable()
    w._render(rec, rec.text)
    items = sorted((i for i in w._cv.find_all() if w._cv.itemcget(i, "text")),
                   key=lambda i: w._cv.coords(i)[1])
    got = [w._cv.coords(i)[1] for i in items]
    assert len(got) == 5
    gaps = [got[i + 1] - got[i] for i in range(len(got) - 1)]
    assert abs(gaps[0] - 30) < 3 and abs(gaps[1] - 30) < 3, f"段内行距失真: {gaps}"
    assert abs(gaps[2] - 70) < 3, f"段间空隙未保留: gaps={gaps}（第3→4行应 70px）"
    assert abs(gaps[3] - 30) < 3, f"段内行距失真: {gaps}"
    root.destroy()


def test_fs_cap_uses_median_height_not_outlier():
    """行高中位数统一上限：虚高框（40px）不得把字号上限拉大。

    常规行 h=19×4 + 虚高行 h=40：cap 应由中位数 19 主导而非 40。
    pp=1、avg=200/5=40 → 旧算法(40 直驱) 40，新算法 min(19, 40×1.3)=19。
    """
    cap = _global_fs_cap([19, 19, 19, 19, 40], 40, 1.0)
    assert cap == 19, f"字号上限应取行高中位数（19），实际 {cap}"


def test_render_font_size_uniform_despite_tall_box():
    """Canvas 端到端：小幅虚高行（h=24，1.26× 正文，未跨断崖阈值）不放大，全行字号一致。

    注：大幅虚高(≥1.6× 正文)会触发标题断崖规则被当"真大号字"放行放大——那是预期
    层级保留，由 test_render_title_line_larger_than_uniform_body 覆盖。
    """
    w, root = _make_renderable()
    ys = [32, 62, 92, 122]            # 前 3 行高 20，第 4 行小幅虚高 24
    line_h = [20, 20, 20, 24]
    rec = SimpleNamespace(
        text="aaaa\nbbbb\ncccc\ndddd",
        lines=["aaaa", "bbbb", "cccc", "dddd"],
        line_boxes=[
            [[(10, y0), (150, y0), (150, y0 + h), (10, y0 + h)]]
            for y0, h in zip(ys, line_h)
        ],
        para_ids=[0] * 4,
    )
    w._bh = 220
    w._render(rec, rec.text)
    items = [i for i in w._cv.find_all() if w._cv.itemcget(i, "text")]
    assert len(items) == 4
    sizes = set()
    for i in items:
        f = w._cv.itemcget(i, "font") or ""
        m = re.search(r"\s(\d+)\s", f" {f} ")
        sizes.add(int(m.group(1)) if m else -1)
    assert len(sizes) == 1, f"小幅虚高行不得放大字号，各行为应一致，实际 {sizes}"
    root.destroy()


def test_line_fs_cap_title_gets_larger_than_body():
    """断崖式大号行（真标题 h=32 vs 正文 19，≥1.6×）按自身行高放大，保留层级。"""
    body_cap = _global_fs_cap([19, 19, 19, 32], 40, 1.0)     # 正文统一上限=19
    assert body_cap == 19
    title_cap = _line_fs_cap(32, 19, body_cap, 1.0)           # 32 ≥ 19×1.6=30.4
    assert title_cap > body_cap, f"标题行应放大：{title_cap} vs 正文 {body_cap}"
    assert title_cap == 27                                    # round(32×0.85)


def test_line_fs_cap_inflated_box_stays_uniform():
    """OCR 虚高 padding（h=24，仅 1.26× 正文）不跨断崖阈值 → 仍用统一上限。"""
    body_cap = _global_fs_cap([19, 19, 19, 24], 40, 1.0)
    assert body_cap == 19
    assert _line_fs_cap(24, 19, body_cap, 1.0) == 19, "小幅虚高不得放行放大"


def test_render_title_line_larger_than_uniform_body():
    """Canvas 端到端：标题行(高 40)字号 > 正文两行(高 19)，且正文两行字号一致。"""
    w, root = _make_renderable()
    ys = [32, 62, 120]                 # 正文 32/62，标题 120（避让大标题框）
    heights = [19, 19, 40]
    rec = SimpleNamespace(
        text="body one\nbody two\ntitle",
        lines=["body one", "body two", "title"],
        line_boxes=[
            [[(10, y0), (150, y0), (150, y0 + h), (10, y0 + h)]]
            for y0, h in zip(ys, heights)
        ],
        para_ids=[0] * 3,
    )
    w._bh = 220
    w._render(rec, rec.text)
    items = [i for i in w._cv.find_all() if w._cv.itemcget(i, "text")]
    assert len(items) == 3
    sizes = []
    for i in sorted(items, key=lambda i: w._cv.coords(i)[1]):
        f = w._cv.itemcget(i, "font") or ""
        m = re.search(r"\s(\d+)\s", f" {f} ")
        sizes.append(int(m.group(1)) if m else -1)
    body_fs, body_fs2, title_fs = sizes
    assert body_fs == body_fs2, f"正文两行字号应一致：{sizes}"
    assert title_fs > body_fs, f"标题行字号应大于正文：{sizes}"
    root.destroy()


def test_render_bold_line_gets_bold_font():
    """加粗/黑体行（墨占比高）译文渲染 bold，细体行不 bold（墨密度相对断崖）。"""
    from PIL import Image, ImageDraw
    w, root = _make_renderable()
    # 源图 200x130：三行 20px 高条带（y 32-52 / 62-82 / 92-112）
    img = Image.new("L", (200, 130), 255)
    d = ImageDraw.Draw(img)
    # 细体两行：每 10px 一条 1px 竖线（墨占比 ~0.1）
    for y0 in (32, 62):
        for x in range(10, 150, 10):
            d.rectangle([x, y0, x, y0 + 19], fill=0)
    # 加粗行：左半块实心黑（墨占比 ~0.5，远超细体）
    d.rectangle([10, 92, 84, 111], fill=0)

    heights = [20, 20, 20]
    rec = SimpleNamespace(
        text="thin one\nthin two\nbold line",
        lines=["thin one", "thin two", "bold line"],
        line_boxes=[
            [[(10, y0), (150, y0), (150, y0 + h), (10, y0 + h)]]
            for y0, h in zip((32, 62, 92), heights)
        ],
        para_ids=[0] * 3,
    )
    w._render(rec, rec.text, img)
    items = [i for i in w._cv.find_all() if w._cv.itemcget(i, "text")]
    assert len(items) == 3
    fonts = []
    for i in sorted(items, key=lambda i: w._cv.coords(i)[1]):
        f = (w._cv.itemcget(i, "font") or "").lower()
        fonts.append("bold" in f)
    assert fonts[0] is False and fonts[1] is False, f"细体行不应加粗：{fonts}"
    assert fonts[2] is True, f"黑体行译文应 bold：{fonts}"
    root.destroy()
