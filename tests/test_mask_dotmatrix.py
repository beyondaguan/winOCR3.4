# -*- coding: utf-8 -*-
"""蒙版点阵分析 + 小框自动框选 + 逐字回填：纯函数单测。

覆盖 segment_line_boxes（水平投影切小框）与 map_chars_to_boxes（译文→小框映射）。
不依赖 Tk / 渲染，纯逻辑验证。
"""
from PIL import Image, ImageDraw

from winocr.ui.tk.mask_segment import (segment_line_boxes, map_chars_to_boxes,
                                        line_ink_ratio)


def test_line_ink_ratio_black_block_high():
    """实心黑块行：墨占比应接近黑块面积比（>0.5）。"""
    img = Image.new("L", (100, 20), 255)
    ImageDraw.Draw(img).rectangle([0, 0, 99, 19], fill=0)
    assert line_ink_ratio(img) > 0.9


def test_line_ink_ratio_thin_lines_low():
    """细笔画行（1px 竖线稀疏）：墨占比应远低于实心块。"""
    img = Image.new("L", (100, 20), 255)
    d = ImageDraw.Draw(img)
    for x in range(0, 100, 4):
        d.line([(x, 0), (x, 3)], fill=0)      # 细短线
    thin = line_ink_ratio(img)
    assert 0.0 <= thin < 0.2, f"细笔划墨占比应低，实际 {thin}"


def test_line_ink_ratio_broken_input_returns_minus_one():
    assert line_ink_ratio(None) == -1.0 or line_ink_ratio(Image.new("L", (1, 1))) == -1.0


def _line_with_chars(gaps_at):
    """造一张白底黑字行图：在 xs 列画竖条模拟『字符』，其余留白模拟间隙。

    gaps_at: 需要留白的列集合（模拟字符间隙）。
    """
    w, h = 120, 20
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    for x in range(w):
        if x in gaps_at:
            continue
        d.line([(x, 0), (x, h - 1)], fill=0)
    return img


def test_segment_finds_two_boxes():
    # 中间留一大段空白 → 应切出 2 个小框（左右各一字符）
    img = _line_with_chars(set(range(55, 66)))
    centers = segment_line_boxes(img)
    assert len(centers) == 2, f"应切出 2 个小框，实际 {len(centers)}"


def test_segment_blank_returns_empty():
    # 全白图（无墨）→ 投影失败，返回空列表（调用方回退均布）
    img = Image.new("L", (120, 20), 255)
    assert segment_line_boxes(img) == []


def test_segment_filters_noise():
    # 左侧 2px 窄框（< min_w=3）应被过滤，仅右侧宽框保留 → 1 个小框
    w, h = 120, 20
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    for x in list(range(0, 2)) + list(range(58, 120)):   # 左 2px 窄块 + 右宽块
        d.line([(x, 0), (x, h - 1)], fill=0)
    centers = segment_line_boxes(img)
    assert len(centers) == 1, f"窄框应被过滤，实际 {len(centers)} 个"


def test_map_follows_grid_when_counts_match():
    # 译文长度 = 小框数 → 逐字锚定到原小框中心
    centers = [10.0, 30.0, 50.0]
    pos = map_chars_to_boxes("ABC", centers, 0, 60)
    assert pos == [10.0, 30.0, 50.0]


def test_map_interpolates_when_longer():
    # 译文更长（4 字）沿 3 栅格插值：t=0,1/3,2/3,1 → idx 0,1,1,2（相邻字同框是预期）
    centers = [10.0, 30.0, 50.0]
    pos = map_chars_to_boxes("ABCD", centers, 0, 60)
    assert pos[0] == 10.0 and pos[-1] == 50.0
    assert pos[1] <= pos[2] <= pos[3]   # 非递减且跟随栅格节奏


def test_map_even_spread_when_no_grid():
    # 无栅格（投影失败）→ 整行均布
    pos = map_chars_to_boxes("AB", [], 0, 100)
    assert pos == [25.0, 75.0], f"均布应得 [25,75]，实际 {pos}"


def test_map_empty_chars():
    assert map_chars_to_boxes("", [10, 30], 0, 60) == []
