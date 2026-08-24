# -*- coding: utf-8 -*-
"""WinOCR 一键安装 OneCore 中英语音包（官方通道，非 hack）。

非管理员运行时通过 ShellExecuteW(runas) 提权重启自身 → UAC 弹窗；
管理员分支用 dism /online /Add-Capability 安装 Speech.TTS.zh-CN / en-US。
日志写 D:/WinOCR3.0/_voice_install.log 供外部验证。
"""
import ctypes
import os
import subprocess
import sys

LOG = r"D:\WinOCR3.0\_voice_install.log"
_TARGETS = ["Speech.TTS.zh-CN", "Speech.TTS.en-US"]


def _log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _relaunch_elevated() -> None:
    """提权重启本脚本（触发 UAC）。SW_HIDE=0 让新窗口不弹出。"""
    script = os.path.abspath(__file__)
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script}"', None, 0)


def _find_speech_caps() -> list:
    """查询系统实际可用的 Speech/TextToSpeech capability 名称。"""
    try:
        r = subprocess.run(
            ["dism", "/online", "/Get-Capabilities"],
            capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        _log(f"    Get-Capabilities FAIL: {e}")
        return []
    caps = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("Capability Identity"):
            caps.append(line.split(":", 1)[1].strip())
    return caps


def main() -> int:
    _log(f"== start {os.path.basename(__file__)} ==")
    if not _is_admin():
        _log("not admin -> relaunch elevated (UAC)")
        _relaunch_elevated()
        return 0  # 原进程退出，等提权实例完成

    _log(f"admin OK, py={sys.version.split()[0]}")

    # 1) 列出全部语音能力（诊断用）
    all_caps = _find_speech_caps()
    speech_caps = [c for c in all_caps if "Speech" in c or "TextToSpeech" in c]
    _log(f"available speech caps ({len(speech_caps)}):")
    for c in speech_caps:
        _log(f"    {c}")

    # 2) 从中匹配 zh-CN / en-US 并安装
    installed = 0
    for lang in ("zh-CN", "en-US"):
        targets = [c for c in speech_caps if lang in c]
        if not targets:
            _log(f"--> {lang}: 系统无对应语音能力（当前可用列表见上）")
            continue
        for cap in targets:
            _log(f"--> install {cap}")
            try:
                r = subprocess.run(
                    ["dism", "/online", "/Add-Capability", f"/CapabilityName:{cap}"],
                    capture_output=True, text=True, timeout=600,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                tail = (r.stdout or "").strip().splitlines()
                tail = tail[-3:] if tail else [r.stderr.strip()[:200]]
                _log(f"    rc={r.returncode} | " + " | ".join(tail))
                if r.returncode == 0:
                    installed += 1
            except Exception as e:
                _log(f"    FAIL: {e}")
    _log(f"== done: {installed} installed ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
