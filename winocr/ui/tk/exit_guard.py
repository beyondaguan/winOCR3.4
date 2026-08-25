# -*- coding: utf-8 -*-
"""进程退出守卫 — 处理 run.bat 的 .venv shim 宿主进程残留（P-11/P-13）。

run.bat 用 `.venv\\Scripts\\pythonw.exe main.py` 启动，该 shim 是重定向器，
会再拉起真正解释器作为子进程。子进程内 os._exit(0) 只退自已，shim 父进程残留，
导致下次 run.bat 又要 stop_winocr.py 杀进程。

从 app.py 拆出：纯函数、零 UI 依赖，便于独立测试。
"""
from __future__ import annotations

import ctypes as _ct
import os as _os
import subprocess as _subprocess
from ctypes import wintypes as _wt


def win32_parent_info():
    """返回 (父进程PID, 父进程可执行文件全路径)。

    仅 Windows。通过 Toolhelp 快照查父 PID，再用 GetProcessImageFileNameW
    取父进程 image 路径（形如 \\\\Device\\...\\.venv\\Scripts\\pythonw.exe）。
    无父进程 / 非 Windows 时返回 (None, None)。
    """
    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    MAXP = _wt.MAX_PATH

    class _PROCESSENTRY32W(_ct.Structure):
        _fields_ = [
            ("dwSize", _wt.DWORD),
            ("cntUsage", _wt.DWORD),
            ("th32ProcessID", _wt.DWORD),
            ("th32DefaultHeapID", _ct.POINTER(_ct.c_ulong)),
            ("th32ModuleID", _wt.DWORD),
            ("cntThreads", _wt.DWORD),
            ("th32ParentProcessID", _wt.DWORD),
            ("pcPriClassBase", _ct.c_long),
            ("dwFlags", _wt.DWORD),
            ("szExeFile", _ct.c_wchar * MAXP),
        ]

    k32 = _ct.windll.kernel32
    cur = _os.getpid()
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, _wt.HANDLE(-1).value):
        return None, None
    parent_pid = None
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = _ct.sizeof(_PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, _ct.byref(e)):
            return None, None
        while True:
            if e.th32ProcessID == cur:
                parent_pid = e.th32ParentProcessID
                break
            if not k32.Process32NextW(snap, _ct.byref(e)):
                return None, None
    finally:
        k32.CloseHandle(snap)
    if not parent_pid:
        return None, None

    hproc = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, parent_pid)
    if not hproc:
        return parent_pid, None
    try:
        buf = _ct.create_unicode_buffer(MAXP)
        size = _wt.DWORD(MAXP)
        ok = k32.QueryFullProcessImageNameW(hproc, 0, buf, _ct.byref(size))
        exe = buf.value if ok else None
    finally:
        k32.CloseHandle(hproc)
    return parent_pid, exe


def current_cmdline_has_main_py() -> bool:
    """当前进程命令行是否含 main.py（run.bat 的 GUI 进程才满足）。

    用 GetCommandLineW 读当前进程命令行，不依赖 PowerShell（pythonw 下
    PowerShell 查命令行会返回空）。pytest / 工具脚本由 .venv shim 拉起但
    命令行是 `-m pytest ...`，不含 main.py，据此区分「run.bat 起的 GUI」。
    """
    try:
        k32 = _ct.windll.kernel32
        k32.GetCommandLineW.restype = _ct.c_wchar_p
        cmd = k32.GetCommandLineW() or ""
        return "main.py" in cmd.lower()
    except Exception:
        return False


def force_exit_venv_tree():
    """进程真正退出（含 run.bat 的 .venv shim 宿主进程）。

    仅当「父进程是 .venv 下的 python/pythonw（venv shim）**且** 当前进程
    命令行含 main.py（确认是 run.bat 起的 GUI）」时，才对父进程 tree 强制
    taskkill（连带本进程一起结束）；否则只 os._exit(0)。

    第二个条件至关重要：pytest 等测试进程同样由 .venv shim 拉起（shim→真身），
    若只看父进程是 venv shim 就会把 pytest 的 shim 父进程 taskkill 掉，导致
    整个测试进程被自己发出的 taskkill /T 连根杀死（无 traceback 的 exit=1）。
    """
    ppid, pexe = win32_parent_info()
    if pexe and ppid:
        pe = pexe.replace("/", "\\")
        is_venv_shim = ("\\.venv\\" in pe
                        and pe.lower().endswith(("python.exe", "pythonw.exe")))
        if is_venv_shim and current_cmdline_has_main_py():
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            try:
                _subprocess.Popen(
                    ["taskkill", "/F", "/T", "/PID", str(ppid)],
                    creationflags=flags,
                    stdin=_subprocess.DEVNULL,
                    stdout=_subprocess.DEVNULL,
                    stderr=_subprocess.DEVNULL,
                    close_fds=True,
                )
            except Exception:
                pass
            _os._exit(0)
            return
    _os._exit(0)
