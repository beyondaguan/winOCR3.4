# -*- coding: utf-8 -*-
"""WinOCR 全局日志配置 —— 统一格式、文件轮转、崩溃排查。

为什么需要这个模块：
  之前 3 次崩溃（ggml-cpu.dll 段错误 ×2 + ucrtbase.dll 栈溢出 ×1）
  完全没有日志记录，只能通过 Windows 事件查看器猜根因。
  现在：所有模块统一落盘，按大小轮转保留，崩溃时可回溯。

用法：
  from winocr.core.logging_config import setup_logging
  setup_logging()  # 在 main.py 入口最早调用

配置优先级（从高到低）：
  1. 环境变量 WINOCR_LOG_LEVEL / WINOCR_LOG_DIR
  2. config.toml [logging] 段（log_level / log_dir）
  3. 默认：INFO 级别，日志放 user_dir()/logs/

与现有 selection 日志的兼容：
  _ensure_sel_logging 仍保留（向后兼容），但 setup_logging() 会
  把 winocr.selection 也挂到同一个文件 handler 上，避免两套日志。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------
# 默认常量
# ---------------------------------------------------------------------
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_DIR = None  # 运行时从 paths.user_dir() 推导
MAX_BYTES = 5 * 1024 * 1024  # 5 MB 单文件上限
BACKUP_COUNT = 3  # 保留 3 份历史
LOG_FORMAT = "[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------
# 内部状态（防重复初始化）
# ---------------------------------------------------------------------
_initialized: bool = False


def _normalize_level(level: Optional[str]) -> int:
    """把字符串级别转成 logging 常量，非法时回落 INFO。"""
    if level is None:
        return logging.INFO
    try:
        return int(logging.getLevelName(level.upper()))
    except (ValueError, TypeError):
        return logging.INFO


def _resolve_log_dir(cfg_dir: Optional[str] = None) -> Path:
    """确定日志目录，自动创建。

    优先级：环境变量 > 配置 > user_dir()/logs
    """
    env = os.environ.get("WINOCR_LOG_DIR")
    if env:
        p = Path(env)
    elif cfg_dir:
        p = Path(cfg_dir)
    else:
        # 延迟导入 paths，避免循环依赖（paths 也 import logging）
        from .paths import user_dir

        p = user_dir() / "logs"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _setup_file_handler(
    log_dir: Path, level: int
) -> logging.handlers.RotatingFileHandler:
    """创建按大小轮转的 RotatingFileHandler。"""
    log_file = log_dir / "winocr.log"
    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    return handler


def _setup_console_handler(level: int) -> Optional[logging.StreamHandler]:
    """创建控制台 handler（仅在有真实控制台时，避免 pythonw 写 None stdout 抛异常）。"""
    # pythonw 启动时 sys.stdout 为 None
    if sys.stdout is None or sys.stderr is None:
        return None
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    return handler


def _patch_sel_logging(file_handler: logging.Handler) -> None:
    """把 winocr.selection 日志也接到文件 handler，避免 _ensure_sel_logging 单独落盘。

    _ensure_sel_logging 在 app.py 中被调用，它可能配置了独立的 NullHandler。
    这里补充一个文件 handler，使 selection 日志与全局日志共存于 winocr.log。
    """
    sel_logger = logging.getLogger("winocr.selection")
    # 只添加一次（通过名称检查防重）
    for h in sel_logger.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            return
    sel_logger.addHandler(file_handler)
    # 如果之前被设为 WARNING 以上，这里保持 WARNING（不降级，避免噪音）
    # 但确保至少能写文件
    if sel_logger.level == logging.NOTSET or sel_logger.level > logging.WARNING:
        sel_logger.setLevel(logging.WARNING)


# ---------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------
def setup_logging(
    level: Optional[str] = None,
    log_dir: Optional[str] = None,
    console: bool = True,
) -> None:
    """初始化 WinOCR 全局日志系统。

    应在应用入口（main.py）最早调用，且只调用一次。

    Args:
        level:  日志级别字符串（DEBUG/INFO/WARNING/ERROR），None 时读环境变量或默认 INFO。
        log_dir: 日志目录路径，None 时读环境变量或默认 user_dir()/logs。
        console: 是否同时输出到控制台（pythonw 会自动跳过）。
    """
    global _initialized
    if _initialized:
        return

    # 1. 解析级别
    env_level = os.environ.get("WINOCR_LOG_LEVEL")
    final_level = _normalize_level(level or env_level or DEFAULT_LOG_LEVEL)

    # 2. 解析目录
    final_dir = _resolve_log_dir(log_dir)

    # 3. 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(final_level)

    # 4. 文件 handler（核心：所有模块统一落盘）
    file_handler = _setup_file_handler(final_dir, final_level)
    root_logger.addHandler(file_handler)

    # 5. 控制台 handler（可选，pythonw 自动跳过）
    if console:
        console_handler = _setup_console_handler(final_level)
        if console_handler:
            root_logger.addHandler(console_handler)

    # 6. 把 selection 日志也挂进来（兼容 _ensure_sel_logging）
    _patch_sel_logging(file_handler)

    # 7. 降低第三方库噪音（只保留 WARNING 以上）
    for noisy in ("urllib3", "requests", "aiohttp", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _initialized = True
    logging.getLogger(__name__).info(
        "Logging initialized: level=%s, dir=%s",
        logging.getLevelName(final_level),
        final_dir,
    )


def is_initialized() -> bool:
    """日志系统是否已初始化（用于测试和防止重复 setup）。"""
    return _initialized


def get_log_dir() -> Optional[Path]:
    """返回当前日志目录（未初始化时返回 None）。"""
    if not _initialized:
        return None
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            return Path(h.baseFilename).parent
    return None
