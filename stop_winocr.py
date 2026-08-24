# -*- coding: utf-8 -*-
"""彻底关闭 WinOCR（改配置/升级后需重启时用）。

用法：
    python stop_winocr.py          # 优雅退出优先，超时强杀
    python stop_winocr.py --force  # 直接强杀
    python stop_winocr.py --quick  # 无旧实例秒退；有旧实例则走优雅+强杀（run.bat 用）

策略：只结束命令行里含 main.py 的 python/pythonw 进程，
绝不误伤其它 Python 程序。优雅关闭先发 WM_CLOSE 等 3 秒，
仍有残留则按 PID 强杀，最后汇总报告。
"""
from __future__ import annotations

import subprocess
import sys
import time

FORCE = "--force" in sys.argv
QUICK = "--quick" in sys.argv

# 每步都是独立的 PowerShell 单行命令（无 cmd 参与，避免转义问题）
def _ps(code: str) -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", code],
            capture_output=True, timeout=60,
        )
        return r.stdout.decode("utf-8", errors="replace").strip()
    except Exception as e:
        return f"[powershell 调用失败: {e}]"


FIND = (
    "Get-CimInstance Win32_Process "
    "| Where-Object { $_.Name -match '^python(w)?\\.exe$' } "
    "| Where-Object { try { $_.CommandLine -match 'main\\.py' } catch { $false } } "
    "| Select-Object -ExpandProperty ProcessId"
)


def main() -> int:
    if QUICK:
        pids = _ps(FIND)
        if not pids:
            print("无旧实例在跑，直接启动新版本。")
            return 0
        print(f"检测到旧实例（PID: {pids}），先关闭再启动...")
    if not FORCE:
        print("[1/3] 尝试优雅退出（发 WM_CLOSE 给 WinOCR 主窗口）...")
        _ps(
            "Get-CimInstance Win32_Process "
            "| Where-Object { $_.Name -match '^python(w)?\\.exe$' } "
            "| ForEach-Object { try { $c = $_.CommandLine } catch { return }; "
            "  if ($c -match 'main\\.py') { "
            "    $proc = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; "
            "    if ($proc) { [void]$proc.CloseMainWindow() } } }"
        )
        time.sleep(3)
    else:
        print("[1/3] 强杀模式（跳过优雅退出）...")

    print("[2/3] 检查是否还有残留进程...")
    pids = _ps(FIND)
    if not pids:
        print("  已全部退出。")
        time.sleep(1)
        pids2 = _ps(FIND)
        print("[3/3] 最终确认: " + ("已全部关闭，可以重启了。" if not pids2 else f"仍有 {pids2} 个进程"))
        return 0

    pid_list = [p for p in pids.splitlines() if p.strip().isdigit()]
    print(f"  残留 {len(pid_list)} 个进程（PID: {', '.join(pid_list)}），执行强杀...")
    for pid in pid_list:
        _ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")

    time.sleep(1)
    left = _ps(FIND)
    if not left:
        print("[3/3] 已全部关闭，可以重启了。")
    else:
        print(f"[3/3] 仍有进程未能关闭（{left}）。\n"
              f"      可能原因：权限不足，请以管理员身份运行：\n"
              f"          python stop_winocr.py --force")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
