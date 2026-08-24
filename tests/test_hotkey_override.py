# -*- coding: utf-8 -*-
"""热键覆盖行为回归测试：任意新录制覆盖旧键、立即生效、重启保留。

钉死三条铁律（用户 2026-08-24 明确要求）：
  1. 任意新录制的组合键都能立即生效；
  2. 录制后【旧的默认/旧覆盖】被覆盖（不再注册，不复存在）；
  3. 重启（save→load）后 override 仍在、仍覆盖。

为避免在测试环境真注册全局键盘钩子（需要管理员权限），stub 掉 keyboard 模块，
只验证「最终要向系统注册哪些组合键」的结论。
"""
import sys
import types

import pytest

# ---- 在导入任何项目模块前 stub 掉 keyboard ----
_fake_kb = types.ModuleType("keyboard")
_captured = []          # 记录 register 期间 add_hotkey 收到的组合键


def _add_hotkey(combo, callback, *a, **k):
    _captured.append(combo)
    return True


def _unhook_all():
    _captured.clear()


_fake_kb.add_hotkey = _add_hotkey
_fake_kb.unhook_all = _unhook_all
sys.modules["keyboard"] = _fake_kb

from winocr.core.config import AppConfig, HotkeyConfig   # noqa: E402
from winocr.services.hotkey import ACTIONS, HotkeyService  # noqa: E402


@pytest.fixture
def defaults():
    """胶囊声明的默认组合键（取自 ACTIONS 之外，模拟胶囊）。"""
    return {
        "snap_translate": "ctrl+shift+a",
        "clipboard_extract": "ctrl+shift+c",
        "translate_text": "ctrl+shift+t",
        "cycle_engine": "ctrl+shift+e",
        "selection_translate": "ctrl+shift+d",
        "tts_read": "ctrl+shift+r",
        "cancel": "ctrl+shift+x",
        "quit": "ctrl+shift+q",
    }


@pytest.fixture
def handlers():
    return {a: (lambda: None) for a in ACTIONS}


def _registered_combos(svc) -> dict:
    return dict(svc.registered)


# ---- 1. 无 override 时，注册的是胶囊默认 ----
def test_default_used_when_no_override(defaults, handlers):
    cfg = HotkeyConfig(enabled=True, quit="ctrl+shift+q", overrides={})
    svc = HotkeyService(cfg)
    svc.register(handlers, defaults)
    assert svc.registered.get("snap_translate") == "ctrl+shift+a"


# ---- 2. 录制任意新键 → 只注册新键，旧默认被覆盖（不复存在）----
@pytest.mark.parametrize("new_combo", ["alt+q", "win+f", "ctrl+alt+s", "shift+f2"])
def test_any_new_override_replaces_default(new_combo, defaults, handlers):
    cfg = HotkeyConfig(enabled=True, quit="ctrl+shift+q", overrides={})
    svc = HotkeyService(cfg)
    svc.register(handlers, defaults)
    assert svc.registered.get("snap_translate") == "ctrl+shift+a"  # 旧默认在位

    # 用户录制一个【任意】新组合键（不是写死的 alt+q）
    cfg.overrides = {"snap_translate": new_combo}
    svc.reload()                                   # 立即生效路径

    reg = _registered_combos(svc)
    assert reg.get("snap_translate") == new_combo  # 新键生效
    assert "ctrl+shift+a" not in reg.values()      # 旧默认彻底消失（被覆盖）
    # 该动作只绑定了一个组合键，不存在「新旧并存」
    assert list(reg.values()).count(new_combo) == 1


# ---- 3. 再次改录 → 上一次 override 也被覆盖（只留最新）----
def test_rerecord_replaces_previous_override(defaults, handlers):
    cfg = HotkeyConfig(enabled=True, quit="ctrl+shift+q", overrides={})
    svc = HotkeyService(cfg)
    svc.register(handlers, defaults)

    cfg.overrides = {"snap_translate": "alt+q"}
    svc.reload()
    assert svc.registered.get("snap_translate") == "alt+q"

    # 又改录成另一个键
    cfg.overrides = {"snap_translate": "f9"}
    svc.reload()
    reg = _registered_combos(svc)
    assert reg.get("snap_translate") == "f9"
    assert "alt+q" not in reg.values()             # 上一版 override 也被覆盖
    assert "ctrl+shift+a" not in reg.values()      # 原始默认同样不在


# ---- 4. 多个动作各自独立覆盖，互不影响 ----
def test_per_action_independent_overrides(defaults, handlers):
    cfg = HotkeyConfig(enabled=True, quit="ctrl+shift+q",
                       overrides={"snap_translate": "alt+q",
                                  "cycle_engine": "alt+e"})
    svc = HotkeyService(cfg)
    svc.register(handlers, defaults)
    reg = _registered_combos(svc)
    assert reg["snap_translate"] == "alt+q"
    assert reg["cycle_engine"] == "alt+e"
    # 未覆盖的动作仍用默认
    assert reg["clipboard_extract"] == "ctrl+shift+c"


# ---- 5. 清除某动作 override → 回落胶囊默认 ----
def test_clear_override_falls_back_to_default(defaults, handlers):
    cfg = HotkeyConfig(enabled=True, quit="ctrl+shift+q",
                       overrides={"snap_translate": "alt+q"})
    svc = HotkeyService(cfg)
    svc.register(handlers, defaults)
    assert svc.registered.get("snap_translate") == "alt+q"

    cfg.overrides = {}                              # 清空（对话框「清除」按钮语义）
    svc.reload()
    assert svc.registered.get("snap_translate") == "ctrl+shift+a"  # 回到默认


# ---- 6. 落盘 → 重载：override 保留，重启后仍覆盖旧默认 ----
def test_override_persists_across_save_load(tmp_path, defaults, handlers):
    cfg = AppConfig.defaults()
    cfg.hotkey.overrides = {"snap_translate": "alt+q", "cycle_engine": "alt+e"}
    p = str(tmp_path / "winocr.toml")
    cfg.save(p)

    reloaded = AppConfig.load(p)
    assert reloaded.hotkey.overrides.get("snap_translate") == "alt+q"
    assert reloaded.hotkey.overrides.get("cycle_engine") == "alt+e"

    # 用重载后的配置装配热键服务，确认旧默认仍被覆盖
    svc = HotkeyService(reloaded.hotkey)
    svc.register(handlers, defaults)
    reg = _registered_combos(svc)
    assert reg["snap_translate"] == "alt+q"
    assert reg["cycle_engine"] == "alt+e"
    assert "ctrl+shift+a" not in reg.values()
