"""英文段落模式回归用例（A / B / A+B）。

复用 tools/en_para_test.py 的图像合成逻辑：在 tmp 目录生成 3 张不同排版的
英文段落图（单段 / 双段带间距 / 双段带首行缩进），分别跑三种 paragraph_mode，
断言段落分组行为符合预期。

运行：
    pytest tests/test_en_paragraph_mode.py -q
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.en_para_test import make_images  # noqa: E402

from winocr.services.ocr.rapidocr import RapidOcrEngine  # noqa: E402

# 预期：图片名 -> 在 B / A+B 模式下应识别出的段落数
EXPECTED_PARAS = {
    "single": 1,   # 单段多行 -> 1 段
    "two_gap": 2,  # 双段带大间距 -> 2 段
    "indent": 2,   # 双段带首行缩进（无间距） -> 2 段
}


def _engine():
    e = RapidOcrEngine()
    e.preprocess = True
    e.model_type = "tiny"
    e.structured = False
    e.auto_upgrade = True
    return e


def test_paragraph_mode_A_is_flat(tmp_path):
    """A 模式：只做行分组，不跑段落判定 -> 无段落结构（平铺）。"""
    paths = make_images(str(tmp_path))
    e = _engine()
    e.paragraph_mode = "A"
    for name, p in paths.items():
        from PIL import Image
        res = e.recognize(Image.open(p).convert("RGB"))
        # A 模式不跑段落层：paragraphs 为空，para_ids 为空
        assert res.paragraphs == [], f"[{name}] A 模式不应有段落：{res.paragraphs}"
        assert res.para_ids == [], f"[{name}] A 模式 para_ids 应为空：{res.para_ids}"
        # text 平铺：段间不出现双换行
        assert "\n\n" not in res.text, f"[{name}] A 模式 text 不应含 \\n\\n"


def test_paragraph_mode_B_and_AplusB_split_paragraphs(tmp_path):
    """B / A+B 模式：四角几何段落判定 -> 正确分段，且 text 含双换行。"""
    paths = make_images(str(tmp_path))
    from PIL import Image
    for mode in ("B", "A+B"):
        e = _engine()
        e.paragraph_mode = mode
        for name, p in paths.items():
            res = e.recognize(Image.open(p).convert("RGB"))
            expected = EXPECTED_PARAS[name]
            # 至少达到预期段数（OCR 偶发多切一段也属正常，但不应少于预期）
            assert len(res.paragraphs) >= expected, (
                f"[{name}] {mode} 段落数应 >= {expected}，实际 {len(res.paragraphs)}: "
                f"{res.paragraphs}"
            )
            assert len(res.para_ids) == len(res.lines), (
                f"[{name}] {mode} para_ids 长度应与 lines 一致"
            )
            # 段数-1 个双换行分隔符（单段时为 0，双段时为 1）
            assert res.text.count("\n\n") == len(res.paragraphs) - 1, (
                f"[{name}] {mode} 双换行数应 = 段数-1，"
                f"实际 text 有 {res.text.count(chr(10)+chr(10))} 个，段落 {len(res.paragraphs)}"
            )


def test_paragraph_mode_default_is_AplusB():
    """默认 paragraph_mode 应为 A+B（推荐）。"""
    e = _engine()
    assert e.paragraph_mode == "A+B", f"默认应为 A+B，实际 {e.paragraph_mode!r}"


def test_paragraph_mode_invalid_value_no_crash(tmp_path):
    """非法 mode 值不应崩溃：当前实现回退到自适应行分组 + 段落判定。"""
    paths = make_images(str(tmp_path))
    e = _engine()
    e.paragraph_mode = "X"  # 非法：走 else 分支（自适应行分组，等同 A+B 行分组层）
    from PIL import Image
    p = paths["two_gap"]
    res = e.recognize(Image.open(p).convert("RGB"))
    assert isinstance(res.text, str) and res.text.strip(), "非法 mode 不应产生空结果"
