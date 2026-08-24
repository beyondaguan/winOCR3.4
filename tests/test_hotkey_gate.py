# -*- coding: utf-8 -*-
"""退出热键必须是「全局闸门、永久存在」。

锁死一条不变量：哪怕用户在设置里关掉了全局热键（enabled=False），
Ctrl+Shift+Q 仍要注册成功并退出整个程序；其余热键才受 enabled 控制。
用 fake keyboard 模块，无需真实库、无需显示器即可跑。
"""
from __future__ import annotations

import sys
import types
import unittest


class _FakeKeyboard:
    """记录 add_hotkey 调用，模拟 keyboard 库的注册行为。"""

    def __init__(self) -> None:
        self.calls = []          # [(combo, callback), ...]

    def add_hotkey(self, combo, callback, *args, **kwargs):
        self.calls.append((combo, callback))

    def unhook_all_hotkeys(self):
        self.calls.clear()


def _install_fake() -> _FakeKeyboard:
    fake = _FakeKeyboard()
    saved = sys.modules.get("keyboard")
    sys.modules["keyboard"] = fake
    return fake, saved


def _restore_fake(saved) -> None:
    if saved is None:
        sys.modules.pop("keyboard", None)
    else:
        sys.modules["keyboard"] = saved


class QuitGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake, self._saved = _install_fake()
        from winocr.services.hotkey import HotkeyService
        from winocr.core.config import HotkeyConfig
        self.HotkeyService = HotkeyService
        self.HotkeyConfig = HotkeyConfig

    def tearDown(self) -> None:
        _restore_fake(self._saved)

    def _handlers(self):
        return {a: lambda: None for a in
                ("snap_translate", "clipboard_extract", "translate_text",
                 "cycle_engine", "selection_translate", "tts_read", "quit")}

    def _defaults(self):
        return {
            "snap_translate": "ctrl+shift+a",
            "clipboard_extract": "ctrl+shift+x",
            "translate_text": "ctrl+shift+t",
            "cycle_engine": "ctrl+shift+e",
            "selection_translate": "ctrl+shift+d",
            "tts_read": "ctrl+shift+r",
        }

    def test_quit_registers_even_when_disabled(self):
        """enabled=False 时，quit 仍注册，其余热键被跳过。"""
        cfg = self.HotkeyConfig(enabled=False)
        svc = self.HotkeyService(cfg)
        ok = svc.register(self._handlers(), self._defaults())

        self.assertTrue(ok, "退出热键应在关闭全局热键时仍注册成功")
        self.assertIn("quit", svc.registered, "Ctrl+Shift+Q 必须始终被注册")
        self.assertEqual(svc.registered["quit"], "ctrl+shift+q")
        # 其余热键应被 enabled 挡掉
        for a in ("snap_translate", "clipboard_extract", "translate_text",
                  "cycle_engine"):
            self.assertNotIn(a, svc.registered, f"{a} 在 disabled 时不应注册")

    def test_quit_and_others_register_when_enabled(self):
        """enabled=True 时，quit 与其余热键都注册（组合键来自胶囊默认）。"""
        cfg = self.HotkeyConfig(enabled=True)
        svc = self.HotkeyService(cfg)
        ok = svc.register(self._handlers(), self._defaults())

        self.assertTrue(ok)
        self.assertIn("quit", svc.registered)
        for a in ("snap_translate", "clipboard_extract", "translate_text",
                  "cycle_engine"):
            self.assertIn(a, svc.registered, f"{a} 在 enabled 时应注册")
            self.assertEqual(svc.registered[a], self._defaults()[a],
                             "无覆盖时应使用胶囊默认组合键")

    def test_override_wins_over_default(self):
        """用户覆盖（overrides）优先于胶囊默认。"""
        cfg = self.HotkeyConfig(enabled=True,
                                overrides={"snap_translate": "ctrl+alt+z"})
        svc = self.HotkeyService(cfg)
        ok = svc.register(self._handlers(), self._defaults())
        self.assertTrue(ok)
        self.assertEqual(svc.registered["snap_translate"], "ctrl+alt+z")
        self.assertEqual(svc.registered["translate_text"], "ctrl+shift+t",
                         "未覆盖的动作仍用胶囊默认")

    def test_quit_empty_combo_not_registered(self):
        """quit 组合键被手动清空时，不应注册（无组合键可绑）。"""
        cfg = self.HotkeyConfig(enabled=True, quit="")
        svc = self.HotkeyService(cfg)
        ok = svc.register(self._handlers(), self._defaults())
        self.assertNotIn("quit", svc.registered)
        # 其余热键仍应正常注册
        self.assertIn("snap_translate", svc.registered)


if __name__ == "__main__":
    unittest.main()
