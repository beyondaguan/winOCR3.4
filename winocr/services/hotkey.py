# -*- coding: utf-8 -*-
"""全局热键服务（keyboard 库封装）。

不是插件轴，而是一个普通服务：Windows 上实现方式单一，
为它建一整套插件机制属于过度设计。真要换实现（pynput），改这一个文件即可。

相比 2.0 的改进：
  - 注册表与回调映射不再是模块级全局字典，改为实例状态；
  - 注册失败（缺库 / 权限不足 / 热键被占用）逐条降级，不会一个失败全盘不注册；
  - 记录实际注册成功的热键，UI 可以如实展示「哪几个生效了」。
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List

from ..core.event_bus import EventBus, Events

logger = logging.getLogger(__name__)

# 动作名 → 中文显示名（P5：动作名 = 胶囊注册键，默认组合键由胶囊声明）
ACTIONS = {
    "snap_translate": "截图翻译",
    "clipboard_extract": "剪贴板提取",
    "translate_text": "翻译文本",
    "cycle_engine": "切换引擎",
    "selection_translate": "划词翻译",
    "tts_read": "朗读文本",
    "open_knowledge": "打开知识库",
    "import_knowledge": "导入知识库",
    "cancel": "取消任务",
    "mask_translate": "蒙版翻译",
    "mask_refresh": "蒙版刷新",
    "quit": "退出程序",
}


class HotkeyService:
    def __init__(self, config, bus: EventBus = None) -> None:
        self.config = config                 # HotkeyConfig
        self.bus = bus
        self.registered: Dict[str, str] = {}  # 动作 → 实际生效的组合键
        self.errors: List[str] = []
        self._handlers: Dict[str, Callable] = {}
        self._defaults: Dict[str, str] = {}   # 动作 → 胶囊声明默认组合键
        self._win32 = None                    # Win32 兜底后端（keyboard 失效时启用）

    @staticmethod
    def backend_available() -> bool:
        try:
            import keyboard  # noqa: F401
            return True
        except Exception:
            return False

    def register(self, handlers: Dict[str, Callable],
                 defaults: Dict[str, str] = None) -> bool:
        """注册热键。handlers 的键取自 ACTIONS；defaults 提供每个动作的默认组合键
        （通常来自胶囊声明），实际生效 = 用户 overrides > defaults。

        退出热键（quit）是「全局闸门」：无论 enabled 开关如何、其余热键是否停用，
        它都始终注册——这是程序的最后逃生口，不能被普通开关关掉（对应需求
        「Ctrl+Shift+Q → 全局闸门，永久存在」）。其余热键照常受 enabled 控制。

        兜底：若 keyboard 库一个键都没注册成功（典型是被 UIPI 拦截：普通用户进程
        无法向管理员前台窗口注入钩子），自动改用 Win32 RegisterHotKey 系统级热键，
        不受 UIPI 影响。
        """
        self._handlers = handlers
        self._defaults = dict(defaults or {})
        self.registered.clear()
        self.errors.clear()

        try:
            import keyboard
        except Exception:
            self.errors.append("未安装 keyboard 库，全局热键不可用（窗口内快捷键仍可用）")
            return False

        # 先无条件注册退出闸门，让它脱离 enabled 的开关。
        quit_handler = handlers.get("quit")
        if quit_handler and getattr(self.config, "quit", ""):
            try:
                keyboard.add_hotkey(self.config.quit, self._wrap("quit", quit_handler))
                self.registered["quit"] = self.config.quit
            except Exception as e:      # 单个失败不影响其余
                self.errors.append(f"{ACTIONS['quit']} ({self.config.quit}): {e}")

        if not getattr(self.config, "enabled", True):
            self.errors.append("其余全局热键已在设置中关闭")
            return bool(self.registered)

        for action, handler in handlers.items():
            if action == "quit":        # 已先行注册，跳过
                continue
            combo = self.config.resolved(action, self._defaults.get(action, ""))
            if not combo:
                continue
            try:
                keyboard.add_hotkey(combo, self._wrap(action, handler))
                self.registered[action] = combo
            except Exception as e:      # 单个失败不影响其余
                self.errors.append(f"{ACTIONS.get(action, action)} ({combo}): {e}")

        # 兜底：keyboard 一个键都没注册成功（典型 UIPI 拦截）→ 切换 Win32 系统热键
        if not self.registered:
            try:
                from .win32_hotkey import Win32HotkeyBackend
                backend = Win32HotkeyBackend()
                if backend.register(handlers, self._resolved_combos(handlers)):
                    self._win32 = backend
                    for a, c in backend.registered.items():
                        self.registered[a] = c
                    self.errors.append("已切换至 Win32 系统热键（keyboard 钩子不可用）")
            except Exception as e:
                self.errors.append(f"Win32 热键兜底失败: {e}")
        return bool(self.registered)

    def _resolved_combos(self, handlers: Dict[str, Callable]) -> Dict[str, str]:
        """为 Win32 兜底后端算出 {action: 组合键}（闸门 + 其余启用项），与 keyboard 同口径。"""
        combos: Dict[str, str] = {}
        quit_handler = handlers.get("quit")
        if quit_handler and getattr(self.config, "quit", ""):
            combos["quit"] = self.config.quit
        if getattr(self.config, "enabled", True):
            for action in handlers:
                if action == "quit":
                    continue
                combo = self.config.resolved(action, self._defaults.get(action, ""))
                if combo:
                    combos[action] = combo
        return combos

    def _wrap(self, action: str, handler: Callable) -> Callable:
        def _fire():
            if self.bus is not None:
                self.bus.publish(Events.HOTKEY, action)
            try:
                handler()
            except Exception as e:
                logger.exception("[热键 %s] 回调异常: %s", action, e)
        return _fire

    def unregister_all(self) -> None:
        try:
            import keyboard
            # unhook_all 比 unhook_all_hotkeys 更彻底，可释放底层监听线程，
            # 避免 root.quit() 后进程因 keyboard 监听线程未结束而挂起。
            keyboard.unhook_all()
        except Exception:
            pass
        if self._win32 is not None:
            try:
                self._win32.unregister_all()
            except Exception:
                pass
            self._win32 = None
        self.registered.clear()

    def reload(self) -> bool:
        """配置变更后重新注册。"""
        self.unregister_all()
        return self.register(self._handlers, self._defaults)

    def summary(self) -> str:
        if self.registered:
            return " | ".join(f"{ACTIONS.get(a, a)}: {k}"
                              for a, k in self.registered.items())
        return self.errors[0] if self.errors else "未注册"
