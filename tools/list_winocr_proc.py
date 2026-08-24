# -*- coding: utf-8 -*-
"""列出所有 WinOCR / Python 相关进程，并按「解释器身份 PYID」对账。

人机一致性
----------
虚拟环境下「AI / 工具脚本跑的解释器」与「双击 run.bat 实际起的进程」常不是同一个进程：
  - run.bat 用 .venv\\Scripts\\pythonw.exe main.py（它是 shim，会再拉起真实解释器）
  - 工具脚本 / 测试可能直接跑 D:\\Documents\\Programs\\Python312\\python.exe

两者都会加载 D:\\winOCR3.4\\winocr 源码，但 PID 不同。排查「改了代码没生效 /
杀了旧进程又起新进程」时，先跑本脚本确认实际在跑哪个解释器。

PYID 与「固定 ID」
-----------------
OS 的进程 PID 是**动态**的，每次运行都会变，无法当固定身份。
真正固定的是「解释器可执行文件」——同一台机器同一份虚拟环境，它的路径每次相同。
所以把「resolive 到真实解释器后的绝对路径」做 sha1 取前 10 位得到一个短 ID，叫 PYID：
  - PYID 只取决于解释器路径，不取决于 PID → 每次运行都相同 = 固定。
  - 判断「AI 跑的和我 run.bat 跑的是不是同一个解释器」= 比 PYID 是否相同，与 PID 无关。

用法
----
    python tools/list_winocr_proc.py                 # 看 WinOCR 相关进程 + PYID + 归并
    python tools/list_winocr_proc.py --all           # 列出所有 python 进程
    python tools/list_winocr_proc.py --pid 2344      # 只看某个 PID
    python tools/list_winocr_proc.py --pyid a1b2c3d4 # 反查：哪些进程属同一解释器
"""
import hashlib
import json
import re
import subprocess
import sys

PS_ALL = (
    "Get-CimInstance Win32_Process "
    "| Where-Object { $_.Name -match 'python' } "
    "| Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine "
    "| ConvertTo-Json"
)

PS_ONE = (
    "Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' "
    "| Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine "
    "| ConvertTo-Json"
)

_SHIM_RE = re.compile(r"\\venv\\[^\\]*\\scripts\\python(w)?\.exe$", re.I)


def _norm(exe: str) -> str:
    return (exe or "").replace("/", "\\").replace("\\\\", "\\").lower()


def _is_shim(exe: str) -> bool:
    """是否 venv 重定向器（run.bat 的 .venv\\Scripts\\pythonw.exe 这类）。"""
    return bool(_SHIM_RE.search(_norm(exe)))


def _pyid(exe: str) -> str:
    """固定解释器身份：resolive 到真实解释器路径后做 sha1 短哈希。

    它只依赖解释器路径，不依赖动态 PID → 每次运行相同，可作为人与 AI 对账的固定 ID。
    """
    n = _norm(exe)
    return hashlib.sha1(n.encode("utf-8")).hexdigest()[:10]


def _query(ps: str) -> list:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        print(f"[错误] 查询失败：{e}", file=sys.stderr)
        return []
    out = r.stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def _is_winocr(row: dict) -> bool:
    cmd = (row.get("CommandLine") or "").lower()
    return "main.py" in cmd or "winocr" in cmd


def _resolve_real(exe: str) -> str:
    """把 venv 重定向器解析到真实解释器——读 pyvenv.cfg 的 home，确定性路径。

    为什么不沿父→子进程找：`.venv\\Scripts\\pythonw.exe` 会拉起子进程（进程表里能看到），
    但 `.venv\\Scripts\\python.exe`（如 pytest）有时不产生可见子进程；两者逻辑上同一解释器，
    走进程树会得出不同 PYID，误导对账。固定做法是读 `<venv>\\pyvenv.cfg` 的 home，
    home 即基础 Python 安装目录，再按 shim 名字保持 pythonw/python 后缀。
    """
    n = _norm(exe)
    m = re.search(r"^(.*)\\scripts\\python(w)?\.exe$", n)
    if not m:
        return exe
    vroot = m.group(1)
    cfg = vroot + "\\pyvenv.cfg"
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip().lower() == "home":
                        home = v.strip().replace("/", "\\")
                        w = m.group(2) or ""
                        return home + ("\\pythonw.exe" if w else "\\python.exe")
    except Exception:
        pass
    return exe


def main() -> int:
    args = sys.argv[1:]
    want = None
    if "--pyid" in args:
        want = args[args.index("--pyid") + 1]
        rows = _query(PS_ALL)
    elif "--pid" in args:
        rows = _query(PS_ONE.format(pid=args[args.index("--pid") + 1]))
    else:
        rows = _query(PS_ALL)

    if not rows:
        print("（无）")
        return 0

    all_py = "--all" in args

    # 计算每个进程的 PYID 与真实解释器路径
    for r in rows:
        real = _resolve_real(r.get("ExecutablePath") or "")
        r["REAL"] = real
        r["PYID"] = _pyid(real)

    print(f"{'PYID':>10} {'PID':>7} {'Parent':>7}  name       真实解释器（resolved）")
    print("-" * 96)
    for r in sorted(rows, key=lambda x: x.get("ProcessId", 0)):
        if want and r["PYID"] != want:
            continue
        if not all_py and not _is_winocr(r):
            continue
        exe = r["REAL"] or "?"
        name = r.get("Name") or "?"
        pid = r.get("ProcessId")
        ppid = r.get("ParentProcessId")
        cmd = " ".join((r.get("CommandLine") or "").split())
        flag = "  <<< WinOCR" if _is_winocr(r) else ""
        print(f"{r['PYID']:>10} {pid:>7} {ppid:>7}  {name:<10}{exe}")
        print(f"                 cmd: {cmd}{flag}")

    if not want:
        # 树状图
        print("\n进程树（WinOCR 相关，含父链）：")
        pids = {r.get("ProcessId"): r for r in rows}
        for r in rows:
            if not _is_winocr(r):
                continue
            chain = []
            cur = r
            seen = 0
            while cur and seen < 10:
                chain.append(cur)
                seen += 1
                parent = pids.get(cur.get("ParentProcessId"))
                if not parent:
                    break
                cur = parent
            chain.reverse()
            parts = [f"{c.get('ProcessId')}:{c.get('Name')}"
                     + ("" if _is_shim(c.get('ExecutablePath') or "") else f"[{_pyid(c.get('REAL'))}]")
                     for c in chain]
            print("   → ".join(parts))

        # 按 PYID 归并，直观对账「谁跟谁是同一个解释器」
        groups = {}
        for r in rows:
            if _is_winocr(r):
                groups.setdefault(r["PYID"], []).append(r.get("ProcessId"))
        print("\n解释器身份归并（同一 PYID = 同一真实解释器）：")
        if not groups:
            print("  （无 WinOCR 进程）")
        for pyid in sorted(groups):
            real = next((r["REAL"] for r in rows if r["PYID"] == pyid), "?")
            print(f"  PYID {pyid}  [{', '.join(map(str, groups[pyid]))}]  {real}")

        if sys.platform == "win32":
            print("\n提示：PID 一运行一变，别拿它当身份。判断人/机用的是不是同一解释器，"
                  "请比对上面的 PYID（由解释器路径算出，每次固定）。可用 "
                  "--pyid <PYID> 反查同一解释器下的进程。")
    return 0


if __name__ == "__main__":
    sys.exit(main())