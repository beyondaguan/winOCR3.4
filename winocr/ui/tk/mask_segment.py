# -*- coding: utf-8 -*-
"""点阵分析（LineSegmenter）：对单行裁剪图做水平投影切字符小框，并做译文回填映射。

纯函数、零业务依赖。列墨统计用 NumPy 向量化（OCR 栈已硬依赖 numpy，速度快 10-50×）。
便于单测。
"""
from __future__ import annotations

import logging

import numpy as np

from .mask_const import (
    _BINARIZE_THRESHOLD,
    _INK_GAP_RATIO,
    _MIN_BOX_WIDTH_RATIO,
)

logger = logging.getLogger(__name__)

# ---- 加粗/黑体判定（墨密度相对断崖）----
# RapidOCR 不返回字体属性；黑体/加粗笔画粗 → 行内墨像素占比明显高于细体正文。
# 以「全选区中位墨占比 × _BOLD_INK_FACTOR」为断崖：跨过即判加粗（抗绝对字号/字体差）；
# 中位墨占比过低（极淡/噪声）不判粗，避免把浅色背景噪点误判成黑体。
_BOLD_INK_FACTOR = 1.5
_BOLD_MIN_MEDIAN = 0.05


def line_ink_ratio(crop_img) -> float:
    """单行裁剪图墨像素占比（0~1）：黑体/加粗高、细体低。失败返回 -1（跳过判定）。"""
    try:
        gray = crop_img.convert("L")
    except Exception:
        return -1.0
    w, h = gray.size
    if w < 2 or h < 2:
        return -1.0
    arr = np.array(gray)
    return float((arr < _BINARIZE_THRESHOLD).mean())


def segment_line_boxes(crop_img):
    """点阵分析：对单行裁剪图做水平投影，切出字符小框，返回各小框中心 x（crop 局部坐标）。

    返回空列表表示投影失败（如整行空白 / 低对比 / 异常），调用方回退到整行均布。
    """
    try:
        gray = crop_img.convert("L")
    except Exception:
        logger.debug("蒙版点阵：灰度转换失败")
        return []
    w, h = gray.size
    if w < 2 or h < 2:
        return []
    # 列墨统计：每列小于阈值的像素数（向量化，替代 O(w*h) 的 Python 双层循环）
    arr = np.array(gray)
    col_ink = (arr < _BINARIZE_THRESHOLD).sum(axis=0)
    nonzero = [int(c) for c in col_ink if c > 0]
    if not nonzero:
        return []
    med = sorted(nonzero)[len(nonzero) // 2]
    thr = max(1, int(med * _INK_GAP_RATIO))     # 相对阈值：列墨数低于中位数比例视为字符间隙
    boxes = []
    start = None
    for x in range(w):
        if col_ink[x] >= thr:
            if start is None:
                start = x
        else:
            if start is not None:
                if x - 1 > start:
                    boxes.append((start, x - 1))
                start = None
    if start is not None and w - 1 > start:
        boxes.append((start, w - 1))
    min_w = max(1, int(h * _MIN_BOX_WIDTH_RATIO))     # 过滤噪声窄段
    boxes = [(a, b) for (a, b) in boxes if (b - a) >= min_w]
    return [(a + b) / 2.0 for (a, b) in boxes]


def map_chars_to_boxes(chars, centers, line_x0, line_x1):
    """把译文逐字映射到 x 坐标：有栅格时沿原小框中心插值，否则整行均布。

    长度不符时沿栅格节奏插值（保持原字位置感）。
    """
    L = len(chars)
    if L == 0:
        return []
    if len(centers) >= 2:
        K = len(centers)
        out = []
        for j in range(L):
            t = 0.5 if L == 1 else j / (L - 1)
            idx = int(round(t * (K - 1)))
            out.append(line_x0 + centers[idx])
        return out
    # 回退：整行均布
    return [line_x0 + (j + 0.5) / L * (line_x1 - line_x0) for j in range(L)]
