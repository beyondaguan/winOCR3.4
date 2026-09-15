# -*- coding: utf-8 -*-
"""CPU 占用率硬限制（Windows Job Object）。

跑本地大模型时把进程整体 CPU 占用率压在配置的百分比以下，避免推理
把核吃满、系统卡顿。基于 Windows 8+ 的 Job Object CPU Rate Control
（500ms 调度窗口内的硬性配额），不是「降优先级」那种软手段——被压住
时线程真的拿不到更多时间片。

用法：
    from winocr.core.cpu_limit import apply_cpu_limit
    apply_cpu_limit(70)   # 进程 CPU ≤ 70%；失败静默返回 False

生效范围：当前 Python 进程的全部线程（llama.cpp 推理线程、ctranslate2
线程都在其中）。Ollama 是独立进程，不受影响。
配置优先级：环境变量 WINOCR_CPU_LIMIT > config（translate/ai 的
cpu_limit 字段，0 = 不限制）。幂等：同一进程重复调用只设置一次。
"""
from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Process / Job Object 常量（winnt.h）
_JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
_JOB_OBJECT_CPU_RATE_CONTROL_HARD_LIMIT = 0x4  # 0x2 是 WEIGHT_BASED，别混
_JobObjectCpuRateControlInformation = 15  # JOBOBJECTINFOCLASS（winnt.h）

_MIN_PERCENT = 10    # Job Object 硬限最低配额：1000/10000 = 10%
_MAX_PERCENT = 95    # 100% 无意义（等于不设），留 5% 余量给系统

_job_handle: wintypes.HANDLE | None = None
_applied_percent = 0


class _CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("ControlFlags", wintypes.DWORD),
        ("CpuRate", wintypes.DWORD),  # 占用率×100（100% = 10000）
    ]


def _k32() -> ctypes.WinDLL:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def effective_percent() -> int:
    """环境变量 > 调用方传入值的公共读取点（0 = 不限制）。"""
    try:
        return int(os.environ.get("WINOCR_CPU_LIMIT", "0") or 0)
    except ValueError:
        return 0


def apply_cpu_limit(percent: int) -> bool:
    """给当前进程设置 CPU 占用率硬上限。成功返回 True。

    percent 会被钳制到 [10, 95]；<=0 直接忽略（不限制）。
    Win7 及更早（无 CpuRateControl）或 Job 嵌套受限时静默失败。
    """
    global _job_handle, _applied_percent

    if percent <= 0:
        return False
    percent = max(_MIN_PERCENT, min(_MAX_PERCENT, int(percent)))
    if _applied_percent == percent and _job_handle:
        return True  # 幂等：同一百分比不重复设置

    try:
        k32 = _k32()
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE,
        ]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcess.argtypes = []

        if not _job_handle:
            _job_handle = k32.CreateJobObjectW(None, None)
            if not _job_handle:
                raise OSError("CreateJobObjectW failed")
            # 注意：只 Assign 一次——重复 Assign 同一进程无害，但没必要
            if not k32.AssignProcessToJobObject(
                _job_handle, k32.GetCurrentProcess()
            ):
                raise OSError("AssignProcessToJobObject failed")

        info = _CPU_RATE_CONTROL_INFORMATION(
            ControlFlags=(_JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                          | _JOB_OBJECT_CPU_RATE_CONTROL_HARD_LIMIT),
            CpuRate=percent * 100,
        )
        if not k32.SetInformationJobObject(
            _job_handle, _JobObjectCpuRateControlInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            raise OSError(f"SetInformationJobObject failed "
                          f"(GetLastError={ctypes.get_last_error()})")

        _applied_percent = percent
        logger.info("[cpu_limit] 进程 CPU 占用率硬限制: %d%%", percent)
        return True
    except OSError as e:
        # Win7 无 CpuRateControl / 嵌套 Job 权限受限等：静默降级
        logger.debug("[cpu_limit] 设置失败（忽略）: %s", e)
        return False


def applied_percent() -> int:
    """当前已生效的限制百分比（0 = 未限制）。"""
    return _applied_percent


def query_cpu_rate() -> int | None:
    """从内核读回实际生效的 CpuRate（percent×100）；未设置返回 None。

    用于诊断与测试：确认 Job Object 配额真的写入成功了。
    """
    if not _job_handle:
        return None
    try:
        k32 = _k32()
        k32.QueryInformationJobObject.restype = wintypes.BOOL
        k32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        info = _CPU_RATE_CONTROL_INFORMATION()
        ret = wintypes.DWORD(0)
        if not k32.QueryInformationJobObject(
            _job_handle, _JobObjectCpuRateControlInformation,
            ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(ret),
        ):
            return None
        return int(info.CpuRate)
    except OSError:
        return None
