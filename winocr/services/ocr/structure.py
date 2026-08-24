# -*- coding: utf-8 -*-
"""几何版面分析 / 表格重建（离线，零额外模型）。

核心思路（依据真实 RapidOCR 检测框调试确定）：
  RapidOCR 对表格截图返回的框是「行级」且有两个固有毛病：
    1) 会把相邻单元格合并成一个框（如 `NSAIDs(如依托考昔)|依托考昔120 mg`，
       横跨药物+剂量两列，中间是表格边框读出的 `|`/`Ⅰ`/`1`/`一` 伪影字符）；
    2) 每个格子的文本常带「行首边框伪影」（`|标准剂量`、`Img，每日1-3次`、
       `1发作36`、`一适用于…`）。

  因此重建流程：
    1. **清伪影 + 拆合并框**（split_and_clean_items）：
       剥离行首 `| 1 I l 一 ! Ⅰ )` 等边框字符；中段 `| Ⅰ !` 是列分隔 → 拆成
       多个片段，各自按字符宽度比例估出子框；
    2. **物理行粗分组**（仅用于分块与定行）；
    3. **版面分块**：连续多列 → 表格区，单列 → 纯文本区；
    4. 表格区内：
       a. **表头定列**：若首行是干净表头（短、与下行有明显纵向间隙），
          用表头各格子的 left 当列锚，所有数据格按「left 距最近列锚」归列 ——
          比按 left 聚类鲁棒得多（真实备注列 left 分布 419~548 分散，聚类必拆列）；
       b. 行 = 后续每个物理行，换行续行独立成行（与 HushSnap 同风格）；
       c. 跨行合并单元格向下填充（仅当包围框完整覆盖目标行）；
       d. 无干净表头时回退「锚列法」（旧逻辑，兼容无表头表格）。

返回 str（重建后的 Markdown / 文本），或 None（输入为空）。
"""
from __future__ import annotations

from typing import Dict, List, Optional


# ----------------------------------------------------------------------
# 文本清理 / 合并框拆分（边框伪影）
# ----------------------------------------------------------------------
def _is_cjk(ch: str) -> bool:
    return (
        "\u4e00" <= ch <= "\u9fff"
        or "\u3400" <= ch <= "\u4dbf"
        or "\uf900" <= ch <= "\ufaff"
    )


_BORDER_HARD = set("|Ⅰ!")          # 一定出现在列边界（伪影/合并框分隔）
_BORDER_SOFT = set("1Il一")        # 需结合上下文判断


def clean_text(text: Optional[str]) -> str:
    """剥离行首边框伪影字符 + 把中段列分隔伪影换成空格。"""
    if not text:
        return ""
    s = text
    while s:
        ch = s[0]
        if ch in "|lI!Ⅰ) ":
            s = s[1:]
            continue
        if ch == "一" and len(s) > 1 and _is_cjk(s[1]):
            # “一适用于/一标准剂量” 的 一 是边框；“一、急性期” 的 一 是真序号
            s = s[1:]
            continue
        if ch == "1" and len(s) > 1:
            nxt = s[1]
            # “1发作36 / 1 0.5 mg” 的 1 是边框；数字“1.0”“1mg”保留
            if _is_cjk(nxt) or nxt == " ":
                s = s[1:]
                continue
        break
    out = []
    for ch in s:
        out.append(" " if ch in _BORDER_HARD else ch)
    return "".join(out).strip()


def _find_cuts(raw: str) -> List[int]:
    """在**原始文本**上找出列分隔位置（边框伪影字符）。

    只在原始文本上找，因为 `Ⅰ`/`|` 等一旦被 clean_text 换成空格就找不到了。
    软分隔（`1 I l 一`）要求**前一个是空格、后一个是中文**，避免误拆
    「每日1次」「1小时后」。
    """
    cuts: List[int] = []
    n = len(raw)
    for i, ch in enumerate(raw):
        if not (0 < i < n - 1):
            continue
        if ch in _BORDER_HARD:
            cuts.append(i)
        elif ch in _BORDER_SOFT:
            prev, nxt = raw[i - 1], raw[i + 1]
            if prev in " 　" and _is_cjk(nxt):
                cuts.append(i)
    return cuts


