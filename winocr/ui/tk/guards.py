# -*- coding: utf-8 -*-
"""UI 线程守卫 — 防止后台线程误触 Tk 控件导致死锁。

Tk 的 Tcl 解释器只存活在创建 ``Tk()`` 的那条线程（本程序即 run() 所在线程）。
任何别的线程直接碰 Tk 部件都会随机死锁 / 控件状态错乱（这是 2.0 反复出现的
"卡死"根因之一）。所有 Tk 访问必须经 TkUi.post() 回到主线程，但代码层面没有
强制 —— 这个装饰器在开发期把「越界调用」显式暴露出来，而不是静默吞掉。

用法：装饰 MainWindow 里所有直接操作 Tk 部件的回调方法。越界调用会写一条
ERROR 级日志（不崩溃、不污染 Tk），便于定位"哪个后台路径忘了走 post"。
"""
from __future__ import annotations

import functools
import logging
import threading


def ui_thread(method):
    """装饰器：确保方法只在 TkUi 的主线程内执行。

    被装饰的方法必须属于某个持有 ``.ui`` 引用、且该 ui 有 ``_ui_thread``
    属性的对象（MainWindow 满足）。非主线程调用时打印 ERROR 并返回 None，
    绝不把 Tk 调用留给错误的线程去执行。
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        ui = getattr(self, "ui", None)
        ui_thread = getattr(ui, "_ui_thread", None)
        if ui_thread is not None and threading.current_thread() is not ui_thread:
            try:
                from .app import _sel_log_static
                _sel_log_static(
                    "UI-THREAD-VIOLATION: %s 被非主线程调用，已拦截（应改走 ui.post）"
                    % method.__name__, logging.ERROR)
            except Exception:
                pass
            return None
        return method(self, *args, **kwargs)

    return wrapper
