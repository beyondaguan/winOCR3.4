# -*- coding: utf-8 -*-
"""直接运行 quit_app 冒烟测试（绕过模块级 skipif）。"""
import sys, os, types, unittest.mock as mock
sys.path.insert(0, r"D:\winOCR3.4")

from types import SimpleNamespace
from winocr.ui.tk.app import TkUi

exit_calls = []
real_os_exit = os._exit
os._exit = lambda c: exit_calls.append(c)

fake_tts = mock.MagicMock()
fake_app = mock.MagicMock()
fake_app.services.get.return_value = fake_tts
fake_tray = mock.MagicMock()
fake_root = mock.MagicMock()

self = SimpleNamespace(app=fake_app, root=fake_root, _tray=fake_tray, _exiting=False)

# 第一次调用
TkUi.quit_app(self)

print("=== quit_app 调用链验证 ===")
print(f"tts.stop called:        {fake_tts.stop.called}")
print(f"tray.stop called:       {fake_tray.stop.called}")
print(f"app.shutdown called:    {fake_app.shutdown.called}")
print(f"root.quit called:       {fake_root.quit.called}")
print(f"root.destroy called:    {fake_root.destroy.called}")
print(f"os._exit called with:   {exit_calls}")

assert fake_tts.stop.called, "tts.stop 未被调用"
assert fake_tray.stop.called, "tray.stop 未被调用"
assert fake_app.shutdown.called, "app.shutdown 未被调用"
assert fake_root.quit.called, "root.quit 未被调用"
assert fake_root.destroy.called, "root.destroy 未被调用"
assert exit_calls == [0], "os._exit(0) 必须被调用"
print("[PASS] 第一次调用: 全部断言通过")

# 幂等：第二次调用不应再触发副作用
TkUi.quit_app(self)
assert fake_tray.stop.call_count == 1, "第二次调用不应再触发 tray.stop"
assert fake_app.shutdown.call_count == 1, "第二次调用不应再触发 shutdown"
assert exit_calls == [0], "第二次调用不应再触发 os._exit"
print("[PASS] 幂等性: 第二次调用无副作用")

# === 热键闸门验证 ===
print("\n=== 退出热键全局闸门验证 ===")
from winocr.services.hotkey import HotkeyService
from winocr.core.config import HotkeyConfig

class FakeKeyboard:
    def __init__(self):
        self.calls = []
    def add_hotkey(self, combo, callback, *a, **kw):
        self.calls.append((combo, callback))
    def unhook_all(self):
        self.calls.clear()

fake_kb = FakeKeyboard()
sys.modules["keyboard"] = fake_kb

# enabled=False 时 quit 仍应注册
cfg = HotkeyConfig(enabled=False)
svc = HotkeyService(cfg)
handlers = {a: (lambda: None) for a in
            ("snap_translate", "clipboard_extract", "translate_text",
             "cycle_engine", "selection_translate", "tts_read", "quit")}
defaults = {"snap_translate": "ctrl+shift+a", "quit": "ctrl+shift+q"}
ok = svc.register(handlers, defaults)
assert ok, "enabled=False 时 quit 仍应注册成功"
assert "quit" in svc.registered, "Ctrl+Shift+Q 必须始终被注册"
assert svc.registered["quit"] == "ctrl+shift+q"
print("[PASS] enabled=False 时 Ctrl+Shift+Q 仍注册成功")

# === tray.stop 验证 ===
print("\n=== tray.stop 线程 join 验证 ===")
from winocr.ui.tk.tray import TrayIcon

# 模拟 icon + thread
fake_icon = mock.MagicMock()
fake_thread = mock.MagicMock()
fake_thread.is_alive.return_value = False  # join 后线程已退出

tray = TrayIcon(show_cb=lambda: None, quit_cb=lambda: None)
tray._icon = fake_icon
tray._thread = fake_thread

tray.stop()

fake_icon.stop.assert_called_once()
fake_thread.join.assert_called_once()
print(f"icon.stop called:  {fake_icon.stop.called}")
print(f"thread.join called: {fake_thread.join.called}")
join_kwargs = fake_thread.join.call_args
print(f"join timeout:      {join_kwargs}")
assert fake_thread.join.called, "thread.join 必须被调用"
print("[PASS] tray.stop 正确调用 icon.stop + thread.join")

os._exit = real_os_exit
print("\n=== 全部冒烟测试通过 ===")
