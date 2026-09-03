# -*- coding: utf-8 -*-
"""蒙版翻译刷新行为回归测试。

设计约束（参考 Translumo 等实时屏幕翻译软件）：
- 不轮询（轮询每 1.2s 要 withdraw 蒙版抓图，导致蒙版闪烁）；
- 仅由全局快捷键 Ctrl+Shift+N（蒙版刷新）调用 _kick() 触发重译；
- 翻译结果按原文缓存：相同原文直接复用译文，不重复请求翻译 API（省网络延迟）；
- 流式补偿：OCR 完成后先显示原文，译文到达再替换；
- 忙时的刷新请求必须排队补跑，不能静默丢弃（否则用户按快捷键像卡死）；
- 翻译必须 silent=True，否则会覆盖主界面译文框 / 触发自动朗读。

headless 下用固定 PIL 图 mock _grab_region，用同步 run_async 跑通整条刷新链路。
"""
import tkinter as tk
from unittest import mock

from winocr.ui.tk.mask_cache import TranslationCache
from winocr.ui.tk.mask_window import MaskWindow


def _fake_img():
    from PIL import Image
    return Image.new("RGB", (100, 100), (255, 255, 255))


def _make_window():
    root = tk.Tk()
    root.withdraw()
    pipeline = mock.MagicMock()
    calls = {"n": 0, "silent": []}

    def fake_translate(text, target, explicit=False, silent=False):
        calls["n"] += 1
        calls["silent"].append(silent)
        tr = mock.MagicMock()
        tr.text = "译:" + text
        return tr

    pipeline.translate.side_effect = fake_translate
    pipeline.run_async.side_effect = lambda fn, on_done=None, on_error=None: (
        on_done(fn()) if on_done else None
    )

    # 用「没有增量方法」的假引擎，强制走普通 recognize 路径，
    # 以隔离测试翻译缓存（不依赖增量 OCR）。
    ocr_engine = mock.MagicMock()

    def fake_recognize(img):
        return ocr_engine._cur

    ocr_engine.recognize.side_effect = fake_recognize
    if hasattr(ocr_engine, "recognize_incremental"):
        del ocr_engine.recognize_incremental
    if hasattr(ocr_engine, "build_ocr_cache"):
        del ocr_engine.build_ocr_cache
    pipeline.services = {"ocr": ocr_engine}

    mw = MaskWindow.__new__(MaskWindow)
    mw._tk = tk
    mw._pipeline = pipeline
    mw._ui = None                 # 直接在主线程调 _render，便于断言
    mw._target = "zh-CN"
    mw._alive = True
    mw._busy = False
    mw._pending_kick = False
    mw._first_done = True        # 跳过剪贴板路径，走 OCR
    mw._current_text = ""
    mw._cache = TranslationCache()        # 译文缓存（替代旧 _tr_cache 裸 dict）
    mw._status = None
    mw._orig_bbox = (0, 0, 100, 100)
    mw._engine_overrides = {}
    mw._bx, mw._by = 0, 0
    mw._bw, mw._bh = 100, 100
    mw.root = tk.Toplevel(root)
    mw.root.withdraw()
    mw._grab_region = lambda hide=True: _fake_img()
    mw._render = mock.MagicMock()
    mw._renderer = mock.MagicMock()        # 渲染委托（_render 已 mock，此处仅作安全兜底）
    mw._clipboard_text = lambda: ""
    return mw, pipeline, calls, ocr_engine


def test_manual_refresh_renders():
    mw, pipeline, calls, ocr = _make_window()
    rec = mock.MagicMock()
    rec.text = "hello world"
    ocr._cur = rec

    mw._kick()

    assert mw._render.called, "_kick 必须驱动一次渲染"
    last_translation = mw._render.call_args[0][1]
    assert last_translation == "译:hello world", "最终应渲染译文"
    mw.root.destroy()


def test_translation_cache_hits_same_source():
    mw, pipeline, calls, ocr = _make_window()
    rec = mock.MagicMock()
    rec.text = "same text"
    ocr._cur = rec

    mw._kick()          # 首次：OCR + 翻译
    mw._kick()          # 再次：原文相同 → 应命中缓存

    assert calls["n"] == 1, "相同原文应命中翻译缓存，只请求一次翻译 API"
    mw.root.destroy()


def test_no_polling_registered():
    """确认已移除轮询：MaskWindow 不再有 _tick / _schedule_tick。"""
    assert not hasattr(MaskWindow, "_tick"), "不应再存在轮询 _tick"
    assert not hasattr(MaskWindow, "_schedule_tick"), "不应再存在轮询调度"


def test_translate_is_silent():
    """蒙版翻译必须 silent=True。

    否则 pipeline.translate 会向事件总线广播 TRANSLATE_START/TRANSLATE_DONE，
    主界面收到后会覆盖译文框、改状态栏，甚至触发「翻译后自动朗读」。
    """
    mw, pipeline, calls, ocr = _make_window()
    rec = mock.MagicMock()
    rec.text = "hello"
    ocr._cur = rec

    mw._kick()

    assert calls["silent"] == [True], "蒙版必须 silent=True，否则会污染主界面译文框"
    mw.root.destroy()


def test_busy_kick_queues_instead_of_dropping():
    """忙时到达的刷新请求必须排队补跑，不能静默丢弃。

    OCR 1-2s + 翻译 API 1-3s 期间，旧实现直接 return 且不提示，
    用户按快捷键完全没反应，体验上就是「蒙版迟钝 / 卡死」。
    """
    mw, pipeline, calls, ocr = _make_window()
    rec = mock.MagicMock()
    rec.text = "hello"
    ocr._cur = rec

    pending = {}

    def slow_run_async(fn, on_done=None, on_error=None):
        # 模拟任务在跑、尚未收尾：只登记，不执行
        pending["fn"], pending["on_done"] = fn, on_done
        return mock.MagicMock()

    pipeline.run_async.side_effect = slow_run_async

    mw._kick()                              # 第一次：占用，进入忙
    assert mw._busy, "首次 kick 应进入忙状态"
    assert pending["fn"] is not None

    mw._kick()                              # 第二次：忙 → 应排队，而非丢弃
    assert mw._pending_kick, "忙时的刷新请求必须排队，不能静默丢弃"

    pending["on_done"](pending["fn"]())     # 任务收尾 → 应自动补跑
    assert not mw._pending_kick, "补跑后应清空排队标志"
    assert mw._render.called, "补跑应真正驱动渲染"
    mw.root.destroy()
