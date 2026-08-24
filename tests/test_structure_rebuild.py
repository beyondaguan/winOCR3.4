# -*- coding: utf-8 -*-
"""structure.rebuild_markdown 的单元测试（纯几何，无需 GUI 框架 / 模型）。"""
from winocr.services.ocr.structure import rebuild_markdown


def _box(x0, y0, x1, y1):
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_three_column_table():
  """合成一个 4 行 x 3 列对齐表格，应重建出带 | 与分隔行的 Markdown。"""
  W = 600
  line_items = [
    ["药物", "标准剂量", "备注"],
    ["别嘌醇", "50-100 mg/日", "基因检测"],
    ["非布司他", "20-40 mg/日", "肾功能不全首选"],
    ["苯溴马隆", "25-50 mg/日", "充分饮水"],
  ]
  line_boxes = [
    [_box(60, 10, 140, 30), _box(260, 10, 340, 30), _box(460, 10, 540, 30)],
    [_box(60, 40, 140, 60), _box(260, 40, 340, 60), _box(460, 40, 540, 60)],
    [_box(60, 70, 140, 90), _box(260, 70, 340, 90), _box(460, 70, 540, 90)],
    [_box(60, 100, 140, 120), _box(260, 100, 340, 120), _box(460, 100, 540, 120)],
  ]
  md = rebuild_markdown(line_items, line_boxes, image_width=W)
  assert md is not None, "对齐三列表格应重建"
  lines = md.split("\n")
  assert lines[0].startswith("| ") and lines[0].endswith(" |")
  assert "| --- | --- | --- |" in md, "应含表头分隔行"
  assert "药物" in md and "别嘌醇" in md and "苯溴马隆" in md
  assert md.split("\n")[0].count("|") == 4


def test_single_column_is_plain_text():
  """单列（无表格结构）应原样输出为纯文本，而非被误判成表格。"""
  W = 600
  line_items = [["这是一段普通文字"], ["这是第二行"], ["这是第三行"]]
  line_boxes = [
    [_box(50, 10, 300, 30)],
    [_box(50, 40, 300, 60)],
    [_box(50, 70, 300, 90)],
  ]
  md = rebuild_markdown(line_items, line_boxes, image_width=W)
  assert md is not None
  assert "|" not in md, "单列不应出现表格线"
  assert "这是一段普通文字" in md and "这是第三行" in md


def test_single_row_is_plain_text():
  """单行多列不足以构成表格，应原样输出为纯文本。"""
  line_items = [["a", "b", "c"]]
  line_boxes = [[_box(0, 0, 10, 10), _box(100, 0, 110, 10), _box(200, 0, 210, 10)]]
  md = rebuild_markdown(line_items, line_boxes, image_width=300)
  assert md is not None
  assert "|" not in md, "单行不应表格化"


def test_two_separate_tables_keep_distinct_columns():
  """两个相邻但列结构不同的表格，应各自独立聚类，不混为一表。"""
  W = 600
  t1 = [
    (["名称", "剂量", "备注"],
     [_box(20, 10, 80, 30), _box(120, 10, 200, 30), _box(260, 10, 360, 30)]),
    (["别嘌醇", "100 mg", "基因检测"],
     [_box(20, 40, 80, 60), _box(120, 40, 200, 60), _box(260, 40, 360, 60)]),
  ]
  para = (["注：用药需遵医嘱。"],
      [_box(20, 80, 400, 100)])
  t2 = [
    (["项目", "结果"],
     [_box(20, 130, 90, 150), _box(200, 130, 300, 150)]),
    (["尿酸", "520 μmol/L"],
     [_box(20, 170, 90, 190), _box(200, 170, 300, 190)]),
  ]
  line_items, line_boxes = [], []
  for items, boxes in t1 + [para] + t2:
    line_items.append(items)
    line_boxes.append(boxes)
  md = rebuild_markdown(line_items, line_boxes, image_width=W)
  assert md is not None
  assert "注：用药需遵医嘱。" in md and "|" not in "注：用药需遵医嘱。"
  assert md.count("| --- |") >= 2, "两个表各应有一条分隔行"
  lines = [l for l in md.split("\n") if l.startswith("|")]
  assert lines[0].count("|") == 4
  assert any(l.count("|") == 3 for l in lines), "表 2 应为 2 列"


def test_merged_cross_row_cell_fills_down():
  """跨行合并单元格：某一格纵向跨两行，其文字应填充到所覆盖的两行。"""
  W = 600
  line_items = [
    ["急性期", "表现"],
    ["", "处理"],
  ]
  line_boxes = [
    [_box(20, 10, 100, 90), _box(200, 10, 300, 30)],
    [_box(20, 50, 100, 90), _box(200, 50, 300, 90)],
  ]
  md = rebuild_markdown(line_items, line_boxes, image_width=W)
  assert md is not None
  assert md.count("急性期") == 2, "跨行单元格应向下填充"
  lines = [l for l in md.split("\n") if l.startswith("|")]
  assert "急性期" in lines[-1]


