# -*- coding: utf-8 -*-
"""蒙版翻译的 Canvas 渲染层（MaskRenderer）。

职责：
- 译文按 OCR 原行整行锚定回填（左缘对齐 + 垂直居中，原地覆盖）
- 字号自适应（铺满行宽 ↔ OCR 行高 1:1 上限 取小）、分行排版
- 注：点阵逐字栅格展开已废弃——英→中译文长度远短于原文字符数，逐字沿原字符
  中心插值会把中文摊满整行宽、拉出巨大字距（用户截图实证，3.4.20「刷新渲染修复」）；
  点阵纯函数保留在 mask_segment.py（不再被渲染调用）

不持有窗口生命周期/事件，只读取 MaskWindow 提供的画布与几何状态（self._w）。
"""
from __future__ import annotations

import logging

from .mask_const import BAR_H, FONT_FLOOR, FONT_FAMILY, MASK_FG
from .mask_segment import line_ink_ratio, _BOLD_INK_FACTOR, _BOLD_MIN_MEDIAN

logger = logging.getLogger(__name__)


def _est_char_em(text: str) -> float:
    """估算文本在 Tk 默认字体下的平均字宽（单位 em），用于『一行能放多少字』。

    CJK / 假名 / 韩文 / 全角 ≈ 1.0em；空格 ≈ 0.5em；Latin / 数字 / 半角标点 ≈ 0.55em。
    仅用于自适应字号估算，不要求像素级精确。ASCII 走快路径，跳过 4 次宽字符范围判断。
    """
    if not text:
        return 0.0
    total = 0.0
    for ch in text:
        if ch in " 　 ":          # 空格（半角/全角/NBSP）
            total += 0.5
            continue
        o = ord(ch)
        if o < 0x80:              # ASCII：快路径
            total += 0.55
            continue
        if (0x4E00 <= o <= 0x9FFF) or (0x3040 <= o <= 0x30FF) or \
           (0xAC00 <= o <= 0xD7AF) or (0xFF00 <= o <= 0xFFEF):
            total += 1.0          # CJK / 假名 / 韩文 / 全角
        else:
            total += 0.55
    return total


def _fit_font_size(text: str, max_w: float, pp: float, fs_cap: int) -> int:
    """逐行自适应字号：取『铺满可用宽度』与『上限字号』的较小者，且不低于地板。

    - 长译文：铺满宽度字号 < 上限 → 按宽度排版（不空旷、不溢出）。
    - 短译文：铺满宽度字号 > 上限 → 封顶到上限（不巨大，保持与原字大小协调）。
    """
    em = _est_char_em(text)
    if em <= 0:
        return FONT_FLOOR
    fs_w = max_w / em
    fs = int(min(fs_w, fs_cap))
    return max(FONT_FLOOR, fs)


