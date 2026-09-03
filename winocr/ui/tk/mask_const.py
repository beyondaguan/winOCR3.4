# -*- coding: utf-8 -*-
"""蒙版翻译浮层共享常量。

集中放置，避免 mask_window / mask_render / mask_segment 三个模块互相 import 形成
循环依赖（本模块只依赖 theme，不依赖任何兄弟模块）。
"""
from . import theme

BAR_H = 22                    # 标题栏高度（像素）
# 蒙版透明度：用户 2026-09-02 下午要求调回半透明（0.6）。注意 Tk 整窗 alpha
# 是"底色+文字"一起半透；白底半透明会呈浅灰雾面。要深色半透明观感只需把
# MASK_BG/MASK_FG/MASK_BAR_BG 改回 #1e1e1e/#f2f2f2/#2d2d2d。
MASK_ALPHA = 0.6
FONT_FLOOR = 6              # 极端小行(噪音/碎片)的安全字号下限，正常文档不触发
FONT_FAMILY = theme.FONT_FAMILY

# ---- 蒙版配色（白底遮蔽档；改这三个值即可切深色/其他主题）----
MASK_BG = "#ffffff"         # 译文底色：不透明白，完全遮蔽原文
MASK_FG = "#1e1e1e"         # 译文文字色：深色
MASK_BAR_BG = "#f0f0f0"     # 标题栏底色：浅灰，与白底协调

# 段间分隔符标点（中英文都包括）——译文分行时在标点处优先断句
_SENT_PUNCT = set("。.！!？?，,；;、:：""''""'')(")

# ---- 点阵投影分割（LineSegmenter，见 mask_segment.py）----
_BINARIZE_THRESHOLD = 128    # 灰度二值化阈值：< 阈值视为"有墨"
_INK_GAP_RATIO = 0.25       # 列墨数相对阈值：低于「中位列墨 × 该比例」判为字符间隙
_MIN_BOX_WIDTH_RATIO = 0.15 # 字符小框最小宽度 = 行高 × 该比例，过滤噪声窄段
