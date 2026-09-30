# -*- coding: utf-8 -*-
"""语音录入：把「说出的英雄名」变成 BP 录入事件。

合规与技术边界（重要）
----------------------
* **只调用 Windows 自带的语音识别**（``System.Speech``，由 ``tools/voice_listen.ps1``
  加载），不联网、不上传音频、不需要任何 API Key——和项目其余部分一样，
  数据全部留在本机。
* **不注入游戏、不读游戏内存、不 OCR 游戏画面**。麦克风只在你按下语音按钮
  （或 ``Ctrl+Alt+V``）后才打开，识别结果与你手打的英雄名走**完全相同**的
  解析管线（:meth:`d2a.data_loader.HeroBook.resolve`），所以「读屏/自动感知 BP」
  那些合规风险在这里完全不存在。
* 识别范围默认被约束成**英雄名语法**（全名 + ``data/aliases.json`` 的 500 多条别名），
  所以准确率远高于自由听写；需要自由说话时可切到 dictation 模式。

工作方式
--------
``tools/voice_listen.ps1`` 是一个**独立子进程**，逐行输出协议：

    READY <culture> <mode>      引擎就绪
    HEARD <文本>                识别到一句话
    REJECT <文本> <置信度>      置信度过低被丢弃
    FLAG <名字> <置信度>        高置信度命中
    WARN <消息>                 非致命问题
    ERROR <消息>                致命问题
    STOPPED <原因>              结束

本模块负责起进程、读协议、维护状态，并把结果交给 UI。
被刻意设计成「可注入命令」，这样没有麦克风的机器上也能用假进程做单元测试。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRIPT = ROOT / "tools" / "voice_listen.ps1"


def _candidates() -> List[str]:
    """按优先级找 PowerShell 可执行文件。"""
    out: List[str] = []
    env = os.environ.get("D2A_POWERSHELL")
    if env:
        out.append(env)
    # 优先 Windows PowerShell 5.1（System.Speech 的兼容性最好）
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    out.append(str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"))
    for name in ("powershell.exe", "pwsh.exe"):
        found = shutil.which(name)
        if found:
            out.append(found)
    return out


def find_powershell() -> Optional[str]:
    for c in _candidates():
        if c and Path(c).exists():
            return c
    return None


def voice_supported() -> tuple:
    """返回 ``(是否可用, 说明)``。用于 UI 决定是否显示语音按钮。"""
    if not sys.platform.startswith("win"):
        return False, "语音输入目前只支持 Windows（依赖系统自带的 System.Speech）"
    if not DEFAULT_SCRIPT.exists():
        return False, f"缺少 {DEFAULT_SCRIPT.name}"
    ps = find_powershell()
    if not ps:
        return False, "找不到 powershell.exe"
    return True, f"就绪（{Path(ps).name}）"


@dataclass
class VoiceEvent:
    kind: str          # ready / heard / reject / flag / warn / error / stopped
    text: str = ""
    confidence: float = 0.0
    detail: str = ""


@dataclass
class VoiceConfig:
    seconds: int = 0                 # 0 = 一直听，直到 stop()
    culture: str = ""                # 空 = 自动（优先 zh-*）
    mode: str = "hero"               # hero | dictation
    min_confidence: float = 0.55
    repeat_guard: float = 1.5


class VoiceListener:
    """管理 ``voice_listen.ps1`` 子进程，把输出解析成 :class:`VoiceEvent`。

    ``command`` 可注入，方便测试（见 ``tests/test_voice.py``）。
    """

    def __init__(
        self,
        config: Optional[VoiceConfig] = None,
        on_event: Optional[Callable[[VoiceEvent], None]] = None,
        command: Optional[Sequence[str]] = None,
        script: Optional[Path] = None,
    ) -> None:
        self.cfg = config or VoiceConfig()
        self.on_event = on_event
        self.script = Path(script) if script else DEFAULT_SCRIPT
        self._command = list(command) if command else None
        self.events: List[VoiceEvent] = []
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False

    # ------------------------------------------------------------------ 构造命令
    def build_command(self) -> List[str]:
        if self._command is not None:
            return list(self._command)
        ps = find_powershell()
        if not ps:
            raise RuntimeError("找不到 powershell.exe，无法启动语音识别")
        cmd = [
            ps,
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(self.script),
            "-Mode", str(self.cfg.mode),
            "-MinConfidence", f"{self.cfg.min_confidence:.2f}",
            "-RepeatGuardSeconds", f"{self.cfg.repeat_guard:.1f}",
        ]
        if self.cfg.seconds:
            cmd += ["-Seconds", str(int(self.cfg.seconds))]
        if self.cfg.culture:
            cmd += ["-Culture", str(self.cfg.culture)]
        return cmd

    # ------------------------------------------------------------------ 状态
    @property
    def running(self) -> bool:
        with self._lock:
            return self._running and self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------ 协议解析
    @staticmethod
    def parse_line(line: str) -> Optional[VoiceEvent]:
        line = (line or "").strip()
        if not line:
            return None
        tag, _, rest = line.partition(" ")
        tag = tag.strip().lower()
        rest = rest.strip()
        if tag == "ready":
            return VoiceEvent("ready", detail=rest)
        if tag == "heard":
            return VoiceEvent("heard", text=rest)
        if tag == "flag":
            # "英雄名 0.87"
            name, _, conf = rest.rpartition(" ")
            try:
                c = float(conf)
            except ValueError:
                name, c = rest, 0.0
            return VoiceEvent("flag", text=name.strip(), confidence=c)
        if tag == "reject":
            name, _, conf = rest.rpartition(" ")
            try:
                c = float(conf)
            except ValueError:
                name, c = rest, 0.0
            return VoiceEvent("reject", text=name.strip(), confidence=c)
        if tag == "warn":
            return VoiceEvent("warn", detail=rest)
        if tag == "error":
            return VoiceEvent("error", detail=rest)
        if tag == "stopped":
            return VoiceEvent("stopped", detail=rest)
        return None

    def _emit(self, ev: Optional[VoiceEvent]) -> None:
        if ev is None:
            return
        self.events.append(ev)
        if len(self.events) > 200:
            del self.events[:100]
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass  # UI 回调出错不能拖垮监听

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> bool:
        if self.running:
            return True
        try:
            cmd = self.build_command()
        except RuntimeError as e:
            self._emit(VoiceEvent("error", detail=str(e)))
            return False
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(ROOT),
                # 不让子进程弹窗
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            self._emit(VoiceEvent("error", detail=f"无法启动语音进程: {e}"))
            return False
        with self._lock:
            self._running = True
        self._thread = threading.Thread(target=self._read_loop, name="d2a-voice", daemon=True)
        self._thread.start()
        return True

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                self._emit(self.parse_line(line))
        except Exception as e:  # 管道被关等
            self._emit(VoiceEvent("warn", detail=f"读取识别输出中断: {e}"))
        finally:
            code = proc.wait()
            with self._lock:
                self._running = False
            self._emit(VoiceEvent("stopped", detail=f"exit={code}"))

    def stop(self, timeout: float = 3.0) -> None:
        proc = self._proc
        with self._lock:
            self._running = False
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=timeout)
                except Exception:
                    pass
        # 显式关闭管道，避免长时间运行/反复开关时泄漏句柄
        for stream in (proc.stdout, proc.stdin, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        self._proc = None

    def __enter__(self) -> "VoiceListener":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def parse_line(line: str) -> Optional[VoiceEvent]:
    """协议行解析的模块级入口（:meth:`VoiceListener.parse_line` 的便捷包装）。"""
    return VoiceListener.parse_line(line)


def probe(seconds: int = 6, culture: str = "", mode: str = "hero") -> int:
    """命令行自检：直接跑一次语音识别并把协议行打印出来。

    这是唯一能真正验证麦克风链路的入口：``python -m d2a --voice-probe``
    """
    ok, why = voice_supported()
    if not ok:
        print(f"语音不可用: {why}", file=sys.stderr)
        return 2

    def show(ev: VoiceEvent) -> None:
        if ev.kind == "heard":
            print(f"  [听到] {ev.text}")
        elif ev.kind == "flag":
            print(f"  [命中] {ev.text}  置信度 {ev.confidence:.2f}")
        elif ev.kind == "reject":
            print(f"  [丢弃] {ev.text}  置信度过低 {ev.confidence:.2f}")
        elif ev.kind == "ready":
            print(f"  [就绪] {ev.detail}")
        elif ev.kind in ("warn", "error"):
            print(f"  [{ev.kind.upper()}] {ev.detail}")
        elif ev.kind == "stopped":
            print(f"  [结束] {ev.detail}")

    print(f"语音自检：监听 {seconds} 秒，模式 {mode}。请对着麦克风念英雄名（如「斧王」「剑圣」）…")
    listener = VoiceListener(VoiceConfig(seconds=seconds, culture=culture, mode=mode), on_event=show)
    if not listener.start():
        print("启动失败", file=sys.stderr)
        return 1
    listener._thread.join(timeout=seconds + 8) if listener._thread else None
    listener.stop()
    heard = [e for e in listener.events if e.kind in ("heard", "flag")]
    print(f"\n共识别到 {len(heard)} 条语音。")
    if not heard:
        engine_failed = any(e.kind == "error" and "识别引擎" in e.detail for e in listener.events)
        if engine_failed:
            print("识别引擎没能创建成功——不是「没听清」，而是环境问题。请检查：")
            print("  1. 设置 → 时间和语言 → 语言 → 中文(简体) → 语言选项 → 语音包是否已安装")
            print("  2. 是否在受限环境（沙箱/容器）里运行，导致无法访问音频设备")
            print("     请在普通终端里再跑一次本命令")
        else:
            print("没有识别到内容（引擎正常，可能是没听到）。请检查：")
            print("  1. Windows 设置 → 隐私和安全性 → 麦克风 → 允许桌面应用访问（最常见）")
            print("  2. 设置 → 系统 → 声音 → 默认输入设备是否选对")
            print("  3. 说话时离麦克风近一点，念完整的英雄名（如「斧王」「剑圣」）")
        return 1
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="语音录入自检")
    ap.add_argument("--probe", action="store_true", help="跑一次识别自检")
    ap.add_argument("--seconds", type=int, default=6)
    ap.add_argument("--culture", default="")
    ap.add_argument("--mode", default="hero", choices=["hero", "dictation"])
    a = ap.parse_args()
    if a.probe:
        sys.exit(probe(a.seconds, a.culture, a.mode))
    ok, why = voice_supported()
    print(f"语音支持: {'是' if ok else '否'} — {why}")