def _median_height(geom_heights):
    """行高中位数（抗离群：OCR 虚高框/真标题都不拉偏正文基准）。"""
    heights = sorted(geom_heights)
    return heights[len(heights) // 2] if heights else 1


def _global_fs_cap(geom_heights, avg_line_h, pp):
    """正文统一字号上限 = 行高中位数（抗单行 OCR 框虚高）。

    逐行用"自己的行框高"作上限时，检测框虚高/含 padding 的行会把字号带飞，
    造成行与行字号跳变巨大（OCR 原文与译文同病，用户实证）。改用全选区行高
    中位数统一封顶：字号只随"该行文字多寡"由宽度平滑决定，不再被单行带偏。
    """
    base_h = _median_height(geom_heights)
    return max(FONT_FLOOR, min(int(round(base_h / pp)),
                               int(round(avg_line_h / pp * 1.3))))


# 标题/大号字断崖阈值：行高 ≥ 正文中位数 × 该倍数 才视为"真大号字行"。
# OCR 虚高 padding 一般只把行高抬 1.1~1.4×，不跨此阈值 → 不会误放行；
# 真标题（如正文 19px / 标题 32px ≈1.7×）跨阈值 → 按自身行高放大，保留层级。
_TITLE_GAP_RATIO = 1.6
_TITLE_FIT = 0.85          # 大号行字号按其行高的 0.85（检测框含 padding，略收）

def _line_fs_cap(h, base_h, global_cap, pp):
    """单行字号上限：正文行用全局统一 cap；断崖式的大号行按自身行高放大。"""
    if base_h and h >= base_h * _TITLE_GAP_RATIO:
        return max(global_cap, int(round(h * _TITLE_FIT / pp)))
    return global_cap


def _compute_line_geom(lines, line_boxes):
    """返回 [{h, gap_before, y0, y1}, ...]"""
    geom = []
    prev_y1 = None
    for i, boxes in enumerate(line_boxes):
        if not boxes:
            h = 20
            y0 = (prev_y1 or 0) + 4
            y1 = y0 + h
        else:
            ys = [p[1] for b in boxes for p in b]
            y0, y1 = min(ys), max(ys)
            h = max(y1 - y0, 12)
        gap = 0 if prev_y1 is None else (y0 - prev_y1)
        geom.append({"h": h, "gap_before": gap, "y0": y0, "y1": y1})
        prev_y1 = y1
    return geom


def _distribute_translation(text, n):
    """把译文分到 n 行。优先按已有 \\n，若不够则字符数均分（首句断点优先标点）。"""
    if n <= 0:
        return [text or ""]
    text = (text or "").strip()
    if not text:
        return [""] * n
    if n == 1:
        return [text]

    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if len(parts) == n:
        return parts

    if len(parts) < n:
        return _split_to_n(parts, n)
    return _merge_to_n(parts, n)


def _split_to_n(parts, n):
    """parts 行数 < n：把每段按字符数切成多个，目标 ~总字符/n。优先在标点断"""
    total = sum(len(p) for p in parts)
    if total == 0:
        return [""] * n
    per = total / n
    out = []
    for p in parts:
        if len(p) <= per * 1.4:
            out.append(p)
            continue
        rest = p
        while len(rest) > per * 1.4 and len(out) < n - 1:
            cut = int(per)
            cut = max(1, min(cut, len(rest) - 1))
            best = -1
            for j in range(max(0, cut - 6), min(len(rest), cut + 6)):
                if rest[j] in _SENT_PUNCT:
                    best = j + 1   # 标点后断开
                    break
            if best <= 0 or best > len(rest):
                best = cut
            out.append(rest[:best].strip())
            rest = rest[best:].strip()
        if rest:
            out.append(rest)
    while len(out) < n:
        out.append("")
    return out[:n]


def _merge_to_n(parts, n):
    """parts 行数 > n：合并较短段直到剩 n 行"""
    from .mask_const import _SENT_PUNCT
    out = list(parts)
    while len(out) > n:
        best_i = 0
        best_sum = 10 ** 9
        for i in range(len(out) - 1):
            s = len(out[i]) + len(out[i + 1])
            if s < best_sum:
                best_sum = s
                best_i = i
        joiner = "" if out[best_i + 1][:1] in _SENT_PUNCT else ""
        out[best_i] = (out[best_i] + joiner + out[best_i + 1]).strip()
        del out[best_i + 1]
    while len(out) < n:
        out.append("")
    return out[:n]


def _sanitize(text):
    if not text:
        return text
    return "".join(
        ch for ch in text
        if ch in ("\t", "\n", "\r") or ord(ch) >= 0x20
    )


class MaskRenderer:
    """Canvas 渲染器：读取 MaskWindow 的画布与几何状态，绘制译文。"""

    def __init__(self, window: "MaskWindow") -> None:
        self._w = window

    def _fs_scale(self) -> float:
        """译文字号倍率（MaskWindow 设置面板滑条）；缺省 1.0。"""
        return float(getattr(self._w, "_font_scale", 1.0) or 1.0)

    def render(self, ocr_result, translation, img=None):
        """Canvas 绝对坐标渲染：译文逐行锚定到 OCR 原行（左缘对齐 + 垂直居中，原地覆盖）。

        img：抓取的选区源图。用于墨密度分析，识别加粗/黑体行（原文强调 → 译文 bold）。
        无 line_boxes（剪贴板 / 结构化 / OCR 缺失）→ 走 _render_fallback 整块居中。
        """
        tk = self._w._tk
        try:
            if not self._w._alive or not self._w.root.winfo_exists():
                return
            tr = _sanitize(translation or "")
            cv = self._w._cv
            cv.delete("all")
            fg = MASK_FG
            pp = self._w._px_per_pt
            if img is None:
                img = self._w._last_img

            lines = getattr(ocr_result, "lines", None) if ocr_result else None
            line_boxes = getattr(ocr_result, "line_boxes", None) if ocr_result else None
            if not (lines and line_boxes and len(lines) == len(line_boxes) and len(lines) > 0):
                self._render_fallback(tr, fg)
                return

            geom = _compute_line_geom(lines, line_boxes)
            n = len(geom)
            usable_w = max(self._w._bw - 16, 1)
            avg_line_h = self._w._bh / max(n, 1)
            tr_lines = _distribute_translation(tr, n)
            # 全局统一字号上限：行高中位数（不随单行虚高框跳变，用户实证反馈）；
            # 断崖式大号行（真标题）由 _line_fs_cap 放行按自身行高放大。
            fs_cap = _global_fs_cap([g["h"] for g in geom], avg_line_h, pp)
            base_h = _median_height([g["h"] for g in geom])

            # ---- 加粗/黑体检测：逐行墨占比，相对中位数断崖（RapidOCR 不给字体属性）----
            ink_ratios, ink_med = [-1.0] * n, -1.0
            if img is not None:
                for i, g in enumerate(geom):
                    boxes = line_boxes[i]
                    if not boxes:
                        continue
                    try:
                        xs = [p[0] for b in boxes for p in b]
                        ys = [p[1] for b in boxes for p in b]
                        crop = img.crop((int(min(xs)), int(min(ys)),
                                         int(max(xs)), int(max(ys))))
                        ink_ratios[i] = line_ink_ratio(crop)
                    except Exception:
                        ink_ratios[i] = -1.0
                pos = sorted(r for r in ink_ratios if r >= 0)
                if pos:
                    ink_med = pos[len(pos) // 2]

            for i, g in enumerate(geom):
                line_txt = tr_lines[i] if i < len(tr_lines) else ""
                boxes = line_boxes[i]
                if not line_txt or not boxes:
                    continue
                # 原行外框（图像坐标 = 选区局部坐标）。逐行独立容错：
                # 某行框为空/坏框只跳过该行，绝不让单行异常坠入全局回退
                # （旧版整个 try 共用一个 except，一行坏框 → min([]) 抛
                #   ValueError → 全版译文缩成左上角 6pt 小字堆，用户截图实证）。
                try:
                    xs = [p[0] for b in boxes for p in b]
                    ys = [p[1] for b in boxes for p in b]
                    lx0 = min(xs)
                    ly0, ly1 = min(ys), max(ys)
                except (ValueError, TypeError):
                    logger.debug("蒙版渲染：第 %d 行框异常，跳过该行", i, exc_info=True)
                    continue
                fs = _fit_font_size(line_txt, usable_w, pp,
                                    _line_fs_cap(g["h"], base_h, fs_cap, pp))
                fs = max(FONT_FLOOR, int(fs * self._fs_scale()))   # 设置面板字号倍率
                cy = (ly0 + ly1) / 2.0 - BAR_H     # Canvas 在 bar 之下，y 偏移 BAR_H
                # 加粗行 → 译文 bold（墨占比 ≥ 中位 × 1.5 且中位足够亮，见 mask_segment）
                is_bold = (ink_med >= _BOLD_MIN_MEDIAN
                           and ink_ratios[i] >= ink_med * _BOLD_INK_FACTOR)
                line_font = (FONT_FAMILY, fs, "bold") if is_bold else (FONT_FAMILY, fs)
                # 整行整体回填：左对齐原行左缘、垂直居中原行（原地覆盖）。
                # 不再做逐字栅格展开——英→中译文长度远短于原文字符数，逐字沿
                # 原字符中心插值会把中文摊满整行宽，拉出巨大字距（用户截图实证）。
                cv.create_text(lx0, cy, text=line_txt,
                               font=line_font,
                               fill=fg, anchor="w")

            # 高度钉死：蒙版窗口 = 框选区域大小，Canvas 内容超出由 Tk 默认裁剪。
            # 严禁把 h 撑出去——否则下一次 _grab_region 会用撑大的 bbox 抓更大区域，
            # OCR 检测框 padding 让窗口越撑越大（用户诉求："蒙版框选多大，出来就多大"）。
            self._w.root.geometry(f"{self._w._bw}x{self._w._bh}+{self._w._bx}+{self._w._by}")
            self._w.root.update()
        except Exception as e:
            logger.debug("蒙版渲染失败回退: %s", e)
            try:
                cv = self._w._cv
                cv.delete("all")
                tr2 = _sanitize(translation or "")
                # 紧急回退也用自适应字号，不再用 FONT_FLOOR（6pt 小字堆不可读）
                fb_cap = max(FONT_FLOOR, int(self._w._bh
                             / max(tr2.count("\n") + 1, 1) / self._w._px_per_pt))
                fb_fs = _fit_font_size(tr2, max(self._w._bw - 16, 1),
                                       self._w._px_per_pt, fb_cap)
                fb_fs = max(FONT_FLOOR, int(fb_fs * self._fs_scale()))
                cv.create_text(8, 8, text=tr2,
                               font=(FONT_FAMILY, fb_fs), fill=MASK_FG,
                               anchor="nw", width=self._w._bw - 16)
            except Exception:
                pass

    def _render_fallback(self, tr, fg):
        """无 line_boxes（剪贴板 / 结构化 / OCR 缺失）的回退：整块居中、自适应字号。"""
        try:
            bw = self._w._bw
            bh = self._w._bh
            pp = self._w._px_per_pt
            usable_w = max(bw - 16, 1)
            fb_cap = max(FONT_FLOOR, int(bh / max(tr.count('\n') + 1, 1) / pp))
            fb_fs = _fit_font_size(tr, usable_w, pp, fb_cap)
            fb_fs = max(FONT_FLOOR, int(fb_fs * self._fs_scale()))
            lines = (tr or "").split('\n') if tr else [""]
            y = 6
            cv = self._w._cv
            for ln in lines:
                if not ln:
                    y += int(fb_fs * 1.0)
                    continue
                cv.create_text(8, y, text=ln, font=(FONT_FAMILY, fb_fs),
                               fill=fg, anchor="nw", width=bw - 16)
                y += int(fb_fs * 1.4)
            # 钉死：蒙版窗口 = 选区大小（不允许扩窗）。
            self._w.root.geometry(f"{bw}x{bh}+{self._w._bx}+{self._w._by}")
            self._w.root.update()
        except Exception:
            logger.debug("蒙版 fallback 渲染失败", exc_info=True)
