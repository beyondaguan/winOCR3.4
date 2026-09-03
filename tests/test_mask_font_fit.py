# -*- coding: utf-8 -*-
"""蒙版翻译逐行自适应字号（纯函数，不依赖 Tk / OCR）。

验证 _fit_font_size 的核心不变量：
- 长译文：铺满宽度字号 < 上限 → 按宽度排版（不空旷、不溢出）。
- 短译文：铺满宽度字号 > 上限 → 封顶到上限（不巨大）。
- 空文本 / 无字宽：落到地板字号，绝不抛错。
"""
from winocr.ui.tk.mask_render import _est_char_em, _fit_font_size, FONT_FLOOR


def test_est_char_em_cjk():
    assert _est_char_em("你好世界") == 4.0


def test_est_char_em_mixed():
    # 2 CJK(1.0) + 3 Latin(0.55) = 2 + 1.65 = 3.65
    assert abs(_est_char_em("你好abc") - 3.65) < 1e-9


def test_est_char_em_empty():
    assert _est_char_em("") == 0.0


def test_fit_short_capped():
    # 2 字 + 宽100 → fs_w=50；上限30 → 封顶30（不巨大）
    assert _fit_font_size("你好", 100, 1.3, 30) == 30


def test_fit_long_floored():
    # 100 字 + 宽100 → fs_w=1；下限=地板（避免溢出/负）
    assert _fit_font_size("字" * 100, 100, 1.3, 200) == FONT_FLOOR


def test_fit_fills_width():
    # 10 字 + 宽200 → fs_w=20；上限200 → 取20（铺满宽度）
    assert _fit_font_size("字" * 10, 200, 1.3, 200) == 20


def test_fit_empty_returns_floor():
    assert _fit_font_size("", 200, 1.3, 200) == FONT_FLOOR
