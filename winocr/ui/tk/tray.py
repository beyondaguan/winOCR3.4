# -*- coding: utf-8 -*-
"""系统托盘（P2-3，可选依赖）。

项目约定「界面零第三方 GUI 依赖」，所以托盘做成**可选**：
装了 pystray 才有托盘图标；没装时静默跳过，程序照常用「隐藏窗口 + 全局热键」
常驻。依赖清单在 requirements.txt 里单独一行注释，不混进 GUI 必需依赖。

托盘菜单：显示主窗口 / 退出。图标用 packaging/WinOCR.ico（不存在则回退内置方块）。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional


def _icon_path() -> Optional[str]:
    """优先 packaging/WinOCR.ico；打包形态回退 exe 同级。"""
    try:
        from ...core.paths import project_root
        root = project_root()
        for cand in (root / "packaging" / "WinOCR.ico",
                     root / "WinOCR.ico",
                     root / "assets" / "WinOCR.ico"):
            if cand.is_file():
                return str(cand)
    except Exception:
        pass
    return None


class TrayIcon:
    """系统托盘图标。start() 返回 True 表示托盘已启用。"""

    def __init__(self, show_cb: Callable[[], None],
                 quit_cb: Callable[[], None]) -> None:
        self._show_cb = show_cb
        self._quit_cb = quit_cb
        self._icon = None
        self._thread = None                # pystray 内部消息循环线程（run_detached 启动）
        self._lock = threading.Lock()

    def start(self) -> bool:
        try:
            import pystray
            from PIL import Image
        except Exception:
            return False            # 未安装 pystray：静默跳过，不打扰用户
        try:
            path = _icon_path()
            img = Image.open(path) if path else Image.new("RGB", (64, 64), "#1677ff")
            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", lambda: self._show_cb()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出 WinOCR", lambda: self._quit_cb()),
            )
            self._icon = pystray.Icon(
                "WinOCR", img, "WinOCR — 截图识字 · 翻译 · AI", menu)

            # pystray _run_detached() 内部直接 threading.Thread(...).start()，
            # 不存储线程引用 → 无法 join / 无法设 daemon → 进程退出时被拖住。
            # 用实例级 patch 替换 _run_detached：自建 daemon 线程并保存引用，
            # stop() 时可 join 等它真正退出。
            icon_ref = self._icon   # 闭包捕获，避免 self._icon 被清空后悬空
            def _patched_run_detached():
                t = threading.Thread(target=lambda: icon_ref._run(), daemon=True)
                self._thread = t
                t.start()
            self._icon._run_detached = _patched_run_detached
            self._icon.run_detached()

            # 兼容回退：若 patch 未生效（属性只读 / 方法签名不同），
            # 仍尝试探测 pystray 可能存储的线程属性。
            if self._thread is None:
                try:
                    self._thread = (getattr(self._icon, "_thread", None)
                                    or getattr(self._icon, "_listener_thread", None)
                                    or getattr(self._icon, "thread", None))
                    if self._thread is not None:
                        self._thread.daemon = True
                except Exception:
                    self._thread = None
            return True
        except Exception as e:
            print(f"[托盘] 启动失败（忽略，继续用隐藏窗口常驻）: {e}")
            return False

    def stop(self) -> None:
        """停托盘：发停止信号 + 等内部线程退出（join 超时 2s，避免拖死进程）。

        三层保障：
        1) 等 icon._running=True（线程初始化窗口）→ icon.stop() 发停止信号；
        2) thread.join(2s) 等线程真正退出（patch 创建的 daemon 线程）；
        3) quit_app 末尾 os._exit(0) 兜底——即使上述两步都失败也能强退进程。
        """
        with self._lock:
            icon = self._icon
            thread = self._thread
            self._icon = None
            self._thread = None
        if icon is not None:
            try:
                # pystray stop() 内部检查 if self._running: —— 如果线程还没跑到
                # _mark_ready()（窗口未创建完），_running 仍为 False，stop() 静默跳过。
                # 轮询等 icon 就绪（最多 1s），确保 stop() 真能发停止信号。
                for _ in range(10):
                    if getattr(icon, "_running", False):
                        break
                    import time as _t
                    _t.sleep(0.1)
                icon.stop()
            except Exception:
                pass
        if thread is not None:
            try:
                # 给消息循环 2s 优雅退出；超时后 daemon=True 保证不会拖住进程
                thread.join(timeout=2.0)
            except Exception:
                pass
