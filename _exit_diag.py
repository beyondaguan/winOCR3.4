# -*- coding: utf-8 -*-
"""启动 WinOCR GUI，观测退出时的进程行为（诊断用，用完即删）。"""
import subprocess, os, time, sys

here = r"D:\winOCR3.4"
venv_py = os.path.join(here, ".venv", "Scripts", "pythonw.exe")

def list_pythonw():
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
         "Select-Object ProcessId, ParentProcessId, CommandLine | ConvertTo-Json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout.strip()
    if not out:
        return []
    import json
    d = json.loads(out)
    return d if isinstance(d, list) else [d]

# 启动
p = subprocess.Popen([venv_py, "main.py"], cwd=here)
print(f"launched {venv_py}, Popen pid={p.pid}")
time.sleep(4)
print(f"\n=== 4s after launch ===")
for x in list_pythonw():
    print(f"  pid={x.get('ProcessId')} parent={x.get('ParentProcessId')} cmd={x.get('CommandLine')}")

print("\nWaiting 30s for user to right-click tray -> Exit... (or we exit test)")
time.sleep(30)
print(f"\n=== after 30s ===")
procs = list_pythonw()
if not procs:
    print("  NO pythonw processes -> clean exit!")
else:
    print("  REMAINING pythonw:")
    for x in procs:
        print(f"  pid={x.get('ProcessId')} parent={x.get('ParentProcessId')} cmd={x.get('CommandLine')}")