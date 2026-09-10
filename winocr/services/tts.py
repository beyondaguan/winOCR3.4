# -*- coding: utf-8 -*-
"""朗读（TTS）服务 —— 两级引擎，断网也有声音。

为什么两级：
  - **edge**：微软 Edge 在线 Neural 语音，音质接近真人，免费无 key，但要联网。
  - **sapi**：Windows 自带 System.Speech（SAPI5），机械但**永远可用**。

`engine="auto"`（默认）时先试 edge，任何失败（无网、被墙、超时、音色不存在）
都自动降级到 sapi。这条降级链是这个模块存在的全部理由：
用户点「朗读」最不能接受的是「点了没反应」，宁可机械音也要出声。

播放为什么不用第三方库：
  edge-tts 产出 mp3，`winsound` 只吃 wav。这里直接用 Windows 原生
  **MCI**（`winmm.dll` 的 `mciSendStringW`）播放 mp3 —— 系统自带解码器，
  零新增依赖，且支持随时 stop。

线程模型：
  `speak()` 立即返回，真正的合成+播放在后台守护线程里跑。
  同一时刻只允许一个朗读任务，新任务会先停掉旧的（用户连点两次
  应该是「换成读这段」，而不是两个声音叠在一起）。
"""

from __future__ import annotations

import atexit
import base64
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import uuid
from typing import Callable, List, Optional

# 一次朗读的文本上限：太长的文本合成慢、也没人真听完，截断更诚实
MAX_CHARS = 4000

# 常用中英音色（不联网也能填给用户选，实际可用性由 edge 侧决定）
PRESET_VOICES: List[tuple] = [
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声（中文，自然）"),
    ("zh-CN-YunxiNeural", "云希 · 男声（中文，活泼）"),
    ("zh-CN-YunjianNeural", "云健 · 男声（中文，沉稳）"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 女声（中文，甜美）"),
    ("zh-TW-HsiaoChenNeural", "曉臻 · 女声（台湾）"),
    ("en-US-AriaNeural", "Aria · 女声（英语 US）"),
    ("en-US-GuyNeural", "Guy · 男声（英语 US）"),
    ("en-GB-SoniaNeural", "Sonia · 女声（英语 UK）"),
    ("ja-JP-NanamiNeural", "七海 · 女声（日语）"),
]


def _clean(text: str) -> str:
    """去掉 Markdown 记号与多余空白 —— 不然会把「星号星号」读出来。"""
    t = text or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)  # 代码块整段跳过
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"[*_#>|]+", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # 链接只读标题
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_CHARS]


class _MciPlayer:
    """MCI 播放器薄封装：open → play → (wait) → close，支持中途 stop。"""

    def __init__(self) -> None:
        self._alias: Optional[str] = None
        self._lock = threading.Lock()

    def _send(self, cmd: str) -> int:
        import ctypes

        return ctypes.windll.winmm.mciSendStringW(cmd, None, 0, None)

    def play(self, path: str, wait_flag: threading.Event) -> bool:
        """播放并阻塞到结束或被 stop；返回是否正常放完。"""
        import ctypes

        alias = "winocr_tts_" + uuid.uuid4().hex[:8]
        if self._send(f'open "{path}" type mpegvideo alias {alias}') != 0:
            # 某些系统 mp3 走 waveaudio 更稳，退一步再试
            if self._send(f'open "{path}" alias {alias}') != 0:
                return False
        with self._lock:
            self._alias = alias
        try:
            if self._send(f"play {alias}") != 0:
                return False
            # 轮询播放状态：比 "play wait" 好，因为可被 stop 打断
            buf = ctypes.create_unicode_buffer(64)
            while not wait_flag.is_set():
                ctypes.windll.winmm.mciSendStringW(
                    f"status {alias} mode", buf, 64, None
                )
                if buf.value.strip() != "playing":
                    break
                time.sleep(0.05)
            return not wait_flag.is_set()
        finally:
            self._send(f"close {alias}")
            with self._lock:
                if self._alias == alias:
                    self._alias = None

    def stop(self) -> None:
        with self._lock:
            alias = self._alias
        if alias:
            try:
                self._send(f"stop {alias}")
                self._send(f"close {alias}")
            except Exception:
                pass


