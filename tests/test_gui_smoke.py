# -*- coding: utf-8 -*-
"""GUI 冒烟测试：不进人机交互，只验证「能不能建起来、控件契约对不对」。

覆盖 2.0 最容易翻车的几处：
  - 面板展开/收起（2.0 因为漏写 global 导致整个 AI 面板失效）
  - 事件总线 → 界面回写（跨线程 after(0)）
  - 设置对话框能否构建（2.0 的设置窗口曾因变量名写错直接抛异常）

没有显示环境（CI）时自动跳过。
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from winocr.core import App
from winocr.core.event_bus import Events
from winocr.core.types import TranslateResult

tk = pytest.importorskip("tkinter")

from conftest import display_available

pytestmark = pytest.mark.skipif(not display_available(), reason="无图形环境")


def _make_ui():
    from winocr.ui.tk import TkUi
    from winocr.ui.tk.main_window import MainWindow

    app = App().build()
    ui = TkUi()
    ui.bind(app)
    ui.root = tk.Tk()
    ui.root.withdraw()                 # 不弹窗，纯构建验证
    ui._setup_style()
    ui.window = MainWindow(ui)
    ui._wire_events()
    return app, ui


def test_main_window_builds():
    app, ui = _make_ui()
    try:
        w = ui.window
        # TkUi 依赖的全部接口都必须存在
        for attr in ("set_status", "show_original", "show_translation",
                     "set_busy", "refresh_engine_label", "get_original"):
            assert callable(getattr(w, attr)), attr
        w.set_status("hello")
        w.show_original("Hello World")
        assert w.get_original() == "Hello World"
        w.show_translation(TranslateResult(text="你好世界", engine="argos", elapsed=0.3))
        assert w.get_translation() == "你好世界"
        w.set_busy(True)
        w.set_busy(False)
    finally:
        ui.root.destroy()


def test_chat_panel_toggle():
    """2.0 在这里栽过：漏写 global → 面板永远展不开。"""
    app, ui = _make_ui()
    try:
        w = ui.window
        assert w.chat is not None
        assert w.chat_visible is False
        w.toggle_chat()
        assert w.chat_visible is True
        w.toggle_chat()
        assert w.chat_visible is False
    finally:
        ui.root.destroy()


def test_chat_bubbles_and_attachments():
    app, ui = _make_ui()
    try:
        chat = ui.window.chat
        n0 = len(chat.bubbles)
        chat.add_bubble("user", "问题")
        chat.add_bubble("assistant", "回答")
        chat.add_bubble("system", "提示")
        assert len(chat.bubbles) == n0 + 3
        chat.clear_history()
        assert chat.bubbles == []

        from PIL import Image
        chat.add_image(Image.new("RGB", (8, 8)), "测试图")
        assert len(chat.attachments) == 1
        chat.remove_attachment(0)
        assert chat.attachments == []
    finally:
        ui.root.destroy()


def test_ui_mode_switch():
    app, ui = _make_ui()
    try:
        ui.window.apply_ui_mode("advanced")
        assert ui.window.advanced_flow.winfo_manager() == "pack"
        ui.window.apply_ui_mode("simple")
        assert ui.window.simple_flow.winfo_manager() == "pack"
    finally:
        ui.root.destroy()


def test_dialogs_build():
    from winocr.ui.tk import dialogs
    app, ui = _make_ui()
    try:
        # open_history 依赖 persistence 服务；用户配置可能黑名单了 json_history，
        # 注入一个假 store 保证冒烟测试不依赖外部配置。
        class _FakeStore:
            def load_records(self):
                return []
            def clear(self):
                pass
        app.services["persistence"] = _FakeStore()

        for opener in (dialogs.open_hotkey_settings,
                       dialogs.open_api_settings,
                       dialogs.open_about,
                       dialogs.open_history):
            opener(ui.window)
            tops = [c for c in ui.root.winfo_children()
                    if isinstance(c, tk.Toplevel)]
            assert tops, opener.__name__
            for t in tops:
                t.grab_release()
                t.destroy()
    finally:
        ui.root.destroy()


def test_quit_app_cleans_up_and_force_exits(monkeypatch):
    """quit_app 调用链路完整且兜底 os._exit（防止托盘线程残留导致进程不退出）。

    之前「右键托盘退出但图标还在」的根因：pystray.Icon.run_detached() 启了一个
    非 daemon 线程跑消息循环；quit_app 只发停止信号、不等线程退出、Tk 销毁后
    主线程就被那条线程拖住。修复：tray.stop() 内部 join 线程 + quit_app 末尾
    os._exit(0) 兜底。本测试断言调用链顺序与幂等性（不真杀 pytest）。
    """
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    import os as os_mod
    from winocr.ui.tk.app import TkUi

    exit_calls = []
    monkeypatch.setattr(os_mod, "_exit", lambda c: exit_calls.append(c))

    fake_tts = MagicMock()
    fake_app = MagicMock()
    fake_app.services.get.return_value = fake_tts
    fake_tray = MagicMock()
    fake_root = MagicMock()

    self = SimpleNamespace(
        app=fake_app, root=fake_root, _tray=fake_tray, _exiting=False)

    TkUi.quit_app(self)

    # 调用顺序与次数
    fake_tts.stop.assert_called_once()
    fake_tray.stop.assert_called_once()
    fake_app.shutdown.assert_called_once()
    fake_root.quit.assert_called_once()
    fake_root.destroy.assert_called_once()
    assert exit_calls == [0], "兜底 os._exit(0) 必须被调用"

    # 幂等：再次调用不再触发副作用（_exiting 守卫）
    TkUi.quit_app(self)
    assert fake_tray.stop.call_count == 1
    assert fake_app.shutdown.call_count == 1
    assert exit_calls == [0]


def test_event_bus_updates_ui_from_worker_thread():
    """后台线程 publish → 主线程 after(0) 回写。

    必须真的跑一轮 mainloop：Tk 只在事件循环运行时才接受来自其他线程的 after()，
    这也正是 3.0 强制「所有跨线程回写走 ui.post()」的原因。
    """
    app, ui = _make_ui()
    seen = {}

    def _worker():
        time.sleep(0.15)
        app.bus.publish(Events.STATUS, "来自后台线程")
        app.bus.publish(Events.TRANSLATE_DONE,
                        TranslateResult(text="ok", engine="argos"))

    def _check():
        seen["translation"] = ui.window.get_translation()
        seen["status"] = ui.window.status_label.cget("text")
        ui.root.quit()

    def _watchdog():
        """超时保护：3s 后强制退出 mainloop，防止测试挂死。"""
        seen.setdefault("timeout", True)
        ui.root.quit()

    threading.Thread(target=_worker, daemon=True).start()
    ui.root.after(500, _check)
    ui.root.after(3000, _watchdog)    # 3s 硬超时
    ui.root.mainloop()
    ui.root.destroy()

    assert seen.get("timeout") is None, "测试超时（3s 内后台线程未完成）"
    assert seen["translation"] == "ok"
    assert "argos" in seen["status"] or "翻译完成" in seen["status"]


def test_post_drains_via_pump_in_fifo_order():
    """回归：跨线程 post 必须按 FIFO 顺序被主线程泵执行（不经 run()，走自引导）。

    3.4.9 把 post 从「非主线程直接 root.after(0)」改为「队列 + 主线程泵」：
    后台线程只入队，泵未启动时由第一次 post 用一次 after(0) 引导，之后
    泵由主线程 after(40) 自续期。本测试验证：不调用 _start_pump，后台线程
    连续多次 post 也要全部按序执行（顺序丢失/丢回调 = 图贴卡「识别中」）。
    """
    app, ui = _make_ui()
    got = []
    timeout_hit = {"yes": False}
    try:
        def _worker():
            time.sleep(0.1)
            for i in range(3):
                ui.post(lambda n=i: got.append(f"job{n}"))

        def _check():
            ui.root.quit()

        def _watchdog():
            """超时保护：3s 后强制退出 mainloop，防止测试挂死。"""
            timeout_hit["yes"] = True
            ui.root.quit()

        threading.Thread(target=_worker, daemon=True).start()
        ui.root.after(800, _check)
        ui.root.after(3000, _watchdog)   # 3s 硬超时
        ui.root.mainloop()
        assert not timeout_hit["yes"], "测试超时（3s 内泵未排空队列）"
        assert got == ["job0", "job1", "job2"], f"泵未按序排空队列: {got}"
    finally:
        ui.root.destroy()
