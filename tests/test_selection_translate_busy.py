"""回归：划词翻译在异步任务异常时必须释放 busy 锁，避免界面"卡死"。

对应根因：原版 ``winocr/capsules/selection_translate.py`` 的 ``_translate_text``
只在成功路径发布 ``WORKFLOW_DONE``；一旦 ``pipeline.translate`` 抛异常，
``run_async`` 只发 ``ERROR/STATUS`` 而不调 ``on_done``，导致 ``_busy`` 永久置位，
后续所有操作（手动 Ctrl+Shift+D）在入口被 ``_busy.is_set()`` 挡掉。

这里用真实 ``Pipeline.run_async`` + 一个会抛异常的 ``translate`` 复现，
断言异常路径也释放 busy（与"修复后"的意图一致）。
"""

import threading
import time

import pytest


class _FakeWindow:
    """只实现划词翻译弹贴条用到的接口，无需真实 Tk。

    实现 conftest.WindowProtocol，mypy 静态检查可捕获与真实 MainWindow 的接口漂移。
    """

    def __init__(self):
        self.busy = False
        self.status_text = ""
        self.sticker_original = ""       # 初始化：防 _done 回调竞态（先 release_busy 后 show_sticker）
        self.sticker_translation = ""    # 同上

    def set_busy(self, b: bool) -> None:
        self.busy = b

    def set_status(self, t: str) -> None:
        self.status_text = t

    def get_selected_text(self) -> str:
        return "hello"

    def get_original(self) -> str:
        return ""

    def get_translation(self) -> str:
        return ""

    def show_sticker(self, original: str, translation: str = "") -> None:
        self.sticker_original = original
        self.sticker_translation = translation


def _make_ui(monkeypatch):
    from winocr.core.app import App
    from winocr.ui.tk.app import TkUi

    app = App().build()

    def boom(text, target, explicit=False, note="", **kwargs):  # noqa: ANN001
        time.sleep(0.3)  # 让"进行中"状态可观测，避免与主线程竞态
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(app.pipeline, "translate", boom)

    ui = TkUi()
    ui.bind(app)
    ui.window = _FakeWindow()
    # headless 下 root 为 None，post() 会直接 return；替身为立即执行，
    # 这样才能验证 _err 里经 post 回写的失败提示真的发出去。
    ui.post = lambda fn, *a, **k: fn(*a, **k)
    return ui


def test_translate_failure_still_releases_busy(monkeypatch):
    """翻译抛异常时，_busy 必须被释放，否则后续热键全部失效。"""
    ui = _make_ui(monkeypatch)

    # 直接走「翻译并弹贴条」核心：它负责置位/释放 _busy 锁。
    ui._translate_to_sticker("hello", note="划词")
    # 翻译进行中应处于 busy（boom 先睡 0.3s，这里留 0.1s 窗口观测）
    time.sleep(0.1)
    assert ui._busy.is_set(), "划词翻译进行中应置位 _busy"

    # 等待后台线程跑完异常路径。注意：_err 里 _release_busy() 先清 _busy、
    # 之后才 post 状态栏/图贴回写，故不能只等 busy 清空（后台线程可能被抢占），
    # 要等「busy 释放 + 状态栏含失败 + 图贴回填失败」三个条件齐了再断言。
    deadline = time.time() + 5
    while time.time() < deadline:
        if (not ui._busy.is_set()
                and "失败" in ui.window.status_text
                and "失败" in ui.window.sticker_translation):
            break
        time.sleep(0.05)

    assert not ui._busy.is_set(), "翻译失败后 _busy 未释放，后续操作会被卡住"
    assert ui.window.busy is False
    # 异常信息应回写状态栏，而不是静默吞掉
    assert "失败" in ui.window.status_text
    # 失败结果也应弹到划词小贴条，而不是只在状态栏静默提示
    assert "hello" in ui.window.sticker_original
    assert "失败" in ui.window.sticker_translation


def test_poll_clipboard_text_waits_for_slow_copy():
    """回归：注入 Ctrl+C 后目标软件复制慢（数百 ms）时，轮询必须等到文本，
    而不是旧版 0.2s 单次等待读到空 → 误报「没有可翻译的文本」。

    用户实测现象：直接划词按热键失败、先手动 Ctrl+C 再按热键成功 ——
    根因就是固定 0.2s 等待对慢复制不够；轮询到 timeout 秒内首次读到
    非空文本即返回。
    """
    from winocr.ui.tk.app import _poll_clipboard_text

    calls = {"n": 0}

    def slow_reader():
        calls["n"] += 1
        return "" if calls["n"] < 4 else "  slow app selected text  "

    r = _poll_clipboard_text(slow_reader, timeout=2.0, interval=0.02)
    assert r == "slow app selected text"
    assert calls["n"] == 4, "应轮询到第 4 次才命中"

    # 一直为空 → 超时返回空串（不能无限等）
    r2 = _poll_clipboard_text(lambda: "", timeout=0.15, interval=0.03)
    assert r2 == ""

    # reader 抛异常 → 安静超时，不崩
    def boom():
        raise RuntimeError("x")

    r3 = _poll_clipboard_text(boom, timeout=0.15, interval=0.03)
    assert r3 == ""


