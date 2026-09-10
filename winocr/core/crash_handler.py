# -*- coding: utf-8 -*-
"""WinOCR 崩溃与异常捕获 —— 未处理异常转储 + faulthandler 原生信号捕获。

用途：
  最近的 3 次崩溃（ggml-cpu.dll 段错误 ×2 + ucrtbase.dll 栈溢出 ×1）
  没有任何 Python 级别的 traceback，无法定位根因。
  本模块：
    1. 接管 sys.excepthook / threading.excepthook：任何未捕获的 Python 异常
       都会写入 crash-YYYYMMDD-HHMMSS.log，包含完整 traceback + 线程列表 + 系统信息。
    2. 集成 faulthandler：在 SIGSEGV / SIGABRT / SIGFPE 等原生信号触发时
       将 C 级别的 traceback 写入 faulthandler-latest.log（同一目录）。
    3. 接管 Tkinter 回调异常：tk.report_callback_exception 走同一套转储逻辑。

用法：
  from winocr.core.crash_handler import install_crash_handler, patch_tkinter
  install_crash_handler()          # 在 main.py 最早调用（Tk 之前）
  patch_tkinter(root)              # 在 Tk() 创建后调用
"""

from __future__ import annotations

import datetime
import os
import platform
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Optional

# faulthandler 是 CPython 内置模块（3.3+），无需 pip 安装
try:
    import faulthandler
except Exception:
    faulthandler = None  # type: ignore[assignment]

# ---------------------------------------------------------------------
# 内部状态
# ---------------------------------------------------------------------
_installed: bool = False
_crash_dir: Optional[Path] = None
_fh_file: Optional[Any] = None  # faulthandler 文件句柄

_original_excepthook = sys.excepthook
_original_threading_excepthook = threading.excepthook
_original_tk_handler = None
_tk_root: Optional[Any] = None  # patch_tkinter 时记录的 root，uninstall 时恢复用


# ---------------------------------------------------------------------
# 转储内容生成
# ---------------------------------------------------------------------
def _dump_sys_info(f) -> None:
    """把系统信息写入文件句柄 f。"""
    f.write("=" * 70 + "\n")
    f.write(f"Timestamp : {datetime.datetime.now().isoformat()}\n")
    f.write(f"Python    : {sys.version}\n")
    f.write(f"Platform  : {platform.platform()}\n")
    f.write(f"Machine   : {platform.machine()}\n")
    f.write(f"Processor : {platform.processor()}\n")
    f.write(f"OS        : {platform.system()} {platform.release()}\n")
    f.write(f"Executable: {sys.executable}\n")
    f.write(f"Args      : {sys.argv}\n")
    f.write(f"CWD       : {os.getcwd()}\n")
    # 尝试补充已安装包版本（用于排查 DLL 冲突）
    try:
        import importlib.metadata as im

        for pkg in (
            "llama-cpp-python",
            "edge-tts",
            "rapidocr-onnxruntime",
            "tkinter",
            "pillow",
        ):
            try:
                ver = im.version(pkg)
                f.write(f"{pkg:30s}: {ver}\n")
            except Exception:
                pass
    except Exception:
        pass
    f.write("=" * 70 + "\n\n")


def _dump_threads(f) -> None:
    """把当前所有线程的 traceback 写入文件句柄 f。"""
    f.write("--- Active Threads ---\n")
    frames = sys._current_frames()
    for th in threading.enumerate():
        f.write(f"\nThread: {th.name} (ident={th.ident}, daemon={th.daemon})\n")
        tid = th.ident
        frame = frames.get(tid) if tid is not None else None
        if frame:
            traceback.print_stack(frame, file=f)
        else:
            f.write("  (frame not available)\n")
    f.write("\n")


