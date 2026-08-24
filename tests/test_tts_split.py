# -*- coding: utf-8 -*-
"""TTS 文本切分逻辑的纯函数测试（不联网，不触碰 edge_tts）。

设计：短句（合计 < maxlen）合并为单段，走老的单段合成路径；
只有超长句 / 多长句才会切成多段，触发流式提前播放（B 改进）。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from winocr.services.tts import TtsService


def test_single_sentence_one_chunk():
    svc = TtsService()
    out = svc._split_sentences("你好，世界。")
    assert out == ["你好，世界。"]


def test_short_sentences_combine_to_one():
    # 短句合计远小于 maxlen，应合并为单段（不触发流式）
    svc = TtsService()
    out = svc._split_sentences("短句一。短句二。短句三！", maxlen=240)
    assert len(out) == 1
    assert out[0] == "短句一。短句二。短句三！"


def test_long_sentence_hard_split():
    svc = TtsService()
    text = "甲" * 500  # 单句超长，无标点
    out = svc._split_sentences(text, maxlen=240)
    assert len(out) == 3
    assert sum(len(c) for c in out) == 500
    assert all(len(c) <= 240 for c in out)


def test_multiple_long_sentences_split():
    svc = TtsService()
    text = "一" * 300 + "。" + "二" * 300 + "！"  # 两句各自超长
    out = svc._split_sentences(text, maxlen=240)
    assert len(out) >= 2
    assert sum(len(c) for c in out) == 602
    assert all(len(c) <= 240 for c in out)
    # 标点必须被保留在切出的片段里
    assert any(c.endswith("。") for c in out)
    assert any(c.endswith("！") for c in out)


def test_newline_preserved_as_boundary():
    svc = TtsService()
    text = "第一行内容很长很长很长\n第二行结束。"
    out = svc._split_sentences(text, maxlen=8)
    # 至少按换行切出两段，且换行符落到首段
    assert any(p.startswith("第一行内容") for p in out)
    assert any("第二行结束。" in p for p in out)


def test_empty_text_falls_back():
    svc = TtsService()
    out = svc._split_sentences("   ")
    assert out == ["   "]