def test_capture_and_translate_uses_latest_text(monkeypatch):
    """回归：取词必须取到【最新】选中文本并送进翻译（贴图随新选中更新）。

    取词在独立工作线程执行，目标软件仍持有焦点，故能读到屏幕任意处新选中的文字。
    这里断言：_capture_selection 返回的最新文本被送进了翻译弹图。
    """
    ui = _make_ui(monkeypatch)

    captured = {}

    def fake_capture():
        captured["called"] = True
        return "刚刚高亮的新文字", "uia"

    monkeypatch.setattr(ui, "_capture_selection", fake_capture)

    # 让 translate 立刻返回带 .text 的结果，便于断言贴图内容
    class _R:
        text = "translated"

    monkeypatch.setattr(ui.app.pipeline, "translate", lambda *a, **k: _R())

    ui._capture_and_translate()

    # 等待后台翻译线程完成：不能只等 _busy 清空（_done 先 release_busy 后
    # post(show_sticker)，竞态窗口内 sticker_original 尚未设置），
    # 必须等到「busy 释放 + sticker_original 回填正确值」两个条件齐了再断言。
    deadline = time.time() + 5
    while time.time() < deadline:
        if (not ui._busy.is_set()
                and ui.window.sticker_original == "刚刚高亮的新文字"):
            break
        time.sleep(0.05)

    assert captured.get("called"), "入口未调用取词方法"
    assert ui.window.sticker_original == "刚刚高亮的新文字", (
        "贴图未使用最新取到的文本，仍可能沿用旧内容")


def test_on_hotkey_selection_delegates_to_worker_thread(monkeypatch):
    """回归：selection_translate 热键不得在监听线程内同步取词（3.4.5 死锁根因）。

    新设计：热键回调 _on_hotkey_selection 立即返回，真正取词放进独立工作线程。
    本测试断言调用回调后取词被委托给另一条线程（无内联 SendInput/UIA 阻塞），
    杜绝「监听线程持锁等待自己」的死锁。
    """
    ui = _make_ui(monkeypatch)
    seen = {}
    real_thread = threading.Thread

    def fake_thread(target=None, **kw):
        seen["target"] = target
        return real_thread(target=target, **kw)

    monkeypatch.setattr(threading, "Thread", fake_thread)
    monkeypatch.setattr(ui, "_capture_selection", lambda: ("x", "uia"))

    ui._on_hotkey_selection()
    assert seen.get("target") is not None, "取词未被委托给工作线程"
    assert seen["target"].__name__ == "_capture_and_translate"


def test_run_async_error_path_calls_on_error_and_does_not_raise():
    """真实 run_async：fn 抛异常时调用 on_error，且不向上抛（与修复后一致）。"""
    from winocr.core.app import App

    app = App().build()
    captured = {}

    def boom():
        raise ValueError("boom")

    def on_error(e):
        captured["err"] = e

    t = app.pipeline.run_async(boom, on_error=on_error)
    t.join(timeout=5)

    assert isinstance(captured.get("err"), ValueError)
    # 关键点：run_async 自己吞掉了异常，不会让调用方线程炸


def test_hotkey_instant_popup_shows_placeholder_then_fills(monkeypatch):
    """即时弹窗：按 Ctrl+Shift+D 瞬间图贴先出现（识别中占位），随后回填真实译文。

    对应 3.4.8 行为：用户要求按键立即出图贴，不等取词+翻译链路走完。
    """
    ui = _make_ui(monkeypatch)
    calls = []

    orig_show = ui.window.show_sticker

    def traced(o, t=""):
        calls.append((o, t))
        return orig_show(o, t)

    ui.window.show_sticker = traced

    # 让 translate 立即返回带 .text 的结果，便于断言贴图最终内容
    class _R:
        text = "translated"

    monkeypatch.setattr(ui.app.pipeline, "translate", lambda *a, **k: _R())
    monkeypatch.setattr(ui, "_capture_selection", lambda: ("selected text", "uia"))

    ui._on_hotkey_selection()

    # 1) 即时弹窗：第一次 show_sticker 必须是「空原文 + 加载中占位」
    assert calls, "按键后图贴未立即弹出"
    first = calls[0]
    assert first[0] == "" and "取词/翻译中" in first[1], (
        f"首弹应为加载中占位，实际 {first!r}")

    # 2) 等待后台取词+翻译线程完成并【回填真实内容】
    #    注意：_done 先释放 busy 再 post(show_sticker)，故不能只等 busy 清空，
    #    要等到真正的译文回填到图贴为止（用 real post 时主线程会在 busy 释放后处理）。
    deadline = time.time() + 5
    last = ("", "")
    while time.time() < deadline:
        if len(calls) >= 2 and calls[-1][0] == "selected text" \
                and calls[-1][1] == "translated":
            last = calls[-1]
            break
        time.sleep(0.02)
    if last == ("", ""):
        last = calls[-1]

    # 3) 再确认 worker 线程已结束（busy 锁释放），避免测试退出时 daemon 线程还在跑。
    #    真实运行不需要这一步；测试里用 immediate post 且不想留下未结束的 worker。
    if ui._busy.is_set():
        deadline2 = time.time() + 3
        while ui._busy.is_set() and time.time() < deadline2:
            time.sleep(0.02)

    # 3) 最终应回填真实原文+译文
    assert last[0] == "selected text" and last[1] == "translated", (
        f"最终图贴未回填真实内容，实际 {last!r}")