def test_title_row_kept_as_plain():
  """整行横跨较宽（标题）应作为纯文字输出，而非 | 表格行。"""
  W = 600
  line_items = [
    ["一、急性期治疗（发作时 24-48 小时内开始）"],
    ["别嘌醇", "50-100 mg/日", "基因检测"],
    ["非布司他", "20-40 mg/日", "肾功能不全首选"],
  ]
  line_boxes = [
    [_box(20, 10, 560, 30)],
    [_box(60, 40, 140, 60), _box(260, 40, 340, 60), _box(460, 40, 540, 60)],
    [_box(60, 70, 140, 90), _box(260, 70, 340, 90), _box(460, 70, 540, 90)],
  ]
  md = rebuild_markdown(line_items, line_boxes, image_width=W)
  assert md is not None
  lines = md.split("\n")
  assert lines[0].startswith("一、急性期治疗") and "|" not in lines[0], \
    "标题行应为纯文字不带 |"
  assert "| --- | --- | --- |" in md, "其后表格应带分隔行"


def test_empty_input():
  assert rebuild_markdown([], [], image_width=600) is None
  assert rebuild_markdown(None, None) is None


def test_wrapped_remark_column_not_split():
  """真实痛风表几何：备注列换行成短行/长行（左对齐、宽度不同），
  列数必须仍是 3，不能按 x 中心被拆成 4~5 列；换行片段不能串进邻列。"""
  from winocr.services.ocr.structure import rebuild_markdown_from_items

  items = [
    # 表头（left=20/140/300）
    (_box(20, 10, 80, 30), "药物", 0.9),
    (_box(140, 10, 230, 30), "标准剂量", 0.9),
    (_box(300, 10, 360, 30), "备注", 0.9),
    # 行1：秋水仙碱（标准剂量换行、备注换行成短行「发作36」+ 长行）
    (_box(20, 40, 100, 60), "秋水仙碱", 0.9),
    (_box(140, 40, 260, 60), "首次 1.0 mg，1 小时后 0.5 mg；之后 0.5", 0.9),
    # 续行：在秋水仙碱行与 NSAIDs 行之间自己的 y 层（与真实截图一致）
    (_box(140, 60, 240, 82), "mg，每日1-3次", 0.9),
    (_box(300, 40, 360, 60), "发作36", 0.9),
    (_box(300, 60, 430, 82), "小时内使用效果最佳；肾功能不全需减量", 0.9),
    # 行2：NSAIDs（药名是宽框，左对齐仍在 left=20）
    (_box(20, 80, 110, 102), "NSAIDs（如依托考昔）", 0.9),
    (_box(140, 80, 260, 102), "依托考昔 120 mg，每日 1 次，连用 8 天", 0.9),
    (_box(300, 80, 440, 102), "有消化道溃疡、肾功能不全者慎用", 0.9),
    # 行3：糖皮质激素
    (_box(20, 110, 130, 132), "糖皮质激素（口服）", 0.9),
    (_box(140, 110, 280, 132), "泼尼松 30-40 mg/日，连用 5-10 天后逐渐减量", 0.9),
    (_box(300, 110, 460, 132), "适用于 NSAIDs/秋水仙碱禁忌或无效者", 0.9),
  ]
  md = rebuild_markdown_from_items(items, 600)
  assert md is not None
  tbl = [l for l in md.split("\n") if l.startswith("|")]
  assert tbl, "应重建出表格"
  # 必须恰好 3 列（表头行有 4 个 |）
  assert tbl[0].count("|") == 4, f"应 3 列，实际：{tbl[0]}"
  # NSAIDs 药名必须落在药物列（行首），不能并进标准剂量列
  nsaid_rows = [l for l in tbl if "NSAIDs" in l]
  assert nsaid_rows and nsaid_rows[0].startswith("| NSAIDs"), \
    f"NSAIDs 药名应在药物列：{nsaid_rows}"
  # 秋水仙碱的备注「发作36」与长行应在备注列
  rows_with_36 = [l for l in tbl if "发作36" in l]
  assert rows_with_36 and rows_with_36[0].startswith("| 秋水仙碱 |"), \
    "发作36 应留在秋水仙碱行"
  # 备注长行不得与药名揉进药物/剂量列
  assert any("发作36" in l and l.count("|") == 4 for l in tbl)


