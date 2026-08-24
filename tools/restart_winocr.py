# -*- coding: utf-8 -*-
"""杀 WinOCR 进程并自动重新启动 run.bat，一键加载最新代码。

用法：python tools/restart_winocr.py
"""
import subprocess
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 复用 kill_winocr 的逻辑
sys.path.insert(0, str(ROOT / "tools"))
from kill_winocr import find_pids, kill_pids           # noqa: E402

print("=" * 50)
print("WinOCR 重启工具")
print("=" * 50)

# 1. 杀进程
print("\n[1/3] 查找并结束 WinOCR 进程...")
pids = find_pids()
if pids:
    n = kill_pids(pids)
    print(f"  → 结束 {n} 个进程")
else:
    print("  → 没有运行中的 WinOCR 进程")

# 2. 启动 run.bat
print("\n[2/3] 启动 run.bat ...")
run_bat = ROOT / "run.bat"
if not run_bat.exists():
    print(f"  → 找不到 {run_bat}", file=sys.stderr)
    sys.exit(1)

# CREATE_NEW_CONSOLE 让 GUI 程序独立窗口跑（run.bat 是 .bat，调 pythonw.exe）
DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010
try:
    subprocess.Popen(
        ["cmd", "/c", str(run_bat)],
        cwd=str(ROOT),
        creationflags=DETACHED_PROCESS | CREATE_NEW_CONSOLE,
        close_fds=True,
    )
    print(f"  → 已启动 run.bat（独立窗口，5 秒左右显示主界面）")
except Exception as e:
    print(f"  → 启动失败：{e}", file=sys.stderr)
    sys.exit(1)

print("\n[3/3] 完成。等几秒新窗口出现即可。")
print("=" * 50)