def _write_crash_dump(
    exc_type, exc_value, exc_tb, source: str = "main"
) -> Optional[Path]:
    """把异常信息写入 crash 文件，返回文件路径（写入失败返回 None）。"""
    global _crash_dir
    try:
        dump_dir = _crash_dir or _default_crash_dir()
        dump_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dump_file = dump_dir / f"crash-{ts}.log"
    # 同一秒内多次崩溃（主线程+子线程同时触发）会覆盖前者，加序号防丢
    seq = 1
    while dump_file.exists():
        dump_file = dump_dir / f"crash-{ts}-{seq}.log"
        seq += 1
        if seq > 99:  # 安全阀
            break

    try:
        with open(dump_file, "w", encoding="utf-8") as f:
            f.write(f"CRASH DUMP — source={source}\n\n")
            _dump_sys_info(f)

            if exc_type is not None:
                f.write("--- Python Traceback ---\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
                f.write("\n")

            _dump_threads(f)
    except Exception:
        return None

    # 同时写一条到 logging（如果已初始化）
    try:
        import logging

        logging.getLogger("winocr.crash").error(
            "Crash dumped to %s (source=%s)", dump_file, source
        )
    except Exception:
        pass

    return dump_file


# ---------------------------------------------------------------------
# 目录解析
# ---------------------------------------------------------------------
def _default_crash_dir() -> Path:
    """默认崩溃转储目录：优先复用日志目录，否则 user_dir()/crashes。"""
    # 尝试复用日志目录（与 winocr.log 同目录）
    try:
        from .logging_config import get_log_dir

        log_dir = get_log_dir()
        if log_dir:
            return log_dir
    except Exception:
        pass
    try:
        from .paths import user_dir

        return user_dir() / "crashes"
    except Exception:
        return Path.cwd() / "crashes"


# ---------------------------------------------------------------------
# Hook 实现
# ---------------------------------------------------------------------
def _excepthook(exc_type, exc_value, exc_tb) -> None:
    """sys.excepthook 替代：记录崩溃转储后，仍调用原始 hook 打印到 stderr。"""
    _write_crash_dump(exc_type, exc_value, exc_tb, source="main")
    _original_excepthook(exc_type, exc_value, exc_tb)


def _threading_excepthook(args) -> None:
    """threading.excepthook 替代：记录线程未处理异常。"""
    _write_crash_dump(
        args.exc_type,
        args.exc_value,
        args.exc_traceback,
        source=f"thread:{getattr(args.thread, 'name', 'unknown')}",
    )
    _original_threading_excepthook(args)


def _tk_report_callback_exception(exc_type, exc_value, exc_tb) -> None:
    """Tkinter 回调异常替代。"""
    _write_crash_dump(exc_type, exc_value, exc_tb, source="tkinter")
    if _original_tk_handler is not None:
        _original_tk_handler(exc_type, exc_value, exc_tb)
    else:
        # fallback：直接打印到 stderr
        sys.stderr.write("Tkinter callback exception:\n")
        traceback.print_exception(exc_type, exc_value, exc_tb)


# ---------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------
def install_crash_handler(crash_dir: Optional[str] = None) -> None:
    """安装崩溃捕获：Python 未处理异常 + faulthandler 原生信号。

    应在 setup_logging() 之后、应用逻辑之前尽早调用。
    Tkinter 回调异常需等 Tk() 实例创建后再调 patch_tkinter(root)。
    """
    global _installed, _crash_dir, _fh_file

    if _installed:
        return

    if crash_dir:
        _crash_dir = Path(crash_dir)
    else:
        _crash_dir = _default_crash_dir()
    _crash_dir.mkdir(parents=True, exist_ok=True)

    # 1. Python 未处理异常
    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook

    # 2. faulthandler：SIGSEGV / SIGABRT / SIGFPE / SIGBUS
    if faulthandler is not None:
        fh_path = _crash_dir / "faulthandler-latest.log"
        try:
            _fh_file = open(fh_path, "w", encoding="utf-8")
            faulthandler.enable(file=_fh_file, all_threads=True)
        except Exception:
            _fh_file = None

    _installed = True

    try:
        import logging

        logging.getLogger("winocr.crash").info(
            "Crash handler installed, dumps go to %s", _crash_dir
        )
    except Exception:
        pass


def patch_tkinter(root) -> None:
    """在 Tk() 实例创建后调用，接管其 report_callback_exception。

    Args:
        root: tkinter.Tk 实例（或任何有 report_callback_exception 的 widget）。
    """
    global _original_tk_handler, _tk_root
    if root is None:
        return
    try:
        _original_tk_handler = root.report_callback_exception
        _tk_root = root
        root.report_callback_exception = _tk_report_callback_exception
    except Exception:
        pass


def uninstall_crash_handler() -> None:
    """卸载崩溃捕获（主要用于测试）。"""
    global _installed
    if not _installed:
        return
    sys.excepthook = _original_excepthook
    threading.excepthook = _original_threading_excepthook
    if faulthandler is not None:
        faulthandler.disable()
    if _fh_file is not None:
        try:
            _fh_file.close()
        except Exception:
            pass
    # 恢复 Tkinter 原始回调
    if _tk_root is not None and _original_tk_handler is not None:
        try:
            _tk_root.report_callback_exception = _original_tk_handler
        except Exception:
            pass
    _installed = False