def test_hushsnap_style_wrapped_continuation():
  """对齐 HushSnap 的真实几何：秋水仙碱的换行续行（mg，每日1-3次）在
  自己的 y 层（秋水仙碱行与 NSAIDs 行之间），NSAIDs 行在其下方独立。
  要求：3 列、NSAIDs 药名在药物列、续行文字不串进 NSAIDs 行、无重复。"""
  from winocr.services.ocr.structure import rebuild_markdown_from_items

  items = [
    # 表头
    (_box(20, 10, 80, 30), "药物", 0.9),
    (_box(140, 10, 230, 30), "标准剂量", 0.9),
    (_box(300, 10, 360, 30), "备注", 0.9),
    # 秋水仙碱行（top 40）+ 它的换行续行（top 65，独立 y 层）
    (_box(20, 40, 100, 60), "秋水仙碱", 0.9),
    (_box(140, 40, 260, 60), "首次 1.0 mg，1 小时后 0.5 mg；之后 0.5", 0.9),
    (_box(300, 40, 360, 60), "发作36", 0.9),
    (_box(140, 65, 240, 85), "mg，每日1-3次", 0.9),
    (_box(300, 65, 430, 85), "小时内使用效果最佳；肾功能不全需减量", 0.9),
    # NSAIDs 行（top 90）
    (_box(20, 90, 110, 112), "NSAIDs（如依托考昔）", 0.9),
    (_box(140, 90, 260, 112), "依托考昔 120 mg，每日 1 次，连用 8 天", 0.9),
    (_box(300, 90, 440, 112), "有消化道溃疡、肾功能不全者慎用", 0.9),
    # 糖皮质激素行（top 135）
    (_box(20, 135, 130, 157), "糖皮质激素（口服）", 0.9),
    (_box(140, 135, 280, 157), "泼尼松 30-40 mg/日，连用 5-10 天后逐渐减量", 0.9),
    (_box(300, 135, 460, 157), "适用于 NSAIDs/秋水仙碱禁忌或无效者", 0.9),
  ]
  md = rebuild_markdown_from_items(items, 600)
  assert md is not None
  tbl = [l for l in md.split("\n") if l.startswith("|")]
  assert tbl, "应重建出表格"
  # 3 列
  assert tbl[0].count("|") == 4, f"应 3 列，实际：{tbl[0]}"
  # NSAIDs 药名在药物列
  assert any(l.startswith("| NSAIDs") for l in tbl), f"NSAIDs 应在药物列：{tbl}"
  # NSAIDs 行不能混入秋水仙碱的续行文字
  nsaid_line = [l for l in tbl if l.startswith("| NSAIDs")][0]
  assert "mg，每日1-3次" not in nsaid_line, "续行不得串进 NSAIDs 行"
  assert "小时内使用效果最佳" not in nsaid_line, "备注续行不得串进 NSAIDs 行"
  # 无重复：秋水仙碱备注「小时内…」全表只出现一次
  assert md.count("小时内使用效果最佳") == 1, "备注文字不得重复填充"
  assert md.count("mg，每日1-3次") == 1, "剂量续行不得重复"


def test_header_missing_a_column_still_aligns_data():
  """表头只标了 2 列（漏了「剂量」列），但数据行有 3 列。
  改进后锚点应并入首数据行 left，使「剂量」列被正确标出而非挤进备注列。"""
  from winocr.services.ocr.structure import rebuild_markdown_from_items

  items = [
    # 表头只有两格：药物 / 备注（缺中间剂量列）
    (_box(20, 10, 100, 30), "药物", 0.9),
    (_box(300, 10, 360, 30), "备注", 0.9),
    # 数据行 3 列
    (_box(20, 40, 100, 60), "别嘌醇", 0.9),
    (_box(140, 40, 260, 60), "50 mg", 0.9),
    (_box(300, 40, 360, 60), "基因检测", 0.9),
    (_box(20, 80, 120, 100), "非布司他", 0.9),
    (_box(140, 80, 260, 100), "40 mg", 0.9),
    (_box(300, 80, 360, 100), "肾功能不全首选", 0.9),
  ]
  md = rebuild_markdown_from_items(items, 600)
  assert md is not None
  tbl = [l for l in md.split("\n") if l.startswith("|")]
  assert tbl, "应重建出表格"
  # 必须 3 列（表头行 4 个 |）
  assert tbl[0].count("|") == 4, f"应 3 列，实际：{tbl[0]}"
  # 表头含「药物」；「剂量」数据不得被挤进备注列
  row = [l for l in tbl if "别嘌醇" in l][0]
  assert "50 mg" in row, f"剂量列应独立成列：{row}"
  assert "| 药物 |" in tbl[0], f"表头首列应为药物：{tbl[0]}"


def test_tight_small_font_header_detected():
  """紧凑小字表头：表头行明显比数据行矮（即使无明显纵向间隙），
  也应被识别为表头并生成分隔行。"""
  from winocr.services.ocr.structure import rebuild_markdown_from_items

  items = [
    # 表头行：矮（h=10）
    (_box(20, 10, 80, 20), "药物", 0.9),
    (_box(140, 10, 230, 20), "标准剂量", 0.9),
    (_box(300, 10, 360, 20), "备注", 0.9),
    # 数据行：高（h=30）
    (_box(20, 40, 100, 70), "别嘌醇", 0.9),
    (_box(140, 40, 260, 70), "50-100 mg/日", 0.9),
    (_box(300, 40, 360, 70), "基因检测", 0.9),
    (_box(20, 80, 120, 110), "非布司他", 0.9),
    (_box(140, 80, 260, 110), "20-40 mg/日", 0.9),
    (_box(300, 80, 360, 110), "肾功能不全首选", 0.9),
  ]
  md = rebuild_markdown_from_items(items, 600)
  assert md is not None
  lines = md.split("\n")
  assert "| --- | --- | --- |" in md, "紧凑小字表头也应生成分隔行"
  assert "药物" in lines[0] and "别嘌醇" in md, "表头与数据均应在场"