def split_and_clean_items(items) -> List[tuple]:
    """清洗文本并按边框伪影拆分合并框，返回 (box, text, score) 列表。

    拆出的每个片段按其字符宽度比例估算一个子包围框（CJK 记 2、ASCII 记 1），
    让片段能按 left 正确归列；每个片段再各自清一次行首伪影。
    空文本项也保留——它的包围框参与行/列判定（如跨行合并表的空行）。
    """
    out: List[tuple] = []
    for box, text, score in items:
        if not box:
            continue
        raw = str(text or "")
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except Exception:
            out.append((box, clean_text(raw), score))
            continue
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        w = max(1.0, right - left)

        cuts = _find_cuts(raw)
        if not cuts:
            out.append((box, clean_text(raw), score))
            continue

        cum = [0.0]
        for ch in raw:
            cum.append(cum[-1] + (2.0 if _is_cjk(ch) else 1.0))
        total = cum[-1] or 1.0
        start = 0
        for cut in cuts + [len(raw)]:
            seg = raw[start:cut]
            cleaned = clean_text(seg)
            if cleaned:
                x0 = left + (cum[start] / total) * w
                x1 = left + (cum[cut] / total) * w
                new_box = [[x0, top], [x1, top], [x1, bottom], [x0, bottom]]
                out.append((new_box, cleaned, score))
            start = cut
    return out


