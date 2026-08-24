# -*- coding: utf-8 -*-
"""pystray 托盘线程生命周期集成测试。

验证 3.4.17 托盘退出修复的核心不变量：
  1. start() 后线程引用被捕获（patch _run_detached 生效）；
  2. 线程是 daemon（不会拖住进程退出）；
  3. stop() 后线程在 2s 内真正退出（轮询等待，不直接 assert）。

需要桌面会话（pystray 要创建真实系统图标），CI 无头环境自动跳过。
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import display_available

pytestmark = pytest.mark.skipif(not display_available(), reason="无图形环境（pystray 需要桌面会话）")

pystray = pytest.importorskip("pystray")
from PIL import Image  # noqa: E402


@pytest.fixture
def tray():
    """创建并启动一个真实 TrayIcon，测试后自动 stop。"""
    from winocr.ui.tk.tray import TrayIcon

    t = TrayIcon(show_cb=lambda: None, quit_cb=lambda: None)
    assert t.start() is True, "pystray 不可用或启动失败"
    yield t
    # 兜底清理：即使测试中途异常也确保 stop
    try:
        t.stop()
    except Exception:
        pass


def test_tray_thread_captured(tray):
    """start() 后 _thread 不为 None（patch _run_detached 生效）。"""
    assert tray._thread is not None, "线程引用未被捕获（patch 失效）"


def test_tray_thread_is_daemon(tray):
    """patch 创建的线程必须是 daemon（不会拖住进程退出）。"""
    assert tray._thread is not None
    assert tray._thread.daemon is True, "线程不是 daemon，进程退出时会被拖住"


def test_tray_thread_alive_before_stop(tray):
    """start() 后线程应处于运行状态。"""
    assert tray._thread is not None
    assert tray._thread.is_alive(), "托盘线程未运行"


def test_tray_stop_joins_thread(tray):
    """stop() 后线程在 2s 内真正退出（轮询等待 Windows 消息循环回收）。

    不直接 assert not thread.is_alive()：pystray 在 Windows 上依赖消息循环，
    stop() 只发停止信号，线程退出有微小延迟。用轮询给缓冲时间。
    """
    thread = tray._thread  # stop() 会清空 self._thread，先记引用
    assert thread is not None and thread.is_alive()

    tray.stop()

    # 轮询等待线程退出（最多 2s = 20 × 0.1s）
    for _ in range(20):
        if not thread.is_alive():
            break
        time.sleep(0.1)

    assert not thread.is_alive(), "托盘线程在 2s 内未退出"

    # stop() 应清空内部引用
    assert tray._thread is None
    assert tray._icon is None


def test_tray_stop_idempotent(tray):
    """stop() 幂等：多次调用不报错。"""
    tray.stop()
    tray.stop()  # 第二次不应抛异常
    assert tray._thread is None
