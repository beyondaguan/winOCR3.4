"""Win32 兜底热键（services/win32_hotkey.py）单测。

覆盖：组合键解析（修饰符 / 命名键 / 非法输入）、后端在 mock 的 u32 上能正确
注册 id 并清理。真实 Win32 消息泵无法在测试里触发，故用 mock 验证注册/反注册逻辑。
"""
import types

import pytest

from winocr.services import win32_hotkey as wh


def test_parse_combo_letters_and_mods():
    assert wh.parse_combo("ctrl+shift+d") == (wh.MOD_CONTROL | wh.MOD_SHIFT, 0x44)
    assert wh.parse_combo("ctrl+shift+x") == (wh.MOD_CONTROL | wh.MOD_SHIFT, 0x58)
    assert wh.parse_combo("alt+f4") == (wh.MOD_ALT, 0x73)
    assert wh.parse_combo("win+r") == (wh.MOD_WIN, 0x52)
    # 必须有修饰键
    assert wh.parse_combo("a") is None
    assert wh.parse_combo("f12") is None
    # 非法 / 残缺
    assert wh.parse_combo("ctrl+shift+") is None
    assert wh.parse_combo("ctrl+bogus") is None
    assert wh.parse_combo("") is None


def test_parse_combo_named_keys():
    assert wh.parse_combo("ctrl+shift+f12") == (wh.MOD_CONTROL | wh.MOD_SHIFT, 0x7B)
    assert wh.parse_combo("alt+up") == (wh.MOD_ALT, 0x26)


def test_backend_register_and_unregister(monkeypatch):
    """用 mock u32 验证：至少注册一个键即返回 True，反注册清空且不抛异常。"""
    fake = types.SimpleNamespace()
    fake.RegisterHotKey = lambda *_a, **_k: 1
    fake.UnregisterHotKey = lambda *_a, **_k: 1
    fake.GetMessageW = lambda *_a, **_k: 0        # 让消息泵立即退出
    fake.TranslateMessage = lambda *_a, **_k: 0
    fake.DispatchMessageW = lambda *_a, **_k: 0
    fake.PostThreadMessageW = lambda *_a, **_k: 1
    fake.VkKeyScanW = lambda ch: ord("A")
    monkeypatch.setattr(wh, "u32", fake)

    backend = wh.Win32HotkeyBackend()
    handlers = {"quit": lambda: None,
                "selection_translate": lambda: None}
    combos = {"quit": "ctrl+shift+q",
              "selection_translate": "ctrl+shift+d"}

    assert backend.register(handlers, combos) is True
    assert "quit" in backend.registered
    assert "selection_translate" in backend.registered

    backend.unregister_all()
    assert backend.registered == {}
