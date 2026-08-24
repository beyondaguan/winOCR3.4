# -*- coding: utf-8 -*-
"""pytest 公共配置：统一 GUI 显示环境检测 + headless 开关 + 数据目录隔离。

替代各测试文件里重复的 _has_display() 函数，消除 CI 环境变量"串味"问题：
- 旧方案用通用 CI=1 判断，本机开发时残留会导致 GUI 测试全跳过；
- 新方案用 WINOCR_CI 专属变量 + --headless-gui 命令行参数，互不干扰。
"""
from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_winocr_home(tmp_path_factory):
    """把 WINOCR_HOME 重定向到 pytest 临时目录，隔离测试数据。

    不设置的话，测试会读写真实 ~/.winocr（config / history / knowledge.db /
    chat_history / selection.log），既污染用户数据，又让测试结果依赖本机状态、
    不可重复、在沙箱/CI 里被权限拦截。paths.user_dir() 每次调用都读
    WINOCR_HOME（无 import 时缓存），所以 session 开始设一次即全局生效。
    """
    home = tmp_path_factory.mktemp("winocr_home")
    os.environ["WINOCR_HOME"] = str(home)
    return home


def pytest_addoption(parser):
    parser.addoption(
        "--headless-gui",
        action="store_true",
        default=False,
        help="跳过所有需要 Tk 显示环境的 GUI 测试（CI / 无头环境用）",
    )


def has_display() -> bool:
    """当前环境是否有可用的 Tk 显示环境。

    三层判断：
    1) WINOCR_CI 环境变量非空 → False（CI 环境）
    2) --headless-gui 命令行参数 → False
    3) 尝试创建 Tk root → 成功为 True，失败为 False
    """
    if os.environ.get("WINOCR_CI"):
        return False
    if "--headless-gui" in sys.argv:
        return False
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


# 模块级缓存：一次检测，整个 session 复用（Tk 创建有开销）
_DISPLAY_AVAILABLE = None


def display_available() -> bool:
    global _DISPLAY_AVAILABLE
    if _DISPLAY_AVAILABLE is None:
        _DISPLAY_AVAILABLE = has_display()
    return _DISPLAY_AVAILABLE


# ------------------------------------------------------------------
# PEP 544 Protocol：TkUi 期望 window 对象实现的全部接口。
# FakeWindow / Mock 对象实现此 Protocol，mypy 静态检查可捕获接口漂移。
# 运行时零开销（Protocol 是 ABC 的轻量替代，不做 isinstance 检查）。
# ------------------------------------------------------------------
try:
    from typing import Protocol
except ImportError:          # Python 3.7 兼容（不安装 typing_extensions 时降级）
    Protocol = object        # type: ignore

class WindowProtocol(Protocol):
    """TkUi 调用 self.window 的全部方法契约。

    新增 TkUi → window 的调用时，必须同步更新此 Protocol，
    mypy 会在测试 FakeWindow 缺方法时报错，从源头防止接口漂移。
    """
    def set_status(self, text: str) -> None: ...
    def set_busy(self, busy: bool) -> None: ...
    def show_original(self, text: str) -> None: ...
    def show_translation(self, result) -> None: ...
    def refresh_engine_label(self) -> None: ...
    def get_selected_text(self) -> str: ...
    def get_original(self) -> str: ...
    def get_translation(self) -> str: ...
    def lang_label(self, code: str) -> str: ...
    def show_sticker(self, original: str, translation: str = "") -> None: ...
    @property
    def last_image(self): ...
    @last_image.setter
    def last_image(self, value) -> None: ...
