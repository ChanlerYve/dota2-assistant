# -*- coding: utf-8 -*-
"""Windows 悬浮窗：叠在游戏画面上方的小面板（tkinter 实现，零第三方依赖）。

合规边界（重要）
----------------
本悬浮窗**不读取游戏任何信息**：

* 不注入 DLL、不读写游戏内存、不抓包、不改游戏文件、不模拟按键；
* 不依赖游戏画面识别（不做 OCR / 图像识别），因此不存在被判定为
  「外部辅助读取对战信息」的技术动作；
* 所有输入都来自你自己在窗口里敲的英雄名（或你在别处看好的 BP），
  推荐所用的胜率/克制数据全部来自公开接口与内置静态数据。

因此它在技术行为上等同于「一个置顶的备忘录 + 计算器」。
在全屏独占（Exclusive Fullscreen）模式下，Windows 不允许任何置顶窗口覆盖，
请在游戏里把显示模式设为「无边框窗口（Borderless Windowed）」——
这也是职业选手与录制场景的常规设置。

键盘：窗口内输入英雄名 → 回车。拖动标题栏移动；``Ctrl+Alt+D`` 全局显示/隐藏；
``Ctrl+Alt+T`` 切换鼠标穿透（穿透后只能靠快捷键恢复）。
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from typing import Dict, List, Optional, Tuple

from .data_loader import HeroNotFound
from .engine import Assistant, Candidate
from .voice import VoiceConfig, VoiceEvent, VoiceListener, voice_supported

# --------------------------------------------------------------------------- 配色
BG = "#10141b"
BG2 = "#161d27"
BG3 = "#1d2734"
LINE = "#2a3646"
FG = "#e6edf6"
DIM = "#93a4ba"
ACCENT = "#e0473c"
TEAL = "#3fd0b4"
GOLD = "#e8bd4d"
WARN = "#e59b6b"

LANE_LABEL = {1: "1 优核", 2: "2 中单", 3: "3 劣单", 4: "4 游走", 5: "5 辅助"}
SIDE_LABEL = {"enemy": "敌", "ally": "我", "ban": "ban"}


# --------------------------------------------------------------------------- Win32 小工具
class Win32:
    """可选的 Windows 能力：DPI 感知、鼠标穿透、全局热键。

    全部包在 try/except 里——非 Windows 或调用失败时功能降级，不影响主流程。
    """

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    WM_HOTKEY = 0x0312

    def __init__(self) -> None:
        self.available = sys.platform.startswith("win")
        self._user32 = None
        if self.available:
            try:
                import ctypes

                self._ctypes = ctypes
                self._user32 = ctypes.windll.user32
            except Exception:
                self.available = False

    def set_dpi_aware(self) -> None:
        if not self.available:
            return
        try:
            self._ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                self._user32.SetProcessDPIAware()
            except Exception:
                pass

    def set_click_through(self, hwnd: int, enable: bool, tool_window: bool = True) -> bool:
        """让窗口忽略鼠标事件（点击直接穿透到游戏）。"""
        if not self.available:
            return False
        try:
            ex = self._user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
            ex |= self.WS_EX_LAYERED
            if tool_window:
                ex |= self.WS_EX_TOOLWINDOW  # 不在任务栏显示
            if enable:
                ex |= self.WS_EX_TRANSPARENT
            else:
                ex &= ~self.WS_EX_TRANSPARENT
            self._user32.SetWindowLongW(hwnd, self.GWL_EXSTYLE, ex)
            return True
        except Exception:
            return False

    def register_hotkey(self, hwnd: int, hotkey_id: int, mods: int, vk: int) -> bool:
        if not self.available:
            return False
        try:
            return bool(self._user32.RegisterHotKey(hwnd, hotkey_id, mods, vk))
        except Exception:
            return False

    def unregister_hotkey(self, hwnd: int, hotkey_id: int) -> None:
        if not self.available:
            return
        try:
            self._user32.UnregisterHotKey(hwnd, hotkey_id)
        except Exception:
            pass

    def foreground_hwnd(self) -> int:
        if not self.available:
            return 0
        try:
            return int(self._user32.GetForegroundWindow())
        except Exception:
            return 0


# --------------------------------------------------------------------------- 悬浮窗
@dataclass
class OverlayOptions:
    alpha: float = 0.92
    width: int = 460
    height: int = 620
    x: int = 24
    y: int = 96
    topmost: bool = True
    top_n: int = 5
    font_size: int = 10
    click_through: bool = False

    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> "OverlayOptions":
        o = cls()
        for k, v in (cfg or {}).items():
            if hasattr(o, k):
                setattr(o, k, v)
        return o


@dataclass
class VoiceOptions:
    """语音录入的界面侧选项（d2a.voice.VoiceConfig 的友好包装）。"""

    enabled: bool = False
    culture: str = ""
    mode: str = "hero"
    min_confidence: float = 0.55
    seconds: int = 0
    auto_add: bool = True
    hotkey: str = "ctrl+alt+v"

    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> "VoiceOptions":
        o = cls()
        for k, v in (cfg or {}).items():
            if hasattr(o, k):
                setattr(o, k, v)
        return o


class Overlay:
    def __init__(
        self,
        assistant: Assistant,
        options: Optional[OverlayOptions] = None,
        voice_options: Optional[VoiceOptions] = None,
    ) -> None:
        self.a = assistant
        self.o = options or OverlayOptions()
        self.vo = voice_options or VoiceOptions()
        self.win = Win32()
        self.win.set_dpi_aware()
        # 语音：识别结果先进队列，由 tk 主线程消费，避免跨线程碰 UI
        self._voice_q: "queue.Queue[VoiceEvent]" = queue.Queue()
        self.listener: Optional[VoiceListener] = None
        self._voice_ok, self._voice_why = voice_supported()

        self.root = tk.Tk()
        self.root.title("Dota2 选人助手")
        self.root.configure(bg=BG)
        self.root.geometry(f"{self.o.width}x{self.o.height}+{self.o.x}+{self.o.y}")
        self.root.attributes("-topmost", bool(self.o.topmost))
        try:
            self.root.attributes("-alpha", float(self.o.alpha))
        except Exception:
            pass
        self.root.overrideredirect(True)  # 去掉标题栏，做成贴片

        self.candidates: List[Candidate] = []
        self.deep = False
        self._drag = (0, 0)
        self._hwnd = 0
        self._t0 = time.time()

        self._build_fonts()
        self._build_ui()
        self.root.update_idletasks()
        self._init_win32_features()
        self.refresh()
        self._tick()

    # ------------------------------------------------------------------ 构造
    def _build_fonts(self) -> None:
        size = int(self.o.font_size)
        family = "Microsoft YaHei UI"
        self.f_title = tkfont.Font(family=family, size=size + 2, weight="bold")
        self.f_body = tkfont.Font(family=family, size=size)
        self.f_small = tkfont.Font(family=family, size=max(7, size - 1))
        self.f_score = tkfont.Font(family=family, size=size + 3, weight="bold")

    def _build_ui(self) -> None:
        pad = 8

        # ---- 标题栏（可拖动）
        bar = tk.Frame(self.root, bg=BG2, height=24)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        title = tk.Label(bar, text="选人助手", bg=BG2, fg=FG, font=self.f_small)
        title.pack(side="left", padx=pad)
        self.lbl_data = tk.Label(bar, text="", bg=BG2, fg=DIM, font=self.f_small)
        self.lbl_data.pack(side="left")
        for text, cmd, color in (
            ("✕", self.close, ACCENT),
            ("穿透", self.toggle_click_through, DIM),
            ("详情", self.toggle_deep, DIM),
        ):
            b = tk.Label(bar, text=text, bg=BG2, fg=color, font=self.f_small, padx=5, cursor="hand2")
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, c=cmd: c())
        # 语音按钮：只有环境支持时才显示（不支持就直接不出现，避免误导）
        self.lbl_mic: Optional[tk.Label] = None
        if self._voice_ok:
            self.lbl_mic = tk.Label(
                bar, text="🎤", bg=BG2, fg=DIM, font=self.f_small, padx=6, cursor="hand2"
            )
            self.lbl_mic.pack(side="right")
            self.lbl_mic.bind("<Button-1>", lambda e: self.toggle_voice())
        for w in (bar, title, self.lbl_data):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        # ---- BP 摘要
        self.frm_draft = tk.Frame(self.root, bg=BG, padx=pad, pady=4)
        self.frm_draft.pack(fill="x")
        self.lbl_ally = tk.Label(self.frm_draft, text="", bg=BG, fg=TEAL, font=self.f_small, anchor="w", justify="left")
        self.lbl_enemy = tk.Label(self.frm_draft, text="", bg=BG, fg=ACCENT, font=self.f_small, anchor="w", justify="left")
        self.lbl_ally.pack(fill="x")
        self.lbl_enemy.pack(fill="x")
        self.lbl_need = tk.Label(self.frm_draft, text="", bg=BG, fg=GOLD, font=self.f_small, anchor="w")
        self.lbl_need.pack(fill="x")

        # ---- 位置选择
        frm_lane = tk.Frame(self.root, bg=BG, padx=pad)
        frm_lane.pack(fill="x", pady=(2, 4))
        tk.Label(frm_lane, text="位置", bg=BG, fg=DIM, font=self.f_small).pack(side="left")
        self.lane_btns: Dict[Optional[int], tk.Label] = {}
        for lane in (1, 2, 3, 4, 5, None):
            text = LANE_LABEL[lane].split()[0] if lane else "自动"
            lb = tk.Label(frm_lane, text=text, bg=BG3, fg=DIM, font=self.f_small, padx=6, pady=1, cursor="hand2")
            lb.pack(side="left", padx=2)
            lb.bind("<Button-1>", lambda e, l=lane: self.set_lane(l))
            self.lane_btns[lane] = lb

        # ---- 推荐列表
        self.frm_cards = tk.Frame(self.root, bg=BG, padx=pad)
        self.frm_cards.pack(fill="both", expand=True)

        # ---- 底部录入
        frm_in = tk.Frame(self.root, bg=BG2, padx=pad, pady=6)
        frm_in.pack(fill="x", side="bottom")
        self.var_input = tk.StringVar()
        self.entry = tk.Entry(
            frm_in, textvariable=self.var_input, bg=BG3, fg=FG, insertbackground=FG,
            relief="flat", font=self.f_body,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.entry.bind("<Return>", self.on_enter)
        self.lbl_side = tk.Label(frm_in, text="敌", bg=ACCENT, fg="#fff", font=self.f_small, padx=6, cursor="hand2")
        self.lbl_side.pack(side="left", padx=4)
        self.lbl_side.bind("<Button-1>", self.cycle_side)
        self.side = "enemy"

        self.lbl_hint = tk.Label(self.root, text="", bg=BG, fg=DIM, font=self.f_small, anchor="w", padx=pad)
        self.lbl_hint.pack(fill="x", side="bottom")

    def _start_drag(self, event) -> None:
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _on_drag(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    # ------------------------------------------------------------------ Win32
    def _init_win32_features(self) -> None:
        try:
            self._hwnd = int(self.root.winfo_id())
            if self.win.available and self._hwnd:
                # 拿顶层句柄（winfo_id 给的是子窗口）
                import ctypes

                self._hwnd = int(ctypes.windll.user32.GetAncestor(self._hwnd, 2))
                self.win.set_click_through(self._hwnd, self.o.click_through)
                self.win.register_hotkey(self._hwnd, 1, Win32.MOD_CONTROL | Win32.MOD_ALT, ord("D"))
                self.win.register_hotkey(self._hwnd, 2, Win32.MOD_CONTROL | Win32.MOD_ALT, ord("T"))
                self.win.register_hotkey(self._hwnd, 3, Win32.MOD_CONTROL | Win32.MOD_ALT, ord("V"))
        except Exception:
            self._hwnd = 0

    def toggle_click_through(self) -> None:
        self.o.click_through = not self.o.click_through
        ok = self.win.set_click_through(self._hwnd, self.o.click_through) if self._hwnd else False
        if self.o.click_through and not ok:
            self.o.click_through = False
            self.hint("当前平台不支持鼠标穿透")
        else:
            self.hint("鼠标穿透：" + ("开（Ctrl+Alt+T 恢复）" if self.o.click_through else "关"))

    def poll_hotkeys(self) -> None:
        """轮询 Win32 热键消息（tkinter 无法直接拿到 WM_HOTKEY）。"""
        if not (self.win.available and self._hwnd):
            return
        try:
            import ctypes
            from ctypes import wintypes

            msg = wintypes.MSG()
            while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), self._hwnd, 0x0312, 0x0312, 1):
                if msg.message == Win32.WM_HOTKEY:
                    if msg.wParam == 1:
                        self.toggle_visible()
                    elif msg.wParam == 2:
                        self.toggle_click_through()
                    elif msg.wParam == 3:
                        self.toggle_voice()
        except Exception:
            pass
        finally:
            self.root.after(150, self.poll_hotkeys)

    def toggle_visible(self) -> None:
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.attributes("-topmost", True)
        else:
            self.root.withdraw()

    # ------------------------------------------------------------------ 交互
    def cycle_side(self, _evt=None) -> None:
        order = ["enemy", "ally", "ban"]
        self.side = order[(order.index(self.side) + 1) % len(order)]
        self.lbl_side.configure(
            text=SIDE_LABEL[self.side],
            bg={"enemy": ACCENT, "ally": "#2c6b52", "ban": "#4a4a5a"}[self.side],
        )

    def set_lane(self, lane: Optional[int]) -> None:
        self.a.draft.my_lane = lane
        self.refresh()

    def on_enter(self, _evt=None) -> None:
        raw = self.var_input.get().strip()
        if not raw:
            return
        if raw in ("reset", "清空"):
            self.a.draft.__init__()
            self.var_input.set("")
            self.refresh()
            return
        if raw.startswith("rm ") or raw.startswith("删 "):
            name = raw[3:].strip()
            try:
                self.a.draft.remove(self.a.book.resolve(name).name)
            except HeroNotFound:
                pass
            self.var_input.set("")
            self.refresh()
            return
        try:
            hero = self.a.book.resolve(raw)
        except HeroNotFound:
            self.hint(f"认不出「{raw}」")
            return
        try:
            if self.side == "ban":
                if hero.name not in self.a.draft.bans:
                    self.a.draft.bans.append(hero.name)
            else:
                self.a.draft.add(hero.name, self.side)
        except ValueError as e:
            self.hint(str(e))
            return
        self.var_input.set("")
        self.refresh()

    def toggle_deep(self) -> None:
        self.deep = not self.deep
        self.refresh()

    def hint(self, text: str, ms: int = 2500) -> None:
        self.lbl_hint.configure(text=text)

        def clear() -> None:
            try:
                self.lbl_hint.configure(text="")
            except Exception:
                pass

        threading.Timer(ms / 1000.0, clear).start()

    # ------------------------------------------------------------------ 语音
    def _voice_status(self, text: str, color: str = DIM) -> None:
        if self.lbl_mic is not None:
            try:
                self.lbl_mic.configure(text=text, fg=color)
            except Exception:
                pass

    def toggle_voice(self) -> None:
        """开/关语音识别（🎤 按钮或 Ctrl+Alt+V）。"""
        if not self._voice_ok:
            self.hint(f"语音不可用：{self._voice_why}")
            return
        if self.listener is not None and self.listener.running:
            self.listener.stop()
            self.listener = None
            self._voice_status("🎤", DIM)
            self.hint("语音录入：已停止")
            return
        cfg = VoiceConfig(
            seconds=int(self.vo.seconds or 0),
            culture=str(self.vo.culture or ""),
            mode=str(self.vo.mode or "hero"),
            min_confidence=float(self.vo.min_confidence),
        )
        self.listener = VoiceListener(cfg, on_event=lambda ev: self._voice_q.put(ev))
        if self.listener.start():
            self._voice_status("🎤…", TEAL)
            self.hint("语音录入：请说英雄名（如「斧王」「剑圣」）", 3500)
        else:
            self.listener = None
            self._voice_status("🎤", ACCENT)
            self.hint("语音启动失败，按详情/查看命令行输出", 4000)

    def _pump_voice(self) -> None:
        """在 tk 主线程消费语音事件队列。"""
        drained = 0
        while drained < 20:
            try:
                ev = self._voice_q.get_nowait()
            except queue.Empty:
                break
            drained += 1
            self._handle_voice_event(ev)
        self.root.after(120, self._pump_voice)

    def _handle_voice_event(self, ev: VoiceEvent) -> None:
        if ev.kind == "ready":
            self._voice_status("🎤…", TEAL)
            return
        if ev.kind in ("warn",):
            return  # 无可用英雄名的告警等，不打扰用户
        if ev.kind == "error":
            self._voice_status("🎤", ACCENT)
            self.hint(f"语音错误：{ev.detail}", 6000)
            return
        if ev.kind == "stopped":
            self._voice_status("🎤", DIM)
            if self.listener is not None and not self.listener.running:
                self.listener = None
            return
        if ev.kind == "reject":
            self.hint(f"语音没听清（{ev.confidence:.2f}）：「{ev.text}」，请再说一次", 3000)
            return
        if ev.kind not in ("heard", "flag"):
            return

        raw = ev.text.strip()
        if not raw:
            return
        # 语音里的标点/空格先清掉再交给别名解析（复用与手打完全相同的管线）
        cleaned = raw.strip("。，、,.!！?？ ")
        try:
            hero = self.a.book.resolve(cleaned)
        except HeroNotFound:
            cands = self.a.book.candidates(cleaned, limit=3)
            if len(cands) == 1:
                hero = cands[0]
            else:
                tip = "、".join(c.name for c in cands) if cands else "无匹配"
                self.hint(f"语音「{raw}」认不出，接近的有：{tip}", 4000)
                return

        if not self.vo.auto_add:
            self.hint(f"语音听到：{hero.name}（未录入）", 3000)
            return
        try:
            if self.side == "ban":
                if hero.name not in self.a.draft.bans:
                    self.a.draft.bans.append(hero.name)
            else:
                self.a.draft.add(hero.name, self.side)
        except ValueError as e:
            self.hint(f"语音：{e}", 3000)
            return
        where = SIDE_LABEL.get(self.side, self.side)
        conf = f" {ev.confidence:.2f}" if ev.kind == "flag" else ""
        self.hint(f"语音录入[{where}]：{hero.name}{conf}（识别原文「{raw}」）", 3000)
        self.refresh()

    # ------------------------------------------------------------------ 渲染
    def refresh(self) -> None:
        d = self.a.draft
        ally = "、".join(
            f"{h}{LANE_LABEL[d.ally_lanes[h]].split()[0]}" if h in d.ally_lanes else h for h in d.ally_heroes()
        ) or "—"
        enemy = "、".join(d.enemy_heroes()) or "—"
        self.lbl_ally.configure(text="我 " + ally)
        self.lbl_enemy.configure(text="敌 " + enemy)
        needs = self.a.engine.team_needs(d)
        labels = {"init": "先手", "control": "硬控", "frontline": "前排", "save": "救人", "push": "清线"}
        gaps = [labels[k] for k, v in needs.items() if v > 0.25]
        self.lbl_need.configure(text="缺口 " + ("、".join(gaps) if gaps else "无"))

        for lane, lb in self.lane_btns.items():
            active = (lane == d.my_lane) if lane is not None else (d.my_lane is None)
            lb.configure(bg=TEAL if active else BG3, fg="#08110f" if active else DIM)

        # 推荐
        for w in self.frm_cards.winfo_children():
            w.destroy()
        top_n = int(self.o.top_n)
        self.candidates = self.a.engine.recommend(d, top_n=top_n, pool_only=True)
        if not self.candidates:
            tk.Label(
                self.frm_cards, text="英雄池为空：命令行执行 pool add，\n或在 config.json 里填 pool 后重启",
                bg=BG, fg=DIM, font=self.f_small, justify="left",
            ).pack(anchor="w")
        for i, c in enumerate(self.candidates):
            self._card(c, i)

        src = "在线" if self.a.book.live_fetched_at or self.a.book.meta_fetched_at else "离线"
        self.lbl_data.configure(text=f" {src} · {time.strftime('%H:%M')}")

    def _card(self, c: Candidate, idx: int) -> None:
        frm = tk.Frame(self.frm_cards, bg=BG2 if idx else BG3, highlightbackground=GOLD if idx == 0 else LINE,
                       highlightthickness=1)
        frm.pack(fill="x", pady=2)
        top = tk.Frame(frm, bg=frm["bg"])
        top.pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(top, text=f"{idx + 1}. {c.hero.name}", bg=frm["bg"], fg=FG, font=self.f_title).pack(side="left")
        tk.Label(top, text="★" * c.stars, bg=frm["bg"], fg=GOLD, font=self.f_small).pack(side="left", padx=4)
        tk.Label(top, text=f"{c.score:.1f}", bg=frm["bg"], fg=GOLD, font=self.f_score).pack(side="right")
        if c.games:
            info = f"{c.position or '-'}号位 · {c.games}局 {c.winrate:.0%}"
        else:
            info = f"{c.position or '-'}号位 · 未玩过"
        tk.Label(frm, text=info, bg=frm["bg"], fg=DIM, font=self.f_small, anchor="w").pack(fill="x", padx=6)

        if self.deep:
            b = c.breakdown
            bars = f"熟练{b['proficiency']:.2f} 对位{b['matchup']:.2f} 阵容{b['team_need']:.2f} 版本{b['meta']:.2f} 配合{b['synergy']:.2f}"
            tk.Label(frm, text=bars, bg=frm["bg"], fg=TEAL, font=self.f_small, anchor="w").pack(fill="x", padx=6)
            for r in c.reasons[:4]:
                tk.Label(frm, text="· " + r, bg=frm["bg"], fg=DIM, font=self.f_small, anchor="w",
                         wraplength=self.o.width - 30, justify="left").pack(fill="x", padx=6)
            for r in c.risks[:3]:
                tk.Label(frm, text="! " + r, bg=frm["bg"], fg=WARN, font=self.f_small, anchor="w",
                         wraplength=self.o.width - 30, justify="left").pack(fill="x", padx=6)
        else:
            text = " · ".join(c.reasons[:2])
            tk.Label(frm, text=text, bg=frm["bg"], fg=DIM, font=self.f_small, anchor="w",
                     wraplength=self.o.width - 30, justify="left").pack(fill="x", padx=6)

    # ------------------------------------------------------------------ 循环
    def _tick(self) -> None:
        """每 0.5 秒检查 BP 是否变化，变了就重绘（保持悬浮窗与状态同步）。"""
        snap = (
            tuple(self.a.draft.ally_heroes()),
            tuple(self.a.draft.enemy_heroes()),
            tuple(self.a.draft.bans),
            self.a.draft.my_lane,
            len(self.a.engine.pool),
        )
        if snap != getattr(self, "_snap", None):
            self._snap = snap
            self.refresh()
        self.root.after(500, self._tick)

    def close(self) -> None:
        if self.win.available and self._hwnd:
            self.win.unregister_hotkey(self._hwnd, 1)
            self.win.unregister_hotkey(self._hwnd, 2)
            self.win.unregister_hotkey(self._hwnd, 3)
        if self.listener is not None:
            self.listener.stop()
            self.listener = None
        self.root.destroy()

    def run(self) -> None:
        self.root.after(200, self.poll_hotkeys)
        self.root.after(150, self._pump_voice)
        if self._voice_ok and self.vo.enabled:
            self.root.after(400, self.toggle_voice)
        self.entry.focus_force()
        try:
            self.root.mainloop()
        finally:
            if self.listener is not None:
                self.listener.stop()


def run_overlay(
    assistant: Assistant,
    options: Optional[OverlayOptions] = None,
    voice_options: Optional[VoiceOptions] = None,
) -> int:
    try:
        import tkinter  # noqa: F401
    except Exception as e:
        print(f"无法启动悬浮窗（缺少 tkinter）: {e}", file=sys.stderr)
        print("改用 Web 面板: python -m d2a --web", file=sys.stderr)
        return 2
    ov = Overlay(assistant, options, voice_options)
    ov.run()
    return 0
