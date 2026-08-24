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
