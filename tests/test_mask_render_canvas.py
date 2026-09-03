# -*- coding: utf-8 -*-
"""蒙版 Canvas 渲染集成测试（headless）：验证 _render 走 Canvas 绝对坐标路径不崩、
逐字 create_text、回退路径也能画。不依赖真实 OCR / 翻译。
"""
import tkinter as tk
from types import SimpleNamespace

from PIL import Image

from winocr.ui.tk.mask_cache import TranslationCache
from winocr.ui.tk.mask_render import MaskRenderer
from winocr.ui.tk.mask_window import MaskWindow


def _make_renderable():
    root = tk.Tk()
    root.withdraw()
    w = MaskWindow.__new__(MaskWindow)
    w._tk = tk
    w._alive = True
    w._px_per_pt = 1.0
    w._bw, w._bh = 60, 70
    w._bx, w._by = 0, 0
    w._last_img = None
    w.root = tk.Toplevel(root)
    w.root.withdraw()
    w._cv = tk.Canvas(w.root, bg="#1e1e1e")
    w._cache = TranslationCache()          # 译文缓存（实测渲染不依赖，但需存在）
    w._renderer = MaskRenderer(w)          # 渲染委托：headless 下直接驱动 Canvas
    return w, root


def _fake_ocr_result():
    rec = SimpleNamespace()
    rec.text = "hello\nworld"
    rec.lines = ["hello", "world"]
    # line_boxes：每行一个四点框（RapidOCR 行级框结构）
    rec.line_boxes = [
        [[(10, 10), (50, 10), (50, 30), (10, 30)]],
        [[(10, 40), (50, 40), (50, 60), (10, 60)]],
    ]
    rec.para_ids = [0, 1]
    return rec


def test_render_canvas_draws_chars():
    w, root = _make_renderable()
    img = Image.new("RGB", (60, 70), (255, 255, 255))
    w._render(_fake_ocr_result(), "译文一\n译文二", img)
    # 逐字 create_text → 应有 >0 个画布对象
    assert len(w._cv.find_all()) > 0, "Canvas 应画出译文，不应为空"
    w.root.destroy()


def test_render_fallback_draws_when_no_line_boxes():
    w, root = _make_renderable()
    rec = SimpleNamespace(text="hello", lines=None, line_boxes=None)
    w._render(rec, "（未能识别文本）")
    assert len(w._cv.find_all()) > 0, "无 line_boxes 应走回退并画出"
    w.root.destroy()


def test_render_no_crash_on_empty_translation():
    w, root = _make_renderable()
    img = Image.new("RGB", (60, 70), (255, 255, 255))
    # 空译文不应抛异常
    w._render(_fake_ocr_result(), "", img)
    w.root.destroy()


def test_render_skips_degenerate_line_without_total_fallback():
    """某行 OCR 框为空（坏框）时只跳过该行，不得坠入全局回退。

    旧版整个 _render 共用一个 except：一行 min([]) 抛 ValueError → 全版译文
    缩成左上角 6pt 小字堆（用户刷新后截图实证）。新版逐行容错，坏行跳过。
    """
    w, root = _make_renderable()
    rec = SimpleNamespace(
        text="good one\ngood two\nbad",
        lines=["good one", "good two", "bad"],
        line_boxes=[
            [[(10, 10), (50, 10), (50, 30), (10, 30)]],
            [[(10, 40), (50, 40), (50, 60), (10, 60)]],
            [],                      # 坏框：空列表
        ],
        para_ids=[0, 1, 2],
    )
    w._render(rec, "译文一\n译文二\n译文三")
    items = len(w._cv.find_all())
    assert items >= 2, f"坏框行应被跳过、其余行正常渲染，实际画布对象 {items}（<2 说明坠入了全局回退）"
    w.root.destroy()


def test_render_does_not_resize_window():
    """蒙版窗口 = 框选大小：_render 不得改写 _bh / _bw。
    禁止用 OCR 检测框 padding 把窗口撑出原框选范围（用户诉求：框多大出多大）。
    """
    w, root = _make_renderable()
    img = Image.new("RGB", (60, 70), (255, 255, 255))
    bw_before, bh_before = w._bw, w._bh
    w._render(_fake_ocr_result(), "译文一\n译文二", img)
    assert w._bw == bw_before, f"_bw 被改：{bw_before} -> {w._bw}"
    assert w._bh == bh_before, f"_bh 被改：{bh_before} -> {w._bh}"
    w.root.destroy()


def test_render_does_not_expand_on_ocr_box_overflow():
    """OCR 检测框底 y 超出 _bh 时，窗口高必须仍等于 _bh（不撑出框选）。"""
    w, root = _make_renderable()
    bh_before = w._bh      # 70
    # 构造 OCR：单行框底 y=200，远超 _bh=70
    rec = SimpleNamespace(
        text="overflow",
        lines=["overflow"],
        line_boxes=[[[(10, 50), (50, 50), (50, 200), (10, 200)]]],
        para_ids=[0],
    )
    img = Image.new("RGB", (60, 200), (255, 255, 255))
    w._render(rec, "很长很长的译文行", img)
    assert w._bh == bh_before, (
        f"OCR 框超出 _bh 也不应撑窗口：{bh_before} -> {w._bh}")
    w.root.destroy()


def test_render_fallback_does_not_resize_window():
    """无 line_boxes 走 _render_fallback，同样不得扩窗。"""
    w, root = _make_renderable()
    bw_before, bh_before = w._bw, w._bh
    rec = SimpleNamespace(text="hello", lines=None, line_boxes=None)
    w._render(rec, "回退译文行一\n回退译文行二\n回退译文行三")
    assert w._bw == bw_before, f"_bw 被改：{bw_before} -> {w._bw}"
    assert w._bh == bh_before, f"_bh 被改：{bh_before} -> {w._bh}"
    w.root.destroy()
