# -*- coding: utf-8 -*-
"""rapidocr._group_by_lines 的单元测试（纯几何，无需 GUI 框架 / 模型）。

重点覆盖两个真实痛点：
 - 备注列换行成「高框」时，不应把下一行并到同一行（多行揉成一行）；
 - 表格边框被 RapidOCR 读成 `|`/`Ⅰ`/`1` 等伪影时，识别文本应清掉。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from winocr.services.ocr.rapidocr import RapidOcrEngine


def _box(x0, y0, x1, y1):
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_tall_remark_cell_does_not_merge_next_row():
  """备注列的高框（top 与上一行对齐、bottom 探入下一行上方）应留在它起始的行，
  下一行必须独立成行，不能被揉进同一行。"""
  items = [
    (_box(10, 10, 80, 30), "药物", 0.9),
    (_box(100, 10, 200, 30), "标准剂量", 0.9),
    (_box(220, 10, 320, 30), "备注", 0.9),
    (_box(220, 10, 320, 70), "需长期使用，注意不良反应", 0.9), # 高框
    (_box(10, 80, 80, 100), "别嘌醇", 0.9),
    (_box(100, 80, 200, 100), "50-100 mg/日", 0.9),
    (_box(220, 80, 320, 100), "基因检测", 0.9),
  ]
  lines, _boxes, _items = RapidOcrEngine._group_by_lines(items)
  assert len(lines) == 2, f"应分出 2 行，实际 {len(lines)} 行：{lines}"
  assert "别嘌醇" in lines[1], "下一行应独立，不被并入上一行"
  assert "药物" in lines[0] and "需长期使用" in lines[0]


def test_border_pipe_artifact_is_stripped():
  """单元格边框被读成 `|`（苯溴马隆|25-50）应在识别文本里清掉。"""
  items = [
    (_box(10, 10, 120, 30), "苯溴马隆|25-50 mg/日", 0.9),
    (_box(140, 10, 260, 30), "促尿酸排泄", 0.9),
  ]
  lines, _boxes, _items = RapidOcrEngine._group_by_lines(items)
  joined = " ".join(lines)
  assert "|" not in joined, f"边框 `|` 应被清掉，实际：{lines}"
  assert "苯溴马隆 25-50 mg/日" in joined or "苯溴马隆 25-50 mg/日" in lines[0]


def test_three_drug_rows_not_fused():
  """真实痛风表：三行药物（秋水仙碱/NSAIDs/糖皮质激素），备注列高框 bottom
  探入下一行上方，行距约 40px（top=10/50/90）。合并带封顶后三行必须各自独立，
  绝不能像旧版那样揉成一行（这正是 winOCR.txt 第 2 行的灾难）。"""
  items = [
    # 行0
    (_box(20, 10, 100, 30), "秋水仙碱", 0.9),
    (_box(140, 10, 260, 30), "首次 1.0 mg", 0.9),
    (_box(300, 10, 460, 72), "发作36小时内使用效果最佳", 0.9),  # 高框
    # 行1
    (_box(20, 50, 110, 72), "NSAIDs（如依托考昔）", 0.9),
    (_box(140, 50, 260, 72), "依托考昔 120 mg", 0.9),
    (_box(300, 50, 460, 112), "有消化道溃疡者慎用", 0.9),    # 高框
    # 行2
    (_box(20, 90, 130, 112), "糖皮质激素（口服）", 0.9),
    (_box(140, 90, 260, 112), "泼尼松 30-40 mg/日", 0.9),
    (_box(300, 90, 460, 132), "适用于禁忌或无效者", 0.9),    # 高框
  ]
  lines, _b, _i = RapidOcrEngine._group_by_lines(items)
  assert len(lines) == 3, f"应分出 3 行，实际 {len(lines)} 行：{lines}"
  assert "秋水仙碱" in lines[0] and "NSAIDs" in lines[1] and "糖皮质激素" in lines[2]
  assert "NSAIDs" not in lines[0] and "糖皮质激素" not in lines[0]


def test_genuine_split_line_still_merged():
  """同一逻辑行被 DB 切成上下两截（几乎相贴、不重叠）仍应拼回一行。"""
  items = [
    (_box(10, 10, 200, 30), "急性发作期", 0.9),  # 上半截
    (_box(10, 31, 200, 50), "禁用降尿酸药", 0.9), # 下半截（gap=1）
  ]
  lines, _boxes, _items = RapidOcrEngine._group_by_lines(items)
  assert len(lines) == 1, f"断框应拼回一行，实际 {len(lines)} 行：{lines}"
  assert "急性发作期" in lines[0] and "禁用降尿酸药" in lines[0]


def test_english_paragraph_not_collapsed_into_one_row():
  """回归：英文正文段落（行高 ≈ 1.5×reach）不再被 row_floor 累加压成 1 行。

  触发条件——`reach = med_h * 1.5` 与行间距同量级时，旧版
  `row_floor = max(row_floor, item['top'] + reach)` 会让合并带无限下推，
  把全文合一行。本测试用真实 OCR 输出的 6 行座标复现这个 bug。"""
  items = [
    (_box(117.8,   26.5, 2074.6,  91.8), "According to Thapa, there are about 300 households in the area, but only elders stay", 0.9),
    (_box(117.8,  118.3, 2069.0, 183.5), "in the village, and their children live in Kathmandu, so the actual number of people in", 0.9),
    (_box(119.0,  213.4, 1921.7, 273.1), "the village at the time of the mudslide cannot be accurately estimated, and the", 0.9),
    (_box(119.0,  305.1, 2122.2, 365.9), "numbers of casualties and missing persons have still not been fully tallied. He said that", 0.9),
    (_box(120.1,  395.8, 2070.1, 457.7), "when he arrived at the scene, he saw two victims' bodies with his own eyes -- one of", 0.9),
    (_box(117.8,  486.4, 1355.1, 551.6), "them seemed to be a friend from his sixth-grade year.", 0.9),
  ]
  lines, _boxes, _items = RapidOcrEngine._group_by_lines(items)
  assert len(lines) == 6, f"应分出 6 行，实际 {len(lines)} 行：{lines}"
  # 真实阅读顺序：第 1 个 box 对应行首 "According to Thapa"，第 6 个对应 "them seemed"
  assert lines[0].startswith("According to Thapa"), f"行首乱序：{lines[0][:60]!r}"
  assert lines[-1].startswith("them seemed"),       f"行尾错位：{lines[-1][:60]!r}"


def test_mixed_font_sizes_stay_separate():
  """新闻页混合字号（标题行高大 + 正文行高小 + 脚注行高更小）应各自独立成行。

  阈值用「图中最小非平凡行距」驱动，所以紧排版不会因为有宽排版而被压垮。"""
  items = [
    # 标题（大字号、行距大）
    (_box(100,  10, 1000,  90), "Breaking News Headline", 0.9),
    # 正文（小字号、紧排版）
    (_box(100, 130,  900, 158), "paragraph one line one", 0.9),
    (_box(100, 162,  900, 190), "paragraph one line two", 0.9),
    (_box(100, 194,  900, 222), "paragraph one line three", 0.9),
    # 脚注（更小字号）
    (_box(100, 280,  400, 296), "caption: figure 1", 0.9),
  ]
  lines, _boxes, _items = RapidOcrEngine._group_by_lines(items)
  assert len(lines) == 5, f"5 个独立位置应得 5 行，实际 {len(lines)} 行：{lines}"
  assert "Breaking News Headline" in lines[0]
  assert "caption: figure 1" in lines[-1]
  # 正文 3 行紧排版不能被揉成一团
  assert sum(1 for l in lines if "paragraph one" in l) == 3


def test_uniform_paragraph_zoom_scale_invariance():
  """同一段正文，从 0.5× 缩到 2× 缩放后，再喂进来仍应得到相同行数。

  `thr = max(2, min_gap * 0.5)` 完全按比例缩放，所以分类结果一致。"""
  base_items = [
    (_box(50, 10, 1000, 40), "line one", 0.9),
    (_box(50, 60, 1000, 90), "line two", 0.9),
    (_box(50, 110, 1000, 140), "line three", 0.9),
    (_box(50, 160, 1000, 190), "line four", 0.9),
  ]
  base_count = len(RapidOcrEngine._group_by_lines(base_items)[0])
  assert base_count == 4, base_count

  def scale(items, k):
    out = []
    for box, text, score in items:
      scaled_box = [[p[0] * k, p[1] * k] for p in box]
      out.append((scaled_box, text, score))
    return out

  # 0.5× 缩放（所有坐标 × 0.5）
  small_count = len(RapidOcrEngine._group_by_lines(scale(base_items, 0.5))[0])
  assert small_count == 4, f"0.5x 缩放行数变化：{small_count}"

  # 2× 缩放（所有坐标 × 2）
  big_count = len(RapidOcrEngine._group_by_lines(scale(base_items, 2.0))[0])
  assert big_count == 4, f"2x 缩放行数变化：{big_count}"


def test_very_loose_paragraph_split_correctly():
  """段间留白特别大的段落（行距 >> 行高）应分开多行；不会被「最小行距」误并。"""
  items = [
    (_box(50, 10, 1000, 40), "first line", 0.9),
    # 第二行远低于第一行（gap = 200，远大于行高 30）
    (_box(50, 270, 1000, 300), "second line", 0.9),
  ]
  lines, _, _ = RapidOcrEngine._group_by_lines(items)
  assert len(lines) == 2, f"宽行距应被分为 2 行，实际：{lines}"


# ---------------------------------------------------------------------------
# 段落层（B 法：四角几何 + 行距众数 + 首行缩进）的单元测试
#   直接喂 line_geom dict（绕过 OCR），保证确定性、不依赖模型/网络。
# ---------------------------------------------------------------------------

def _geom(x0, y0, x1, y1, text=""):
  return {"x0": x0, "x1": x1, "y0": y0, "y1": y1, "text": text}


def test_detect_paragraphs_split_on_large_gap():
  """段间大间隙（> 行距众数 ×1.8）应切成两段。"""
  geoms = [
    _geom(10, 10, 200, 30, "第一段落第一行"),
    _geom(10, 32, 200, 52, "第一段落第二行"),
    _geom(10, 120, 200, 140, "第二段落第一行"),   # gap=68，远大于行距 22
  ]
  ids = RapidOcrEngine._detect_paragraphs(geoms, img_w=210, img_h=200)
  assert ids == [0, 0, 1], f"应切 2 段，实际：{ids}"
  # 聚合后段落数
  paras = []
  buf, cur = [], -2
  for g, pid in zip(geoms, ids):
    if pid != cur:
      if buf:
        paras.append("\n".join(buf))
      buf, cur = [], pid
    buf.append(g["text"])
  if buf:
    paras.append("\n".join(buf))
  assert len(paras) == 2, f"聚合后应 2 段：{paras}"


def test_detect_paragraphs_chinese_indent():
  """中文首行缩进（段间无垂直间隙）应被识别为新段。"""
  geoms = [
    _geom(10, 10, 200, 30, "第一段第一行"),
    _geom(10, 32, 200, 52, "第一段第二行"),
    _geom(40, 54, 200, 74, "第二段首行缩进"),   # 左缩进 30px，无额外垂直间隙
  ]
  ids = RapidOcrEngine._detect_paragraphs(geoms, img_w=210, img_h=100)
  assert ids == [0, 0, 1], f"首行缩进应切 2 段，实际：{ids}"


def test_build_result_carries_paragraphs():
  """_build_result 应附带 paragraphs / para_ids，且 text 用双换行分段。"""
  items = [
    (_box(50, 10, 1000, 40), "第一段第一行", 0.95),
    (_box(50, 52, 1000, 82), "第一段第二行", 0.95),   # 行距 12px（> SPLIT_THR）
    (_box(50, 200, 1000, 230), "第二段第一行", 0.95),  # 段间大间隙 118px
  ]
  engine = RapidOcrEngine()
  engine.structured = False
  res = engine._build_result(None, items)
  assert len(res.lines) == 3, f"应分出 3 行：{res.lines}"
  assert len(res.paragraphs) == 2, f"应 2 段：{res.paragraphs}"
  assert res.para_ids == [0, 0, 1], f"para_ids 异常：{res.para_ids}"
  assert "\n\n" in res.text, f"text 应双换行分段：{res.text!r}"


def test_build_result_mode_A_is_flat():
  """paragraph_mode='A'：仅行分组，不做段落判定，text 平铺无 \\n\\n。"""
  items = [
    (_box(50, 10, 1000, 40), "第一段第一行", 0.95),
    (_box(50, 52, 1000, 82), "第一段第二行", 0.95),
    (_box(50, 200, 1000, 230), "第二段第一行", 0.95),
  ]
  engine = RapidOcrEngine()
  engine.structured = False
  engine.paragraph_mode = "A"
  res = engine._build_result(None, items)
  assert res.paragraphs == [], f"A 模式不应有段落：{res.paragraphs}"
  assert res.para_ids == [], f"A 模式 para_ids 应为空：{res.para_ids}"
  assert "\n\n" not in res.text, f"A 模式 text 不应双换行：{res.text!r}"
  assert len(res.lines) == 3, f"A 模式应仍有 3 行：{res.lines}"


def test_build_result_mode_B_runs_paragraphs():
  """paragraph_mode='B'：行分组用旧固定阈值，仍跑四角段落判定，text 双换行。"""
  items = [
    (_box(50, 10, 1000, 40), "第一段第一行", 0.95),
    (_box(50, 52, 1000, 82), "第一段第二行", 0.95),
    (_box(50, 200, 1000, 230), "第二段第一行", 0.95),
  ]
  engine = RapidOcrEngine()
  engine.structured = False
  engine.paragraph_mode = "B"
  res = engine._build_result(None, items)
  assert len(res.paragraphs) == 2, f"B 模式应 2 段：{res.paragraphs}"
  assert "\n\n" in res.text, f"B 模式 text 应双换行：{res.text!r}"

