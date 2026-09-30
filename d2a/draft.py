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

    def guess_enemy_lanes(self, book, positions: Optional[Dict[str, int]] = None) -> Dict[str, int]:
        """推断敌方各英雄的位置（1~5），返回 {英雄: 位置}。

        思路：**全局分配**，而不是「第 i 手固定猜第几个位置」。早先的实现用固定顺序
        ``[1,2,3,5,4]``，对摇摆位（如 Juggernaut 可 1/4、Mirana 可 2/4/5）误判率很高，
        而引擎给「同路敌人」的对位权重是 1.0、其余只有 0.2，一次误判影响很大。

        现在的打分由三部分组成：
        1. **先手顺序先验**：早拿的多为核心（1/2/3），晚拿的多为辅助（4/5）；
        2. **英雄自身的位置倾向**：按英雄的标签（lane/scaling/init/frontline/save/control）
           估计它在每个位置的合适度，而不是只看 ``lanes`` 是否包含；
        3. **阵容结构约束**：一个位置最多一人；不给「能打核心却排在最后」的情况加分。

        ``positions`` 可传入玩家手动指定的位置，优先级最高。
        """
        if self.enemy_lanes:
            return dict(self.enemy_lanes)
        heroes = self.enemy_heroes()
        if not heroes:
            return {}

        manual = dict(positions or {})
        out: Dict[str, int] = {}
        # 手动指定先占位
        for h, l in manual.items():
            if h in heroes and l in (1, 2, 3, 4, 5):
                out[h] = int(l)

        remaining = [h for h in heroes if h not in out]
        total = len(heroes)

        # 全局最佳优先分配（贪心最大权匹配）：
        # 把所有 (英雄, 位置) 组合按合适度排序，从高到低逐个「配对」，
        # 英雄已配对或位置已占用就跳过。这样不会出现「某英雄挑走了对别人
        # 唯一合适的位置」——而逐个英雄贪心会有这个毛病。
        pairs: List[Tuple[float, str, int]] = []
        for h in remaining:
            hero = book.heroes.get(h)
            pos = heroes.index(h)
            for lane in (1, 2, 3, 4, 5):
                if hero is not None and not hero.can_lane(lane):
                    continue
                pairs.append((Draft._lane_fit_score(hero, lane, pos, total), h, lane))
        pairs.sort(key=lambda x: (-x[0], x[1], x[2]))

        used_lanes = set(out.values())
        for score, h, lane in pairs:
            if h in out or lane in used_lanes:
                continue
            out[h] = int(lane)
            used_lanes.add(lane)

        # 兜底：实在排不下的（例如两个只能打 1 号位的核心），允许与别人同路，
        # 但位置仍然必须是该英雄合法的
        for h in remaining:
            if h in out:
                continue
            hero = book.heroes.get(h)
            out[h] = int(hero.lanes[0]) if (hero is not None and hero.lanes) else 5
        return out

    @staticmethod
    def _lane_fit_score(hero, lane: int, pick_index: int, total: int) -> float:
        """某个英雄打某个位置的合适度（越大越好）。"""
        # 1) 位置倾向：由英雄标签推断
        affinity = {
            1: hero.trait("scaling") * 1.0 + hero.trait("lane") * 1.0,
            2: hero.trait("scaling") * 0.8 + hero.trait("teamfight") * 0.8 + hero.trait("lane") * 0.8,
            3: hero.trait("frontline") * 1.0 + hero.trait("init") * 0.8 + hero.trait("sustain") * 0.5,
            4: hero.trait("init") * 0.9 + hero.trait("control") * 0.8 + hero.trait("escape") * 0.4,
            5: hero.trait("save") * 1.0 + hero.trait("control") * 0.7 + hero.trait("sustain") * 0.5,
        }.get(lane, 0.0)

        # 2) 顺位先验：早手偏核心，晚手偏辅助
        progress = (pick_index / max(1, total - 1)) if total > 1 else 0.0
        core_prior = {1: 3.0, 2: 2.6, 3: 2.4, 4: 1.0, 5: 0.6}[lane]
        supp_prior = {1: 0.6, 2: 1.0, 3: 1.2, 4: 2.6, 5: 3.0}[lane]
        prior = core_prior * (1.0 - progress) + supp_prior * progress
        return prior * 1.5 + affinity

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