# ----------------------------------------------------------------------
# 基础几何
# ----------------------------------------------------------------------
def _median(xs: List[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _cluster_columns(cxs: List[float], tol: float) -> List[float]:
    """把一组 x 按 tol 间距聚成列，返回每列中心（升序）。"""
    cxs = sorted(cxs)
    groups: List[List[float]] = []
    for c in cxs:
        if groups and (c - groups[-1][-1]) <= tol:
            groups[-1].append(c)
        else:
            groups.append([c])
    return [sum(g) / len(g) for g in groups]


def _column_index(x: float, centers: List[float]) -> int:
    return min(range(len(centers)), key=lambda k: abs(centers[k] - x))


def _column_bands(anchors: List[float]) -> List[tuple]:
    """由列锚点算出每个列的水平「带」：相邻锚点的中垂线为界，
    首/末列向外侧无限延伸。用于按归属带而非最近中心归列。"""
    n = len(anchors)
    bands: List[tuple] = []
    for k in range(n):
        lo = anchors[k] - ((anchors[k] - anchors[k - 1]) / 2.0 if k > 0 else 1e9)
        hi = anchors[k] + ((anchors[k + 1] - anchors[k]) / 2.0 if k + 1 < n else 1e9)
        bands.append((lo, hi))
    return bands


def _assign_column(c: Dict, anchors: List[float], bands: List[tuple]) -> int:
    """把格子归到哪一列：优先看中心落在哪个列带；否则取与该列带水平重叠
    最大者；最后回退最近中心。比纯最近中心更抗「宽框 / 偏移列」误归。"""
    cx = c["cx"]
    for k, (lo, hi) in enumerate(bands):
        if lo <= cx < hi:
            return k
    best, best_ov = None, -1.0
    for k, (lo, hi) in enumerate(bands):
        ov = min(hi, c["right"]) - max(lo, c["left"])
        if ov > best_ov:
            best_ov, best = ov, k
    if best is not None:
        return best
    return _column_index(c["left"], anchors)


def _merge_close(xs: List[float], tol: float) -> List[float]:
    """把间距 <= tol 的相邻锚点合并为平均值，避免近乎重合的列锚产生幽灵列。"""
    if not xs:
        return []
    out = [xs[0]]
    for x in xs[1:]:
        if x - out[-1] <= tol:
            out[-1] = (out[-1] + x) / 2.0
        else:
            out.append(x)
    return out


def _parse_items(items) -> List[Dict]:
    """把 [(box, text, score), ...] 解析为带几何的 cell 列表。"""
    cells: List[Dict] = []
    for box, text, _score in items:
        if not box:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except Exception:
            continue
        if not xs or not ys:
            continue
        clean = (text or "").replace("|", " ").strip()
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        cells.append({
            "text": clean,
            "cx": (left + right) / 2.0,
            "left": left, "right": right,
            "top": top, "bottom": bottom,
            "w": right - left,
            "h": bottom - top,
        })
    return cells


def _physical_lines(cells: List[Dict], thr: float) -> List[List[Dict]]:
    """按 top-y 做物理行粗分组（仅用于分块，不决定最终行）。"""
    if not cells:
        return []
    s = sorted(cells, key=lambda c: (c["top"], c["cx"]))
    lines: List[List[Dict]] = [[s[0]]]
    cur_top = s[0]["top"]
    for c in s[1:]:
        if c["top"] <= cur_top + thr:
            lines[-1].append(c)
        else:
            lines.append([c])
            cur_top = c["top"]
    for ln in lines:
        ln.sort(key=lambda c: c["cx"])
    return lines


def _line_col_count(line: List[Dict], tol: float) -> int:
    if not line:
        return 0
    return len(_cluster_columns([c["left"] for c in line], tol))


def _segment_blocks(lines: List[List[Dict]], tol: float) -> List[Dict]:
    """物理行 → [表格区 | 纯文本区] 区块。"""
    blocks: List[Dict] = []
    cur: Optional[Dict] = None

    def _new(kind: str, ln: List[Dict]) -> Dict:
        return {"kind": kind, "lines": [ln]}

    for ln in lines:
        if _line_col_count(ln, tol) >= 2:        # 多列 → 表
            if cur is None or cur["kind"] != "table":
                if cur is not None:
                    blocks.append(cur)
                cur = _new("table", ln)
            else:
                cur["lines"].append(ln)
        else:                                     # 单列 → 文本
            if cur is None or cur["kind"] != "plain":
                if cur is not None:
                    blocks.append(cur)
                cur = _new("plain", ln)
            else:
                cur["lines"].append(ln)

    if cur is not None:
        blocks.append(cur)

    # 不足 2 行的「表」降级为纯文本（单行多列不是真正的表）
    for b in blocks:
        if b["kind"] == "table" and len(b["lines"]) < 2:
            b["kind"] = "plain"
    return blocks


# ----------------------------------------------------------------------
# 表格渲染
# ----------------------------------------------------------------------
def _detect_header(lines: List[List[Dict]], med_h: float) -> Optional[List[Dict]]:
    """首行是否为「干净表头」。

    两种判据（满足其一即认作表头）：
      1) 与下一行有明显纵向间隙（传统大间距表头）；
      2) 无间隙但表头行明显比数据行矮（紧凑小字表头，如字号更小的列名）。
    均要求首行至少 2 列、且单个格子不过高（排除把标题行当表头）。
    """
    if len(lines) < 2:
        return None
    first, second = lines[0], lines[1]
    if not first or not second or len(first) < 2:
        return None
    if any(c["h"] > med_h * 1.8 for c in first):
        return None
    b0 = max(c["bottom"] for c in first)
    t1 = min(c["top"] for c in second)
    gap = t1 - b0
    # 判据 1：明显间隙
    if gap > max(4.0, med_h * 0.6):
        return first
    # 判据 2：紧凑但表头行显著矮于数据行
    if gap >= -med_h * 0.2:
        h1 = _median([c["h"] for c in first])
        h2 = _median([c["h"] for c in second])
        if h1 <= h2 * 0.85 and h1 <= med_h * 1.2:
            return first
    return None


def _render_header_based(lines: List[List[Dict]], header: List[Dict]) -> List[str]:
    """表头定列 + 物理行当行。

    列锚点 = 表头 left ∪ 首数据行 left（表头漏列时也能对齐出全部数据列），
    近似重合的锚点经 _merge_close 合并，避免幽灵列。
    """
    hdr_src = [c for c in header if c["text"]]
    # 首数据行 left 也并入锚集：表头若比数据少一列，数据列仍能被正确标出
    first_data = lines[1] if len(lines) > 1 else []
    candidates = [c["left"] for c in hdr_src] + \
                 [c["left"] for c in first_data if c["text"]]
    anchors = _merge_close(sorted(set(round(x, 1) for x in candidates)), tol=14.0)
    if len(anchors) < 2:
        # 表头列数不足 → 回退锚列法
        return _render_anchor_based([c for ln in lines for c in ln])
    bands = _column_bands(anchors)
    ncol = len(anchors)

    hdr_row = ["" for _ in range(ncol)]
    for c in hdr_src:
        j = _assign_column(c, anchors, bands)
        if not hdr_row[j]:
            hdr_row[j] = c["text"]

    data_lines = lines[1:]
    rows = []
    for ln in data_lines:
        rows.append({
            "top": min(c["top"] for c in ln),
            "bottom": max(c["bottom"] for c in ln),
            "cells": list(ln),
        })
    M = len(rows)
    matrix: List[List[str]] = [["" for _ in range(ncol)] for _ in range(M)]
    span: List[List[Optional[tuple]]] = [[None for _ in range(ncol)] for _ in range(M)]
    for i, r in enumerate(rows):
        for c in r["cells"]:
            j = _assign_column(c, anchors, bands)
            if matrix[i][j]:
                matrix[i][j] += " " + c["text"]
            else:
                matrix[i][j] = c["text"]
                span[i][j] = (c["top"], c["bottom"])

    # 跨行合并单元格向下填充（仅当该格包围框完整覆盖目标行，避免续行文字被误填）
    for j in range(ncol):
        for i in range(M):
            if matrix[i][j] and span[i][j]:
                t, b = span[i][j]
                for k in range(i + 1, M):
                    if matrix[k][j]:
                        break
                    rt, rb = rows[k]["top"], rows[k]["bottom"]
                    if t <= rt + 2 and b >= rb - 2:
                        matrix[k][j] = matrix[i][j]
                    else:
                        break

    # 丢弃全空列
    nonempty = [j for j in range(ncol)
                if hdr_row[j] or any(matrix[i][j] for i in range(M))]
    if not nonempty:
        return _render_anchor_based([c for ln in lines for c in ln])
    hdr_row = [hdr_row[j] for j in nonempty]
    matrix = [[row[j] for j in nonempty] for row in matrix]
    ncol = len(nonempty)

    out: List[str] = []
    out.append("| " + " | ".join(hdr_row) + " |")
    out.append("| " + " | ".join(["---"] * ncol) + " |")
    for row in matrix:
        out.append("| " + " | ".join(row) + " |")
    return out


def _render_anchor_based(block_cells: List[Dict]) -> List[str]:
    """锚列法（无干净表头时回退）：列按 left 聚类，锚列定逻辑行。"""
    tol = _guess_tol(block_cells)
    centers = _cluster_columns([c["left"] for c in block_cells], tol)
    ncol = len(centers)
    if ncol < 2:
        return [" ".join(c["text"] for c in ln)
                for ln in _physical_lines(block_cells, tol)]

    bands = _column_bands(centers)
    for c in block_cells:
        c["col"] = _assign_column(c, centers, bands)

    # 锚列：中位数高度最小的列（最不换行），用它定逻辑行
    col_heights: Dict[int, List[float]] = {}
    for c in block_cells:
        col_heights.setdefault(c["col"], []).append(c["h"])
    anchor = min(col_heights, key=lambda k: _median(col_heights[k]))

    anchors = sorted([c for c in block_cells if c["col"] == anchor],
                     key=lambda c: c["top"])
    if not anchors:
        return [" ".join(c["text"] for c in ln)
                for ln in _physical_lines(block_cells, tol)]

    rows: List[Dict] = []
    for i, a in enumerate(anchors):
        band_top = a["top"]
        if i + 1 < len(anchors):
            band_bottom = (a["bottom"] + anchors[i + 1]["top"]) / 2.0
        else:
            band_bottom = a["bottom"] + max(1.0, (a["bottom"] - a["top"]))
        rows.append({"top": band_top, "bottom": band_bottom, "cells": [a]})

    anchor_tops = [a["top"] for a in anchors]
    for c in block_cells:
        if c["col"] == anchor:
            continue
        i = min(range(len(rows)),
                key=lambda k: abs(c["top"] - anchor_tops[k]))
        rows[i]["cells"].append(c)

    M = len(rows)
    matrix: List[List[str]] = [["" for _ in range(ncol)] for _ in range(M)]
    span: List[List[Optional[tuple]]] = [[None for _ in range(ncol)]
                                         for _ in range(M)]
    for i, r in enumerate(rows):
        for c in r["cells"]:
            j = c["col"]
            if matrix[i][j]:
                matrix[i][j] += " " + c["text"]
            else:
                matrix[i][j] = c["text"]
                span[i][j] = (c["top"], c["bottom"])

    # 跨行合并单元格向下填充（仅当该格包围框完整覆盖目标行）
    for j in range(ncol):
        for i in range(M):
            if matrix[i][j] and span[i][j]:
                t, b = span[i][j]
                for k in range(i + 1, M):
                    if matrix[k][j]:
                        break
                    # 用目标行的**实际内容范围**（而非延伸到带下界的 band）
                    rt = min(c["top"] for c in rows[k]["cells"])
                    rb = max(c["bottom"] for c in rows[k]["cells"])
                    if t <= rt + 2 and b >= rb - 2:
                        matrix[k][j] = matrix[i][j]
                    else:
                        break

    nonempty = [j for j in range(ncol)
                if any(matrix[i][j] for i in range(M))]
    if not nonempty:
        return [" ".join(c["text"] for c in ln)
                for ln in _physical_lines(block_cells, tol)]
    matrix = [[row[j] for j in nonempty] for row in matrix]
    ncol = len(nonempty)

    out: List[str] = []
    for i, row in enumerate(matrix):
        out.append("| " + " | ".join(row) + " |")
        if i == 0:
            out.append("| " + " | ".join(["---"] * ncol) + " |")
    return out


def _guess_tol(cells: List[Dict]) -> float:
    all_w = [c["w"] for c in cells if c["w"] > 0]
    med_w = _median(all_w) if all_w else 20.0
    return max(15.0, med_w * 0.4)


def _render_table_block(block_cells: List[Dict], med_h: float) -> List[str]:
    """把一个表格区渲染为 Markdown 表（表头定列优先，回退锚列法）。"""
    thr = max(2.0, med_h * 0.6)
    lines = _physical_lines(block_cells, thr)
    if len(lines) < 2:
        return [" ".join(c["text"] for c in ln) for ln in lines]
    header = _detect_header(lines, med_h)
    if header is not None:
        return _render_header_based(lines, header)
    return _render_anchor_based([c for ln in lines for c in ln])


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def rebuild_markdown_from_items(items, image_width: Optional[int] = None) -> Optional[str]:
    """从原始识别项 [(box, text, score), ...] 做版面分析并重建。

    这是结构化输出的主入口，由 RapidOCR 引擎在开启「结构化输出」时调用。
    ``image_width`` 参数为历史兼容保留（旧接口/测试按位置传入），
    几何重建只依赖各项包围框，不依赖画布宽度。
    """
    # 清伪影 + 拆合并框（关键：真实框经常把相邻单元格合并成一个框）
    items = split_and_clean_items(items)
    cells = _parse_items(items)
    if not cells:
        return None

    tol = _guess_tol(cells)
    all_h = [c["h"] for c in cells]
    med_h = _median(all_h) if all_h else 12.0
    thr = max(2.0, med_h * 0.6)

    lines = _physical_lines(cells, thr)
    blocks = _segment_blocks(lines, tol)

    out: List[str] = []
    for b in blocks:
        if b["kind"] == "table":
            out.extend(_render_table_block(
                [c for ln in b["lines"] for c in ln], med_h))
        else:
            for ln in b["lines"]:
                out.append(" ".join(c["text"] for c in ln))
    return "\n".join(out) if out else None


def rebuild_markdown(
    line_items: List[List[str]],
    line_boxes: List[List[list]],
    image_width: Optional[int] = None,
) -> Optional[str]:
    """兼容旧接口：从「逐行 [items] + 每行 [boxes]」重建。"""
    if not line_items or not line_boxes:
        return None
    items = []
    for its, bxs in zip(line_items, line_boxes):
        for t, b in zip(its, bxs):
            items.append((b, t, 0.0))
    return rebuild_markdown_from_items(items, image_width)
