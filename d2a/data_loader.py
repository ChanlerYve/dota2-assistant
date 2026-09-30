# -*- coding: utf-8 -*-
"""数据层：英雄库、克制矩阵、版本强度、玩家英雄池的加载与查询。

对外只暴露 :class:`HeroBook`，UI / 引擎都不直接读 JSON 文件。
所有数据分三类来源，优先级从低到高：

1. ``data/heroes.json``       —— 内置人工维护的英雄属性与能力标签（离线可用）
2. ``data/matchups.json``     —— 内置克制/协同矩阵（手工种子，可被在线数据覆盖）
3. ``data/meta.json``         —— 版本强度（胜率/出场率），由在线刷新写入
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data"

TRAIT_KEYS: Tuple[str, ...] = (
    "lane",
    "init",
    "counter",
    "teamfight",
    "control",
    "save",
    "frontline",
    "push",
    "sustain",
    "scaling",
    "escape",
)

# 中文/口语别名 -> 规范英雄名。只覆盖高频常用名，其余走模糊匹配。
ALIASES: Dict[str, str] = {
    "斧王": "Axe",
    "小小": "Tiny",
    "幽鬼": "Spectre",
    "幻影刺客": "Phantom Assassin",
    "幻刺": "Phantom Assassin",
    "敌法师": "Anti-Mage",
    "敌法": "Anti-Mage",
    "影魔": "Shadow Fiend",
    "火女": "Lina",
    "莉娜": "Lina",
    "电魂": "Razor",
    "闪电幽魂": "Razor",
    "冰女": "Crystal Maiden",
    "水晶室女": "Crystal Maiden",
    "剑圣": "Juggernaut",
    "主宰": "Juggernaut",
    "宙斯": "Zeus",
    "巫医": "Witch Doctor",
    "巫妖": "Lich",
    "潮汐": "Tidehunter",
    "潮汐猎人": "Tidehunter",
    "猛犸": "Magnus",
    "谜团": "Enigma",
    "谜团": "Enigma",
    "沉默": "Silencer",
    "沉默术士": "Silencer",
    "卡尔": "Invoker",
    "祈求者": "Invoker",
    "白牛": "Spirit Breaker",
    "裂魂人": "Spirit Breaker",
    "小小": "Tiny",
    "钢背": "Bristleback",
    "钢背兽": "Bristleback",
    "军团": "Legion Commander",
    "军团指挥官": "Legion Commander",
    "全能": "Omniknight",
    "全能骑士": "Omniknight",
    "毒龙": "Viper",
    "冥界亚龙": "Viper",
    "龙骑": "Dragon Knight",
    "龙骑士": "Dragon Knight",
    "屠夫": "Pudge",
    "帕吉": "Pudge",
    "小鱼人": "Slark",
    "斯拉克": "Slark",
    "火猫": "Ember Spirit",
    "灰烬之灵": "Ember Spirit",
    "土猫": "Earth Spirit",
    "大地之灵": "Earth Spirit",
    "蓝猫": "Storm Spirit",
    "风暴之灵": "Storm Spirit",
    "紫猫": "Void Spirit",
    "虚无之灵": "Void Spirit",
    "水人": "Morphling",
    "变体精灵": "Morphling",
    "骷髅王": "Wraith King",
    "冥魂大帝": "Wraith King",
    "流浪剑客": "Sven",
    "斯温": "Sven",
    "拍拍熊": "Ursa",
    "熊战士": "Ursa",
    "猴子": "Monkey King",
    "齐天大圣": "Monkey King",
    "美杜莎": "Medusa",
    "一姐": "Medusa",
    "虚空": "Faceless Void",
    "虚空假面": "Faceless Void",
    "火枪": "Sniper",
    "矮人狙击手": "Sniper",
    "敌法": "Anti-Mage",
    "大娜迦": "Medusa",
    "小娜迦": "Naga Siren",
    "毒狗": "Venomancer",
    "剧毒术士": "Venomancer",
    "萨尔": "Disruptor",
    "干扰者": "Disruptor",
    "戴泽": "Dazzle",
    "暗影牧师": "Dazzle",
    "暗牧": "Dazzle",
    "神谕": "Oracle",
    "神谕者": "Oracle",
    "光法": "Keeper of the Light",
    "光之守卫": "Keeper of the Light",
    "大牛": "Elder Titan",
    "上古巨神": "Elder Titan",
    "小牛": "Earthshaker",
    "撼地神牛": "Earthshaker",
    "土猫": "Earth Spirit",
    "飞机": "Gyrocopter",
    "矮人直升机": "Gyrocopter",
    "月骑": "Luna",
    "露娜": "Luna",
    "小黑": "Drow Ranger",
    "卓尔游侠": "Drow Ranger",
    "白虎": "Mirana",
    "米拉娜": "Mirana",
    "风行": "Windranger",
    "风行者": "Windranger",
    "赏金": "Bounty Hunter",
    "赏金猎人": "Bounty Hunter",
    "蚂蚁": "Weaver",
    "编织者": "Weaver",
    "隐刺": "Riki",
    "力丸": "Riki",
    "蜘蛛": "Broodmother",
    "育母蜘蛛": "Broodmother",
    "老鹿": "Leshrac",
    "痛苦女王": "Queen of Pain",
    "女王": "Queen of Pain",
    "帕克": "Puck",
    "仙女龙": "Puck",
    "伐木机": "Timbersaw",
    "发条": "Clockwerk",
    "发条技师": "Clockwerk",
    "蝙蝠": "Batrider",
    "蝙蝠骑士": "Batrider",
    "沙王": "Sand King",
    "斧王": "Axe",
    "血魔": "Bloodseeker",
    "末日": "Doom",
    "末日使者": "Doom",
    "半人马": "Centaur Warrunner",
    "人马": "Centaur Warrunner",
    "马格纳斯": "Magnus",
    "死亡先知": "Death Prophet",
    "DP": "Death Prophet",
    "黑鸟": "Outworld Destroyer",
    "殁境神蚀者": "Outworld Destroyer",
    "冥魂": "Wraith King",
    "大圣": "Monkey King",
    "凤凰": "Phoenix",
    "帕吉": "Pudge",
    "巫妖王": "Lich",
    "修补匠": "Tinker",
    "修补匠": "Tinker",
    "矮人": "Sniper",
    "大树": "Treant Protector",
    "树精卫士": "Treant Protector",
    "死灵法": "Necrophos",
    "瘟疫法师": "Necrophos",
    "小狗": "Lifestealer",
    "噬魂鬼": "Lifestealer",
    "骨法": "Pugna",
    "帕格纳": "Pugna",
    "VS": "Vengeful Spirit",
    "复仇之魂": "Vengeful Spirit",
    "海民": "Kunkka",
    "昆卡": "Kunkka",
    "船长": "Kunkka",
    "海象": "Tusk",
    "巨牙海民": "Tusk",
    "小Y": "Shadow Shaman",
    "暗影萨满": "Shadow Shaman",
    "蓝胖": "Ogre Magi",
    "食人魔魔法师": "Ogre Magi",
    "斧王": "Axe",
    "猛犸": "Magnus",
    "军团指挥官": "Legion Commander",
    "bane": "Bane",
    "pom": "Mirana",
    "am": "Anti-Mage",
    "tb": "Terrorblade",
    "sf": "Shadow Fiend",
    "qop": "Queen of Pain",
    "cm": "Crystal Maiden",
    "ogre": "Ogre Magi",
    "es": "Earthshaker",
    "sk": "Sand King",
    "wk": "Wraith King",
    "lc": "Legion Commander",
    "ns": "Night Stalker",
    "pa": "Phantom Assassin",
    "pl": "Phantom Lancer",
    "dusa": "Medusa",
    "mk": "Monkey King",
    "ww": "Winter Wyvern",
    "wd": "Witch Doctor",
    "sb": "Spirit Breaker",
    "bs": "Bloodseeker",
    "ck": "Chaos Knight",
    "dk": "Dragon Knight",
    "dp": "Death Prophet",
    "od": "Outworld Destroyer",
    "ta": "Templar Assassin",
    "aw": "Arc Warden",
    "np": "Nature's Prophet",
    "furion": "Nature's Prophet",
}


def normalize(name: str) -> str:
    """归一化英雄名：去空白/标点、转小写、统一全角。"""
    s = unicodedata.normalize("NFKC", name or "").strip().lower()
    s = re.sub(r"[\s'’\-_\.]+", "", s)
    return s


# 天梯档位：OpenDota heroStats 里的 1_pick..8_pick 就是这个编号。
# 实测：8(Immortal) 在公开数据里恒为 0，所以「高分段」实际最高取到 7(Divine)。
BRACKETS: Dict[int, str] = {
    1: "Herald",
    2: "Guardian",
    3: "Crusader",
    4: "Archon",
    5: "Legend",
    6: "Ancient",
    7: "Divine",
    8: "Immortal",
}

BRACKET_ALIASES: Dict[str, int] = {
    "herald": 1, "卫士": 1, "先锋": 1,
    "guardian": 2, "guardian2": 2, "守卫": 2,
    "crusader": 3, "十字军": 3, "中军": 3,
    "archon": 4, "执政官": 4, "统帅": 4,
    "legend": 5, "legend5": 5, "传奇": 5,
    "ancient": 6, "远古": 6, "神话": 6,
    "divine": 7, "神圣": 7, "超凡": 7,
    "immortal": 8, "不朽": 8, "冠绝": 8,
}


def parse_bracket(value) -> Optional[int]:
    """把 ``"divine"`` / ``"Divine"`` / ``7`` / ``"超凡"`` 解析成档位编号。"""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 8 else None
    s = str(value).strip().lower()
    if s.isdigit():
        b = int(s)
        return b if 1 <= b <= 8 else None
    return BRACKET_ALIASES.get(s)


class HeroNotFound(KeyError):
    """无法把用户输入解析成英雄。"""


@dataclass(frozen=True)
class Hero:
    name: str
    attr: str
    lanes: Tuple[int, ...]
    damage: str
    tags: Tuple[str, ...]
    traits: Dict[str, int]

    def trait(self, key: str) -> int:
        return int(self.traits.get(key, 0))

    def can_lane(self, lane: int) -> bool:
        return lane in self.lanes

    @property
    def is_support(self) -> bool:
        return 4 in self.lanes or 5 in self.lanes

    @property
    def is_core(self) -> bool:
        return any(l in (1, 2, 3) for l in self.lanes)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "attr": self.attr,
            "lanes": list(self.lanes),
            "damage": self.damage,
            "tags": list(self.tags),
            "traits": dict(self.traits),
        }


@dataclass
class PlayerHeroStat:
    """玩家在某英雄上的历史表现。

    ``by_lane`` 是「按位置拆分」的战绩：``{位置: (局数, 胜场)}``，位置取 1~5。
    来源是 OpenDota 每场比赛的 ``position_est`` 字段（已实测可用）。
    没有它时，熟练度只能回答「你会不会玩这个英雄」，
    有了它才能回答「你会不会用这个英雄打这个位置」——这两件事差别很大。
    """

    hero: str
    games: int = 0
    wins: int = 0
    # 来自公开 API 的额外信息
    last_played: Optional[int] = None
    # {位置: [局数, 胜场]}；位置 1~5
    by_lane: Dict[int, list] = field(default_factory=dict)
    # 最近一次同步到的 match_id（用于增量回写）
    last_match_id: Optional[int] = None

    @property
    def winrate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    def lane_games(self, lane: int) -> int:
        rec = self.by_lane.get(int(lane)) or self.by_lane.get(str(lane))
        return int(rec[0]) if rec else 0

    def lane_wins(self, lane: int) -> int:
        rec = self.by_lane.get(int(lane)) or self.by_lane.get(str(lane))
        return int(rec[1]) if rec else 0

    def lane_winrate(self, lane: int) -> Optional[float]:
        g = self.lane_games(lane)
        return (self.lane_wins(lane) / g) if g else None

    def add_lane_result(self, lane: int, win: bool) -> None:
        rec = self.by_lane.get(int(lane))
        if rec is None:
            rec = [0, 0]
            self.by_lane[int(lane)] = rec
        rec[0] += 1
        if win:
            rec[1] += 1

    def to_dict(self) -> dict:
        out = {"hero": self.hero, "games": self.games, "wins": self.wins}
        if self.by_lane:
            # 键统一转成字符串，方便写进 JSON 再读回来
            out["by_lane"] = {str(k): list(v) for k, v in sorted(self.by_lane.items())}
        if self.last_match_id:
            out["last_match_id"] = int(self.last_match_id)
        if self.last_played:
            out["last_played"] = int(self.last_played)
        return out

    @classmethod
    def from_dict(cls, payload: dict) -> "PlayerHeroStat":
        st = cls(
            hero=str(payload.get("hero") or ""),
            games=int(payload.get("games") or 0),
            wins=int(payload.get("wins") or 0),
        )
        for k, v in (payload.get("by_lane") or {}).items():
            try:
                lane = int(k)
            except (TypeError, ValueError):
                continue
            seq = list(v) if isinstance(v, (list, tuple)) else [0, 0]
            st.by_lane[lane] = [int(seq[0]) if seq else 0, int(seq[1]) if len(seq) > 1 else 0]
        if payload.get("last_match_id"):
            st.last_match_id = int(payload["last_match_id"])
        if payload.get("last_played"):
            st.last_played = int(payload["last_played"])
        return st


@dataclass
class HeroBook:
    """英雄库 + 各类数据表的统一入口。"""

    heroes: Dict[str, Hero] = field(default_factory=dict)
    aliases: Dict[str, str] = field(default_factory=dict)
    # 克制矩阵: {hero: {enemy: 优势值}}，优势值 > 0 表示我方占优（单位：胜率差值）
    matchups: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # 协同矩阵: {hero: {ally: 协同值}}
    synergies: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # 版本强度: {hero: {"winrate": float, "pickrate": float, "brackets": {档位: [pick, win]}, ...}}
    meta: Dict[str, dict] = field(default_factory=dict)
    meta_source: str = "builtin"
    meta_patch: str = "unknown"
    meta_patch_date: str = ""
    meta_fetched_at: int = 0
    live_source: str = "builtin"
    live_fetched_at: int = 0
    alias_source: str = "builtin"
    _index: Dict[str, str] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ 构建
    @classmethod
    def load(cls, data_dir: pathlib.Path = DATA_DIR) -> "HeroBook":
        book = cls()
        heroes_path = data_dir / "heroes.json"
        if not heroes_path.exists():
            raise FileNotFoundError(
                f"缺少英雄库 {heroes_path}；请先运行 python tools/gen_hero_data.py"
            )
        raw = json.loads(heroes_path.read_text(encoding="utf-8"))
        for h in raw["heroes"]:
            hero = Hero(
                name=h["name"],
                attr=h.get("attr", "all"),
                lanes=tuple(int(x) for x in h.get("lanes", [])),
                damage=h.get("damage", "mixed"),
                tags=tuple(h.get("tags", [])),
                traits={k: int(h.get("traits", {}).get(k, 0)) for k in TRAIT_KEYS},
            )
            book.heroes[hero.name] = hero

        for fn, attr in (("matchups.json", "matchups"), ("synergies.json", "synergies")):
            p = data_dir / fn
            if p.exists():
                payload = json.loads(p.read_text(encoding="utf-8"))
                setattr(book, attr, {k: dict(v) for k, v in payload.get("pairs", {}).items()})

        # 真实对位数据优先：同一条关系以线上数据为准，线上没覆盖的用内置种子补齐。
        live_path = data_dir / "matchups_live.json"
        if live_path.exists():
            live = json.loads(live_path.read_text(encoding="utf-8"))
            book.live_source = live.get("source", "live")
            book.live_fetched_at = int(live.get("fetched_at") or 0)
            for a, row in live.get("pairs", {}).items():
                book.matchups.setdefault(a, {}).update({k: float(v) for k, v in row.items()})

        meta_path = data_dir / "meta.json"
        if meta_path.exists():
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            book.meta = {k: dict(v) for k, v in payload.get("heroes", {}).items()}
            book.meta_source = payload.get("source", "unknown")
            book.meta_patch = payload.get("patch", "unknown")
            book.meta_patch_date = payload.get("patch_date", "")
            book.meta_fetched_at = int(payload.get("fetched_at") or 0)

        book._build_index(data_dir)
        return book

    def _build_index(self, data_dir: pathlib.Path = DATA_DIR) -> None:
        """构建名字索引。优先级（高 → 低）：
        aliases.json 显式别名 → 内置 ALIASES → 官方全名 → 官方末词短名。

        修复过的坑：末词短名必须用 ``setdefault`` 且跳过 <=2 字符的键。
        以前是直接赋值，且索引按 ``self.heroes`` 的**字典顺序**构建，
        于是 "Vengeful Spirit" 的末词 "spirit" 会覆盖掉短名，
        而 "ls" 这种两字母缩写又会被末词索引污染（实测 ``ls`` 曾错解析成
        Vengeful Spirit 而不是 Lifestealer）。
        """
        idx: Dict[str, str] = {}
        # 1) 显式别名优先（玩家可自己往 aliases.json 里加口语叫法）
        self.alias_source = "builtin"
        alias_path = data_dir / "aliases.json"
        if alias_path.exists():
            try:
                raw_aliases = json.loads(alias_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw_aliases = {}
            n = 0
            for alias, name in raw_aliases.items():
                if str(alias).startswith("_") or name not in self.heroes:
                    continue
                idx[normalize(alias)] = name
                n += 1
            if n:
                self.alias_source = f"{alias_path.name}({n})"
        for alias, name in ALIASES.items():
            if name in self.heroes:
                idx.setdefault(normalize(alias), name)
        # 2) 官方全名
        for name in self.heroes:
            idx[normalize(name)] = name
        # 3) 官方末词短名（只做兜底，且要求 >=3 字符，避免 "ls"/"io" 这类噪声键）
        for name in self.heroes:
            last = normalize(name.split()[-1])
            if len(last) >= 3:
                idx.setdefault(last, name)
        self._index = idx

    # ------------------------------------------------------------------ 查询
    def pickrate(self, hero: str) -> int:
        """出场数（用于歧义消解：一个缩写命中多个英雄时，取更常见的那个）。"""
        return int((self.meta.get(hero) or {}).get("pickrate") or 0)

    def candidates(self, text: str, limit: int = 5) -> List[Hero]:
        """返回所有可能匹配的英雄，按「匹配强度 + 出场率」降序。

        用于 UI 的「你是不是想选 X？」补全提示，以及给 :meth:`resolve` 消歧。
        比 resolve 宽松：允许 1 个字符的输入（打 ``s`` 就能看到一堆 S 开头的英雄）。
        """
        key = normalize(text)
        if not key:
            return []
        scored = self._score_matches(key, min_prefix_len=1)
        return [self.heroes[n] for _, n in scored[:limit]]

    def _score_matches(self, key: str, min_prefix_len: int = 2) -> List[Tuple[float, str]]:
        """给每个英雄算一个匹配分，返回 [(分, 英雄名)] 降序。

        匹配分只表达「这个输入有多像这个英雄」，不表达英雄强弱：
        别名/全名命中 1.0、前缀 0.9、包含 0.75、编辑距离按相似度折算。
        同一档内用出场数排序，这样 ``ls`` 会落到 Lifestealer，而不会被
        ``Vengeful Spirit`` 的末词索引抢走。
        """
        out: Dict[str, float] = {}
        for hero in self.heroes:
            full = normalize(hero)
            score = 0.0
            if key == full:
                score = 1.0
            elif len(key) >= min_prefix_len and full.startswith(key):
                score = 0.9
            elif len(key) >= 3 and key in full:
                score = 0.75
            elif len(key) >= 3:
                import difflib

                r = difflib.SequenceMatcher(None, key, full).ratio()
                if r >= 0.78:
                    score = 0.5 + (r - 0.78) * 2.0  # 0.78→0.5, 1.0→0.94
            if score > 0:
                out[hero] = score
        # 显式别名直接给满分（优先级最高）
        for k, name in self._index.items():
            if k == key and name in self.heroes:
                out[name] = 1.0
        ranked = sorted(out.items(), key=lambda kv: (-kv[1], -self.pickrate(kv[0]), kv[0]))
        return [(s, n) for n, s in ranked]

    def resolve(self, text: str) -> Hero:
        """把用户输入解析为英雄（别名 / 官方名 / 缩写 / 前缀 / 模糊匹配）。

        消歧规则：命中多个时取「匹配强度优先、同强度取出场率更高」的那个。
        """
        key = normalize(text)
        if not key:
            raise HeroNotFound(text)
        scored = self._score_matches(key)
        if not scored:
            raise HeroNotFound(text)
        return self.heroes[scored[0][1]]

    def try_resolve(self, text: str) -> Optional[Hero]:
        try:
            return self.resolve(text)
        except HeroNotFound:
            return None

    def by_lane(self, lane: int) -> List[Hero]:
        return [h for h in self.heroes.values() if h.can_lane(lane)]

    def matchup(self, hero: str, enemy: str) -> float:
        """hero 对 enemy 的优势值（单位：胜率差，正数=优势）。"""
        v = self.matchups.get(hero, {}).get(enemy)
        if v is not None:
            return float(v)
        # 对称回退：反向取负
        v = self.matchups.get(enemy, {}).get(hero)
        return -float(v) if v is not None else 0.0

    def synergy(self, a: str, b: str) -> float:
        v = self.synergies.get(a, {}).get(b)
        if v is not None:
            return float(v)
        v = self.synergies.get(b, {}).get(a)
        return float(v) if v is not None else 0.0

    def winrate(self, hero: str, default: float = 0.5) -> float:
        """版本胜率；无数据时回退到 0.5（中性），而不是编造数字。"""
        rec = self.meta.get(hero)
        if rec and rec.get("winrate"):
            return float(rec["winrate"])
        return default

    def has_meta(self, hero: str) -> bool:
        return hero in self.meta and bool(self.meta[hero].get("winrate"))

    def bracket_winrate(self, hero: str, bracket: Optional[int]) -> Optional[float]:
        """指定天梯档位下的胜率。没有该档位数据时返回 None（不假装有）。"""
        if not bracket:
            return None
        rec = self.meta.get(hero) or {}
        bk = rec.get("brackets") or {}
        row = bk.get(bracket) or bk.get(str(bracket))
        if not row:
            return None
        try:
            picks, wins = int(row[0]), int(row[1])
        except (TypeError, ValueError, IndexError):
            return None
        if picks <= 0:
            return None
        return wins / picks

    def bracket_picks(self, hero: str, bracket: Optional[int]) -> int:
        if not bracket:
            return 0
        rec = self.meta.get(hero) or {}
        bk = rec.get("brackets") or {}
        row = bk.get(bracket) or bk.get(str(bracket))
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError, IndexError):
            return 0

    def bracket_label(self, bracket: Optional[int]) -> str:
        if not bracket:
            return "全体"
        return BRACKETS.get(int(bracket), str(bracket))

    def names(self) -> List[str]:
        return sorted(self.heroes)

    def validate(self) -> List[str]:
        """自检：返回问题列表（空 = 健康）。"""
        problems: List[str] = []
        if len(self.heroes) < 100:
            problems.append(f"英雄数量偏少: {len(self.heroes)}")
        for name, h in self.heroes.items():
            if not h.lanes:
                problems.append(f"{name}: 未声明可用分路")
            if h.attr not in ("str", "agi", "int", "all"):
                problems.append(f"{name}: 未知主属性 {h.attr}")
            for k, v in h.traits.items():
                if not 0 <= v <= 3:
                    problems.append(f"{name}: 标签 {k}={v} 超出 0-3")
        for a, row in self.matchups.items():
            if a not in self.heroes:
                problems.append(f"克制矩阵含未知英雄: {a}")
            for b in row:
                if b not in self.heroes:
                    problems.append(f"克制矩阵 {a} 指向未知英雄 {b}")
        return problems
