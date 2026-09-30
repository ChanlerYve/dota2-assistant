# -*- coding: utf-8 -*-
"""本地配置：玩家英雄池、权重偏好、界面选项。

配置文件位置（按优先级）：
1. ``--config`` 指定的路径
2. 项目目录下 ``config.json``
3. ``%APPDATA%/dota2-assistant/config.json``

样例见仓库里的 ``config.example.json``。
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]


def default_config_path() -> pathlib.Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return pathlib.Path(appdata) / "dota2-assistant" / "config.json"
    return pathlib.Path.home() / ".dota2-assistant" / "config.json"


@dataclass
class Config:
    # 玩家英雄池（简写形式）：{英雄名: [局数, 胜场]} 或 {英雄名: 局数}
    pool: Dict[str, object] = field(default_factory=dict)
    # 玩家英雄池（完整形式，优先于 pool）：{英雄名: {games, wins, by_lane, last_match_id, ...}}
    # 由 tools/sync_results.py 写入；by_lane 让熟练度能按位置计算。
    pool_records: Dict[str, dict] = field(default_factory=dict)
    # 天梯档位切片：1..8 或 "divine"/"超凡" 等；空 = 用全体平均
    bracket: str = ""
    # 权重：[熟练度, 对位克制, 阵容契合, 版本强度, 配合协同]
    weights: Dict[str, float] = field(
        default_factory=lambda: {
            "proficiency": 0.32,
            "matchup": 0.22,
            "team_need": 0.22,
            "meta": 0.14,
            "synergy": 0.10,
        }
    )
    steam_id: str = ""             # 可选：用于从公开 API 导入英雄池
    auto_import_pool: bool = False  # 启动时是否自动导入
    import_min_games: int = 3       # 导入时忽略少于该局数的英雄
    hotkey: str = "ctrl+alt+d"      # 悬浮窗显隐快捷键（文档用，实际由 GUI 注册）
    overlay: Dict[str, object] = field(default_factory=dict)
    ui: Dict[str, object] = field(default_factory=dict)
    # 语音录入（只用 Windows 自带识别，离线、不联网）
    voice: Dict[str, object] = field(default_factory=dict)

    # ------------------------------------------------------------------ 读写
    @classmethod
    def load(cls, path: Optional[pathlib.Path] = None) -> "Config":
        candidates: List[pathlib.Path] = []
        if path:
            candidates.append(pathlib.Path(path))
        candidates += [ROOT / "config.json", default_config_path()]
        for p in candidates:
            if p.exists():
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    raise ValueError(f"配置文件 {p} 不是合法 JSON: {e}") from e
                return cls.from_dict(payload, source=str(p))
        return cls()

    @classmethod
    def from_dict(cls, payload: dict, source: str = "<dict>") -> "Config":
        c = cls()
        raw_pool = dict(payload.get("pool") or {})
        # 兼容两种写法：简写 [局数, 胜场] 与完整 {games, wins, by_lane, ...}
        for hero, val in raw_pool.items():
            if isinstance(val, dict):
                c.pool_records[str(hero)] = dict(val)
            else:
                c.pool[str(hero)] = val
        if payload.get("weights"):
            c.weights.update({k: float(v) for k, v in payload["weights"].items()})
        c.steam_id = str(payload.get("steam_id") or "")
        c.auto_import_pool = bool(payload.get("auto_import_pool", False))
        c.import_min_games = int(payload.get("import_min_games", 3))
        c.hotkey = str(payload.get("hotkey") or c.hotkey)
        c.bracket = str(payload.get("bracket") or "")
        c.overlay = dict(payload.get("overlay") or {})
        c.ui = dict(payload.get("ui") or {})
        c.voice = dict(payload.get("voice") or {})
        c._source = source  # type: ignore[attr-defined]
        return c

    def save(self, path: Optional[pathlib.Path] = None) -> pathlib.Path:
        p = pathlib.Path(path) if path else default_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # 输出时把简写池与完整记录池合并回一个 "pool"，简写在前、完整记录覆盖同名英雄
        merged: Dict[str, object] = dict(self.pool)
        for hero, rec in (self.pool_records or {}).items():
            merged[hero] = rec
        p.write_text(
            json.dumps(
                {
                    "pool": merged,
                    "weights": self.weights,
                    "steam_id": self.steam_id,
                    "auto_import_pool": self.auto_import_pool,
                    "import_min_games": self.import_min_games,
                    "hotkey": self.hotkey,
                    "bracket": self.bracket,
                    "overlay": self.overlay,
                    "ui": self.ui,
                    "voice": self.voice,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return p

    @property
    def source(self) -> str:
        return getattr(self, "_source", "<defaults>")

    # ------------------------------------------------------------------ 便捷
    def pool_items(self) -> List[Tuple[str, object]]:
        return list(self.pool.items())

    def overlay_options(self) -> Dict[str, object]:
        base = {
            "alpha": 0.92,
            "width": 460,
            "height": 620,
            "x": 24,
            "y": 96,
            "topmost": True,
            "top_n": 5,
            "font_size": 10,
            "click_through": False,
        }
        base.update(self.overlay or {})
        return base

    def voice_options(self) -> Dict[str, object]:
        """语音录入选项。

        ``enabled`` 只是「启动时是否预开监听」，不影响按钮/热键随时可用。
        """
        base = {
            "enabled": False,          # 启动悬浮窗后是否立刻开始听
            "culture": "",             # 空 = 自动（优先 zh-*）
            "mode": "hero",            # hero（受约束的英雄名语法，推荐）| dictation
            "min_confidence": 0.55,
            "seconds": 0,              # 0 = 一直听，直到再次按热键
            "auto_add": True,          # 听清后是否直接录入 BP（False = 只提示不录入）
            "hotkey": "ctrl+alt+v",
        }
        base.update(self.voice or {})
        return base
