# -*- coding: utf-8 -*-
"""BP（Ban/Pick）状态机：记录选人顺序、分路归属，并推断敌方大概位置。

只做纯逻辑，不碰任何游戏进程/内存，输入完全来自用户的键盘录入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# 分路编号与可玩英雄的对应关系见 data/heroes.json 的 lanes 字段
LANE_NAMES = {1: "优势路核心(1)", 2: "中路(2)", 3: "劣势路(3)", 4: "游走/4号位", 5: "5号位辅助"}

# 常见分路模板：每个位置给出「期望承担的职责」，用于计算阵容缺口
# (lane, 需求描述, 需要的标签及最低总等级)
ROLE_TEMPLATES: Dict[int, dict] = {
    1: {"label": "1号位核心", "want": ("scaling", "lane"), "note": "后期输出与发育"},
    2: {"label": "2号位中单", "want": ("scaling", "teamfight"), "note": "中期带节奏与输出"},
    3: {"label": "3号位劣势路", "want": ("frontline", "init"), "note": "前排与开团"},
    4: {"label": "4号位游走", "want": ("init", "control"), "note": "游走与先手控制"},
    5: {"label": "5号位辅助", "want": ("save", "control"), "note": "保人与视野"},
}


@dataclass
class Pick:
    hero: str
    side: str  # "ally" | "enemy"
    order: int
    lane: Optional[int] = None  # 自己或队友的分路；敌方多为推测

    @property
    def is_ally(self) -> bool:
        return self.side == "ally"


@dataclass
class Draft:
    """一次选人阶段的完整状态。"""

    allies: List[Pick] = field(default_factory=list)
    enemies: List[Pick] = field(default_factory=list)
    bans: List[str] = field(default_factory=list)
    # 我们这一手要选的位置（1-5），None = 自动推荐一个
    my_lane: Optional[int] = None
    # 我方已确定的分路归属 {hero: lane}
    ally_lanes: Dict[str, int] = field(default_factory=dict)
    # 敌方位置推测 {hero: lane}
    enemy_lanes: Dict[str, int] = field(default_factory=dict)
    _counter: int = field(default=0, repr=False)

    # ------------------------------------------------------------------ 写入
    def add(self, hero: str, side: str, lane: Optional[int] = None) -> Pick:
        if side not in ("ally", "enemy"):
            raise ValueError("side 必须是 ally 或 enemy")
        if hero in self.picked():
            raise ValueError(f"{hero} 已被选走")
        self._counter += 1
        p = Pick(hero=hero, side=side, order=self._counter, lane=lane)
        (self.allies if side == "ally" else self.enemies).append(p)
        if lane:
            (self.ally_lanes if side == "ally" else self.enemy_lanes)[hero] = lane
        return p

    def remove(self, hero: str) -> bool:
        for lst in (self.allies, self.enemies):
            for i, p in enumerate(lst):
                if p.hero == hero:
                    lst.pop(i)
                    self.ally_lanes.pop(hero, None)
                    self.enemy_lanes.pop(hero, None)
                    return True
        return False

    def set_lane(self, hero: str, lane: int) -> None:
        if hero in self.ally_heroes():
            self.ally_lanes[hero] = lane
        elif hero in self.enemy_heroes():
            self.enemy_lanes[hero] = lane
        else:
            raise KeyError(hero)
        for p in self.allies + self.enemies:
            if p.hero == hero:
                p.lane = lane

    # ------------------------------------------------------------------ 读取
    def ally_heroes(self) -> List[str]:
        return [p.hero for p in self.allies]

    def enemy_heroes(self) -> List[str]:
        return [p.hero for p in self.enemies]

    def picked(self) -> List[str]:
        return self.ally_heroes() + self.enemy_heroes()

    def all_banned(self) -> List[str]:
        return list(self.bans)

    def is_empty(self) -> bool:
        return not self.allies and not self.enemies

    def taken_lanes(self) -> Dict[int, str]:
        """我方已占用的分路 -> 英雄。"""
        out: Dict[int, str] = {}
        for h, l in self.ally_lanes.items():
            out[int(l)] = h
        return out

    def open_lanes(self) -> List[int]:
        taken = set(self.taken_lanes())
        return [l for l in (1, 2, 3, 4, 5) if l not in taken]

    def next_lane(self) -> Optional[int]:
        """未指定位置时，按 1→5 的顺序推荐下一个待补位置。

        策略：优先补核心位（1/2/3 空着先补），再补辅助位。
        这是给「随机匹配、队友都在抢」的常见场景留的兜底。
        """
        if self.my_lane:
            return self.my_lane
        open_l = self.open_lanes()
        return open_l[0] if open_l else None

    def guess_enemy_lanes(self, book) -> Dict[str, int]:
        """按敌方选人顺序粗推位置：第一个偏核心，最后两个偏辅助。

        依据：正常 BP 里，敌方通常先拿核心摇摆位、后拿辅助位。
        这只是启发式推断，玩家可以在 UI 里手动改。
        """
        if self.enemy_lanes:
            return dict(self.enemy_lanes)
        out: Dict[str, int] = {}
        heroes = self.enemy_heroes()
        n = len(heroes)
        if n == 0:
            return out
        # 常见的“猜测顺序”：1,2,3,5,4
        guess_order = [1, 2, 3, 5, 4]
        for i, h in enumerate(heroes):
            want = guess_order[i] if i < len(guess_order) else 5
            hero = book.heroes.get(h)
            if hero is None:
                out[h] = want
                continue
            if hero.can_lane(want):
                out[h] = want
                continue
            # 不能打期望位置：就近选择最接近的合法位置
            legal = sorted(hero.lanes, key=lambda l: abs(l - want))
            out[h] = legal[0] if legal else want
        return out

    def lane_matchups(self, book) -> List[Tuple[str, str, int]]:
        """返回我方可能对上的敌方英雄 [(我, 敌, 路)]，用于计算对线克制。"""
        enemy_lanes = self.guess_enemy_lanes(book)
        mine = self.taken_lanes()
        res: List[Tuple[str, str, int]] = []
        for lane, my in mine.items():
            for e, el in enemy_lanes.items():
                if el == lane:
                    res.append((my, e, int(lane)))
        return res

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> dict:
        return {
            "allies": [p.hero for p in self.allies],
            "enemies": [p.hero for p in self.enemies],
            "bans": list(self.bans),
            "my_lane": self.my_lane,
            "ally_lanes": dict(self.ally_lanes),
            "enemy_lanes": dict(self.enemy_lanes),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Draft":
        d = cls(my_lane=payload.get("my_lane"), bans=list(payload.get("bans", [])))
        for h in payload.get("allies", []):
            d.add(h, "ally", payload.get("ally_lanes", {}).get(h))
        for h in payload.get("enemies", []):
            d.add(h, "enemy", payload.get("enemy_lanes", {}).get(h))
        return d

    # ------------------------------------------------------------------ 摘要
    def summary(self) -> str:
        a = ", ".join(
            f"{p.hero}({LANE_NAMES.get(self.ally_lanes.get(p.hero, 0), '?')})" if p.hero in self.ally_lanes else p.hero
            for p in self.allies
        ) or "（空）"
        e = ", ".join(self.enemy_heroes()) or "（空）"
        return f"我方: {a}\n敌方: {e}"
