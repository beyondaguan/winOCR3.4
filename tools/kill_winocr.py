# -*- coding: utf-8 -*-
"""关闭所有 WinOCR 进程。

按进程命令行匹配 main.py / winocr，避免误杀其它 Python 程序。

用法：
    python tools/kill_winocr.py
    或双击运行

依赖：仅标准库（通过 PowerShell 查命令行）。
"""
import subprocess
import sys

# 只杀命令行含 main.py / winocr 的 python 进程；其它 python 程序（如 pip、IDE）
# 命令行不含这些字样，毫发无损。
PS_LIST = (
    "Get-CimInstance Win32_Process "
    "-Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
    "| Where-Object { $_.CommandLine -and ($_.CommandLine -like '*main.py*' "
    "-or $_.CommandLine -like '*winocr*' -or $_.CommandLine -like '*WinOCR*') } "
    "| Select-Object ProcessId,Name,CommandLine "
    "| ConvertTo-Csv -NoTypeInformation"
)


def find_pids() -> list:
    """返回所有 WinOCR 进程的 PID 列表。"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", PS_LIST],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        print("[错误] 找不到 powershell（需要 Windows 系统）", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[错误] 查询进程失败：{e}", file=sys.stderr)
        return []

    out = result.stdout.strip()
    if not out:
        return []
    pids = []
    lines = out.splitlines()
    for line in lines[1:]:                                  # 跳过 CSV header
        parts = line.split(",")
        if not parts:
            continue
        try:
            pid = int(parts[0].strip().strip('"'))
            pids.append(pid)
        except (ValueError, IndexError):
            continue
    return pids


def kill_pids(pids: list) -> int:
    killed = 0
    for pid in pids:
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                print(f"[已结束] PID={pid}")
                killed += 1
            else:
                err = (r.stdout + r.stderr).strip() or "未知"
                print(f"[失败] PID={pid}: {err}", file=sys.stderr)
        except Exception as e:
            print(f"[失败] PID={pid}: {e}", file=sys.stderr)
    return killed


def main() -> int:
    print("正在查找 WinOCR 进程...")
    pids = find_pids()
    if not pids:
        print("没有找到 WinOCR 进程。")
        return 0
    print(f"找到 {len(pids)} 个候选进程：{pids}")
    n = kill_pids(pids)
    print(f"完成，共结束 {n} 个进程。")
    return 0


if __name__ == "__main__":
    sys.exit(main())