class _SapiHost:
    """常驻 PowerShell + System.Speech 宿主：**一次启动，多次朗读复用**。

    P2-11 根因：原 `_speak_sapi` 每次朗读都 `subprocess.Popen(["powershell"...])`
    冷启动一个新进程（~300–600ms）+ 临时 .txt 传中文。这里改成常驻一个
    powershell 进程，逐句通过 stdin/stdout 行协议喂文本、等回执。

    行协议（stdin→宿主，UTF-8 / ASCII 安全）：
        <token>|<rate>|<vol>|<base64(text)>
    宿主每读完一句回写一行：
        DONE:<token>
    收到 `QUIT` 行退出。文本经 Base64，彻底规避管道中文编码坑。

    取消 / 超时：直接 `terminate` 宿主（SAPI 在进程内，音频随之停止），
    下次 `_ensure` 自动重建。任何异常 / 宿主死亡 / 握手超时 → 调用方
    (`_speak_sapi`) 回落到一次性 subprocess（旧行为），**绝不静默失效**。

    测试性：行协议与启动分离为 `_build_script()` / `_launch()`，单测可
    monkeypatch `_launch` 用 Python 假宿主覆盖握手逻辑（无需 Windows）。
    """

    # 真·卡死看门狗：正常朗读永不会到，仅防宿主假死无限阻塞（>> 最长朗读）
    _HANG_GUARD = 600.0

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @staticmethod
    def _build_script() -> str:
        """生成常驻宿主的 PowerShell 脚本（Windows 专用）。

        订阅 SpeakProgress 事件，逐词输出 PROG:offset:length 行，
        Python 端 reader 线程解析后回调 on_progress 实现蒙版跟进。
        """
        return (
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.add_SpeakProgress({ "
            "  param($sender, $e); "
            "  [Console]::Out.Write("
            "    'PROG:' + $e.CharacterPosition + ':' + "
            "    $e.CharacterCount + [char]10); "
            "  [Console]::Out.Flush() "
            "}); "
            "while ($true) { "
            "  $line = [Console]::In.ReadLine(); "
            "  if ($line -eq $null -or $line -eq 'QUIT') { break }; "
            "  $p = $line.Split('|'); "
            "  if ($p.Length -lt 4) { continue }; "
            "  $tok = $p[0]; "
            "  try { $s.Rate = [int]$p[1] } catch { }; "
            "  try { $s.Volume = [int]$p[2] } catch { }; "
            "  $txt = [Text.Encoding]::UTF8.GetString("
            "    [Convert]::FromBase64String($p[3])); "
            "  try { $s.Speak($txt) } catch { }; "
            '  [Console]::Out.Write("DONE:$tok`n"); '
            "  [Console]::Out.Flush() "
            "}"
        )

    def _launch(self) -> subprocess.Popen:
        """启动常驻宿主进程（可被单测 monkeypatch）。"""
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        return subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                self._build_script(),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    def _ensure(self) -> bool:
        """确保宿主存活（惰性启动 / 死亡重建）。返回是否可用。"""
        if self._proc is not None and self._proc.poll() is None:
            return True
        self._proc = None
        try:
            self._proc = self._launch()
            return True
        except Exception:
            self._proc = None
            return False

    def _terminate(self) -> None:
        """终止常驻宿主（取消 / 超时 / 重建前调用）。"""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def speak(
        self,
        text: str,
        cancel: threading.Event,
        rate: int = 0,
        vol: int = 0,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """复用常驻进程朗读一段文本。

        on_progress(offset, length) 在 SpeakProgress 事件触发时回调，
        用于 UI 蒙版跟进。返回：True=处理完毕；False=失败（调用方回落）。
        """
        token = uuid.uuid4().hex
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        line = f"{token}|{int(rate)}|{int(vol)}|{payload}\n"

        with self._lock:
            if not self._ensure():
                return False
            proc = self._proc

            ack = threading.Event()
            dead = threading.Event()

            def _reader() -> None:
                try:
                    for raw in proc.stdout:
                        raw = (raw or "").strip()
                        if raw.startswith("PROG:"):
                            if on_progress and not cancel.is_set():
                                parts = raw[5:].split(":")
                                if len(parts) >= 2:
                                    try:
                                        on_progress(int(parts[0]), int(parts[1]))
                                    except Exception:
                                        pass
                            continue
                        if raw.startswith("DONE:"):
                            if raw[5:] == token:
                                ack.set()
                                return
                except Exception:
                    pass
                dead.set()

            reader = threading.Thread(target=_reader, daemon=True)
            reader.start()

            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except Exception:
                return False

            step = 0.05
            waited = 0.0
            while not ack.is_set() and not dead.is_set():
                if cancel.is_set():
                    self._terminate()  # 取消：杀宿主，音频即停
                    return True
                time.sleep(step)
                waited += step
                if waited >= self._HANG_GUARD:
                    self._terminate()
                    return False
            return ack.is_set()

    def stop(self) -> None:
        self._terminate()


class TtsService:
    """朗读服务。对外只有 available / warmup / speak / stop / list_voices。"""

    name = "tts"

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg
        self._player = _MciPlayer()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._edge_ok: Optional[bool] = None  # None=未探测 / True / False
        self.last_engine = ""  # 上次真正出声的引擎
        # P2-11：常驻 SAPI 宿主（PowerShell+System.Speech），懒启动、跨朗读复用
        self._sapi_host: Optional[_SapiHost] = None
        self._sapi_lock = threading.Lock()
        atexit.register(self._stop_host)

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """Windows 上永真：sapi 是系统组件，兜底永远在。"""
        return os.name == "nt" or self._has_edge()

    def _has_edge(self) -> bool:
        try:
            import edge_tts  # noqa: F401

            return True
        except Exception:
            return False

    def engine_display(self) -> str:
        if not self._has_edge():
            return "系统语音（离线）"
        mode = getattr(self.cfg, "engine", "auto") if self.cfg else "auto"
        return {"edge": "Edge 在线语音", "sapi": "系统语音（离线）"}.get(
            mode, "自动（在线优先）"
        )

    def list_voices(self) -> List[tuple]:
        return list(PRESET_VOICES)

    # ------------------------------------------------------------------
    def warmup(self) -> None:
        """探测 edge 可用性，并在后台真合成一次「你好。」预热链路。
        同时预热 SAPI 常驻宿主，确保首次朗读无需冷启动。
        """
        self._edge_ok = self._has_edge()
        if self._edge_ok and not getattr(self, "_warmed", False):
            self._warmed = True
            threading.Thread(
                target=self._warmup_synth, daemon=True, name="winocr-tts-warmup"
            ).start()
        # 预热 SAPI 常驻宿主，免首次朗读冷启动
        try:
            self._get_sapi_host()
        except Exception:
            pass

    def _warmup_synth(self) -> None:
        try:
            # 用永不被取消的 Event，合成一段极短文本即可（只为预热链路）
            self._synth_chunk("你好。", threading.Event())
        except Exception:
            pass

    def stop(self) -> None:
        """立刻停：先置取消位，再掐播放器。常驻宿主保留复用，atexit 负责退出清理。"""
        self._cancel.set()
        self._player.stop()

    def _stop_host(self) -> None:
        """终止常驻 SAPI 宿主（stop / atexit 调用）。"""
        with self._sapi_lock:
            host = self._sapi_host
            self._sapi_host = None
        if host is not None:
            try:
                host.stop()
            except Exception:
                pass

    def speaking(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    # ------------------------------------------------------------------
    def speak(
        self,
        text: str,
        on_status: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """异步朗读。on_status 回调状态文本，on_progress(offset, length)
        回调当前朗读位置用于 UI 蒙版跟进。两者均在后台线程触发。"""
        text = _clean(text)
        if not text:
            if on_status:
                on_status("没有可朗读的文本")
            return

        self.stop()  # 新任务顶掉旧任务
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=0.2)  # 只等旧线程快速退出，不阻塞新朗读启动
        self._cancel = threading.Event()

        self._thread = threading.Thread(
            target=self._work,
            args=(text, on_status, self._cancel, on_progress),
            daemon=True,
            name="winocr-tts",
        )
        self._thread.start()

    # ------------------------------------------------------------------
    def _work(
        self, text: str, on_status, cancel: threading.Event, on_progress=None
    ) -> None:
        mode = (getattr(self.cfg, "engine", "auto") if self.cfg else "auto") or "auto"

        def say(msg: str) -> None:
            if on_status and not cancel.is_set():
                try:
                    on_status(msg)
                except Exception:
                    pass

        def prog(off: int, ln: int) -> None:
            if on_progress and not cancel.is_set():
                try:
                    on_progress(off, ln)
                except Exception:
                    pass

        if mode in ("auto", "edge") and self._has_edge():
            say("正在合成语音…")
            try:
                if self._speak_edge(text, cancel, on_progress=prog):
                    self.last_engine = "edge"
                    say("" if cancel.is_set() else "朗读完成")
                    return
            except Exception:
                pass
            if cancel.is_set():
                return
            if mode == "edge":
                say("Edge 在线语音不可用（检查网络）")
                return
            say("在线语音不可用，改用系统语音…")

        if cancel.is_set():
            return
        try:
            if self._speak_sapi(text, cancel, on_progress=prog):
                self.last_engine = "sapi"
                say("" if cancel.is_set() else "朗读完成（系统语音）")
                return
        except Exception:
            pass
        say("朗读失败：系统语音不可用")

    # ---------------- edge：在线 Neural ----------------
    def _speak_edge(
        self,
        text: str,
        cancel: threading.Event,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """在线 Neural 朗读。

        B 改进（流式 + 提前播放）：旧实现把整段文本合成成一个 mp3
        再播放——文本越长，要等越久才出第一声。现在按句切分，
        起一个后台合成线程逐句产出 mp3 推入队列，主播放线程边播边等
        下一句，于是**第一句合成完就开始响**，后续句子在后台补齐。
        短文本（单句）走单段老路径，行为不变。

        on_progress 在每个 chunk 开始播放前回调，传递该 chunk 在原文中
        的字符偏移和长度，用于 UI 蒙版跟进。
        """
        chunks = self._split_sentences(text)
        if len(chunks) <= 1:
            return self._speak_edge_single(text, cancel, on_progress=on_progress)

        # 计算每个 chunk 在原文中的字符偏移
        offsets: List[int] = []
        pos = 0
        for ch in chunks:
            idx = text.find(ch, pos)
            if idx < 0:
                idx = pos
            offsets.append(idx)
            pos = idx + len(ch)

        q: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=2)

        def produce() -> None:
            for i, ch in enumerate(chunks):
                if cancel.is_set():
                    q.put(None)
                    return
                try:
                    path = self._synth_chunk(ch, cancel)
                except Exception:
                    if not cancel.is_set():
                        q.put(None)  # 合成失败：停后续，已播部分保留
                    return
                if cancel.is_set():
                    self._rm(path)
                    q.put(None)
                    return
                q.put((path, offsets[i], len(ch)))
            q.put(None)

        prod = threading.Thread(target=produce, daemon=True, name="winocr-tts-synth")
        prod.start()

        played_any = False
        while True:
            item = q.get()
            if item is None:
                break
            if cancel.is_set():
                if isinstance(item, tuple):
                    self._rm(item[0])
                break
            path, off, ln = item
            if on_progress and not cancel.is_set():
                try:
                    on_progress(off, ln)
                except Exception:
                    pass
            played_any = self._player.play(path, cancel) or played_any
            self._rm(path)
            if cancel.is_set():
                break
        prod.join(timeout=2)
        while not q.empty():  # 清掉残留未播文件
            try:
                leftover = q.get_nowait()
                if isinstance(leftover, tuple):
                    self._rm(leftover[0])
            except Exception:
                pass
        return played_any or cancel.is_set()

    def _speak_edge_single(
        self,
        text: str,
        cancel: threading.Event,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """单段（短文本）合成+播放，等价于旧实现。"""
        import asyncio

        import edge_tts

        voice = (
            getattr(self.cfg, "voice", "") if self.cfg else ""
        ) or "zh-CN-XiaoxiaoNeural"
        rate = int(getattr(self.cfg, "rate", 0) or 0) if self.cfg else 0
        vol = int(getattr(self.cfg, "volume", 0) or 0) if self.cfg else 0

        path = os.path.join(
            tempfile.gettempdir(), f"winocr_tts_{uuid.uuid4().hex[:8]}.mp3"
        )

        async def _synth() -> None:
            comm = edge_tts.Communicate(
                text, voice, rate=f"{rate:+d}%", volume=f"{vol:+d}%"
            )
            await comm.save(path)

        try:
            asyncio.run(asyncio.wait_for(_synth(), timeout=25))
        except Exception:
            self._rm(path)
            raise

        if cancel.is_set():
            self._rm(path)
            return True  # 已被用户取消，算「处理完了」
        if not os.path.exists(path) or os.path.getsize(path) < 256:
            self._rm(path)
            return False
        if on_progress and not cancel.is_set():
            try:
                on_progress(0, len(text))
            except Exception:
                pass
        try:
            return self._player.play(path, cancel) or cancel.is_set()
        finally:
            self._rm(path)

    def _synth_chunk(self, text: str, cancel: threading.Event) -> str:
        """合成单句为临时 mp3 并返回路径（调用方负责删除）。"""
        import asyncio

        import edge_tts

        voice = (
            getattr(self.cfg, "voice", "") if self.cfg else ""
        ) or "zh-CN-XiaoxiaoNeural"
        rate = int(getattr(self.cfg, "rate", 0) or 0) if self.cfg else 0
        vol = int(getattr(self.cfg, "volume", 0) or 0) if self.cfg else 0

        path = os.path.join(
            tempfile.gettempdir(), f"winocr_tts_{uuid.uuid4().hex[:8]}.mp3"
        )

        async def _synth() -> None:
            comm = edge_tts.Communicate(
                text, voice, rate=f"{rate:+d}%", volume=f"{vol:+d}%"
            )
            await comm.save(path)

        asyncio.run(asyncio.wait_for(_synth(), timeout=25))
        if not os.path.exists(path) or os.path.getsize(path) < 256:
            self._rm(path)
            raise RuntimeError("edge synth produced empty audio")
        return path

    @staticmethod
    def _split_sentences(text: str, maxlen: int = 240) -> List[str]:
        """按句切分（保留标点），超长句再硬切，避免单段过大拖慢首声。"""
        raw = re.split(r"(?<=[。！？!?；;\n])", text)
        out: List[str] = []
        buf = ""
        for p in raw:
            if not p.strip():
                continue
            if len(buf) + len(p) <= maxlen:
                buf += p
            else:
                if buf:
                    out.append(buf)
                while len(p) > maxlen:
                    out.append(p[:maxlen])
                    p = p[maxlen:]
                buf = p
        if buf:
            out.append(buf)
        return out or [text]

    # ---------------- sapi：系统离线 ----------------
    def _get_sapi_host(self) -> Optional[_SapiHost]:
        """返回（惰性创建）常驻 SAPI 宿主单例。"""
        with self._sapi_lock:
            if self._sapi_host is None:
                self._sapi_host = _SapiHost()
            return self._sapi_host

    def _speak_sapi(
        self,
        text: str,
        cancel: threading.Event,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """走 PowerShell 的 System.Speech（离线兜底），**复用常驻进程**。

        P2-11：优先用 `_SapiHost` 常驻进程（免每次冷启动 + 免临时文件）；
        任何失败（启动/握手/宿主死亡）都回落 `_speak_sapi_oneshot`（旧行为），
        保证离线场景「点了就有声」，永不静默失效。

        on_progress 通过 SAPI SpeakProgress 事件回调逐词进度。
        """
        if os.name != "nt":
            return False
        rate = int(getattr(self.cfg, "rate", 0) or 0) if self.cfg else 0
        vol = int(getattr(self.cfg, "volume", 0) or 0) if self.cfg else 0
        try:
            host = self._get_sapi_host()
            if host is not None and host.speak(
                text, cancel, rate, vol, on_progress=on_progress
            ):
                return True
        except Exception:
            pass
        # 常驻路径失败 → 一次性 subprocess 兜底（无进度回调）
        return self._speak_sapi_oneshot(text, cancel, rate, vol)

    def _speak_sapi_oneshot(
        self, text: str, cancel: threading.Event, rate: int = 0, vol: int = 0
    ) -> bool:
        """旧实现（兜底）：每次朗读冷启动一个 powershell 进程 + 临时 .txt。

        仅在常驻宿主不可用（启动失败 / 握手异常 / 宿主死亡）时调用。
        """
        if os.name != "nt":
            return False
        # SAPI Rate 取 -10 ~ 10，把百分比压进这个区间
        sapi_rate = max(-10, min(10, round(rate / 10)))
        sapi_vol = max(0, min(100, 100 + vol))

        txt_path = os.path.join(
            tempfile.gettempdir(), f"winocr_tts_{uuid.uuid4().hex[:8]}.txt"
        )
        try:
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Rate = {sapi_rate}; $s.Volume = {sapi_vol}; "
                f"$s.Speak([IO.File]::ReadAllText('{txt_path}', "
                "[Text.Encoding]::UTF8))"
            )
            flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            proc = subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            while proc.poll() is None:
                if cancel.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    return True
                time.sleep(0.05)
            return proc.returncode == 0
        finally:
            self._rm(txt_path)

    @staticmethod
    def _rm(path: str) -> None:
        try:
            os.remove(path)
        except Exception:
            pass
