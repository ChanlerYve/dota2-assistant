# -*- coding: utf-8 -*-
"""推荐引擎：把「我会玩什么」与「这局怎么赢」合成一个可解释的打分。

打分模型（全部落在 0~1，最后 × 惩罚系数 × 100）
------------------------------------------------
    总分 = 100 × ( w_p·熟练度 + w_m·克制对位 + w_t·阵容契合
                  + w_g·版本强度 + w_s·配合协同 ) × 风险惩罚

各因子含义与归一化方式：

* **熟练度 proficiency**：贝叶斯收缩后的胜率（把「3 局全胜」这类小样本拉回均值），
  再叠加局数带来的机制熟练加成。这是权重最高的因子——不会玩的英雄再强也没意义。
* **克制对位 matchup**：敌方每个英雄对该英雄的克制值取我方面向的净值，
  其中「可能同路对线」的敌人权重更高（对线期崩盘是最常见的输法）。
* **阵容契合 team_need**：我方阵容缺什么（先手/硬控/前排/救人/清线），
  这一手能不能补上；同时考虑该英雄在目标位置上的适配度与阵容冗余度。
* **版本强度 meta**：当前版本的英雄胜率，只在有真实数据时计入（无数据=中性 0.5）。
* **配合协同 synergy**：与已选队友的组合技加成（猛犸+虚空之类）。

风险惩罚 multiplier
-------------------
基础 1.0，命中以下情况才扣，且每项都有上限，避免把候选直接打到地下：

* 我方完全没有先手/硬控，且这一手也没补上 → 团战开不起来
* 敌方物理输出高而我方没有前排 → 会被顶着推
* 敌方推进强而我方没有清线 → 会被兵线拖死
* 熟练度极低（几乎没玩过这个英雄）
* 顺位僵化（最后几手还选摇摆位，位置安排不上）

设计原则
--------
1. **可解释**：每个分数都能拆成上面的因子，UI 必须能说清楚「为什么推它」。
2. **不编数据**：没有真实胜率/克制数据时按中性 0.5 处理，并在 UI 标注数据来源。
3. **纯函数**：引擎不依赖网络、不依赖随机数，便于单测与复现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .data_loader import Hero, HeroBook, PlayerHeroStat, TRAIT_KEYS
from .draft import ROLE_TEMPLATES, Draft

# --------------------------------------------------------------------------- 配置


@dataclass
class Weights:
    """各因子权重。玩家可以在 UI 里拖滑块实时调整，不必改代码。"""

    proficiency: float = 0.32
    matchup: float = 0.22
    team_need: float = 0.22
    meta: float = 0.14
    synergy: float = 0.10

    def normalized(self) -> "Weights":
        s = self.proficiency + self.matchup + self.team_need + self.meta + self.synergy
        if s <= 0:
            return Weights()
        return Weights(
            proficiency=self.proficiency / s,
            matchup=self.matchup / s,
            team_need=self.team_need / s,
            meta=self.meta / s,
            synergy=self.synergy / s,
        )


@dataclass
class Tuning:
    """引擎的数值调参集中在这里，方便做「稳健型/激进型」预设。"""

    # 熟练度
    prior_games: float = 8.0          # 贝叶斯收缩强度：局数越少越往基准胜率靠
    benchmark_winrate: float = 0.5    # 基准胜率（收缩目标）
    comfort_games: float = 40.0       # 达到该局数即拿满机制熟练加分
    comfort_bonus: float = 0.18       # 机制熟练加分的最大幅度
    # 对位
    enemy_pickrate_prior: float = 0.2  # 未知位置敌人的权重（其余按同路加权）
    # 版本强度
    meta_span: float = 0.08           # 胜率 ±8pp 映射到 0~1，避免小差异被放大
    # 协同
    synergy_span: float = 0.05
    # 惩罚上限
    max_penalty: float = 0.30
    # 早手摇摆位加分上限
    flexibility_bonus: float = 0.04


DEFAULT_WEIGHTS = Weights()
DEFAULT_TUNING = Tuning()

# 阵容需求目标：低于该值即认为「缺」
NEED_TARGET = {
    "init": 4.0,       # 先手
    "control": 5.0,    # 硬控
    "frontline": 4.0,  # 前排
    "save": 2.0,       # 救人
    "push": 3.0,       # 清线/推塔
}

# 递减收益：第 n 个英雄在某个标签上的边际贡献
DIMINISH = (1.0, 0.6, 0.35, 0.2, 0.12)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _trait_sum(heroes: Sequence[Hero], key: str) -> float:
    """多个英雄在某标签上的合计贡献（递减收益：两个 3 级控不如一个 3 级加一个 1 级值钱）。"""
    values = sorted((h.trait(key) for h in heroes), reverse=True)
    return sum(v * DIMINISH[i] for i, v in enumerate(values) if i < len(DIMINISH))


def _damage_profile(heroes: Sequence[Hero]) -> Dict[str, float]:
    """粗略统计伤害类型构成，用于发现「全物理被护甲吃死」这类问题。"""
    prof = {"physical": 0.0, "magic": 0.0, "pure": 0.0, "mixed": 0.0}
    for h in heroes:
        prof[h.damage] = prof.get(h.damage, 0.0) + 1.0
    return prof


# --------------------------------------------------------------------------- 结果


@dataclass
class Candidate:
    hero: Hero
    score: float                                # 0~100
    breakdown: Dict[str, float] = field(default_factory=dict)
    penalties: List[Tuple[str, float]] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    position: Optional[int] = None
    games: int = 0
    winrate: float = 0.0

    @property
    def stars(self) -> int:
        """给 UI 用的 1~5 星。"""
        if self.score >= 78:
            return 5
        if self.score >= 68:
            return 4
        if self.score >= 58:
            return 3
        if self.score >= 48:
            return 2
        return 1

    def to_dict(self) -> dict:
        return {
            "hero": self.hero.name,
            "score": round(self.score, 1),
            "stars": self.stars,
            "position": self.position,
            "games": self.games,
            "winrate": round(self.winrate, 3),
            "breakdown": {k: round(v, 3) for k, v in self.breakdown.items()},
            "penalties": [(k, round(v, 3)) for k, v in self.penalties],
            "reasons": self.reasons,
            "risks": self.risks,
            "attr": self.hero.attr,
            "lanes": list(self.hero.lanes),
            "tags": list(self.hero.tags),
        }


# --------------------------------------------------------------------------- 引擎


class RecommendationEngine:
    def __init__(
        self,
        book: HeroBook,
        pool: Optional[Dict[str, PlayerHeroStat]] = None,
        weights: Optional[Weights] = None,
        tuning: Optional[Tuning] = None,
        player_baseline_winrate: float = 0.5,
    ) -> None:
        self.book = book
        self.pool: Dict[str, PlayerHeroStat] = dict(pool or {})
        self.w = (weights or DEFAULT_WEIGHTS).normalized()
        self.t = tuning or DEFAULT_TUNING
        self.player_baseline_winrate = player_baseline_winrate

    # ------------------------------------------------------------- 玩家熟练度
    def set_pool(self, stats: Iterable[PlayerHeroStat]) -> None:
        self.pool = {s.hero: s for s in stats}

    def proficiency(self, hero: str) -> Tuple[float, int, float, str]:
        """返回 (熟练度 0~1, 局数, 收缩后胜率, 说明)。"""
        st = self.pool.get(hero)
        if st is None or st.games <= 0:
            return 0.0, 0, 0.0, "英雄池里没有记录，视为不会玩"

        g = st.games
        wr = st.winrate
        # 贝叶斯收缩：小样本向基准胜率靠拢，避免「1 局 1 胜 = 100%」
        base = self.player_baseline_winrate or self.t.benchmark_winrate
        k = self.t.prior_games
        shrunk = (wr * g + base * k) / (g + k)
        # 胜率项：0.5 基准映射 0.5，±0.25 打满
        wr_term = _clamp(0.5 + (shrunk - 0.5) * 2.0)
        # 熟练加成：局数越多越熟练（机制、连招、出装）
        comfort = _clamp(g / self.t.comfort_games)
        score = _clamp((1 - self.t.comfort_bonus) * wr_term + self.t.comfort_bonus * comfort)
        note = f"你打了 {g} 局，胜率 {wr:.0%}" + ("（样本偏少，已做收缩）" if g < 10 else "")
        return score, g, wr, note

    # ------------------------------------------------------------- 对位克制
    def _enemy_lane_weights(self, draft: Draft, position: Optional[int]) -> Dict[str, float]:
        """给每个敌人分配打对位的权重：可能同路的敌人权重最高。"""
        w: Dict[str, float] = {}
        enemy_lanes = draft.guess_enemy_lanes(self.book)
        for e in draft.enemy_heroes():
            el = enemy_lanes.get(e)
            if position and el == position:
                w[e] = 1.0            # 直接对线
            elif position and el and abs(int(el) - int(position)) == 1:
                w[e] = 0.45           # 邻路，前中期会互相对上
            else:
                w[e] = self.t.enemy_pickrate_prior
        return w

    def matchup(self, hero: str, draft: Draft, position: Optional[int]) -> Tuple[float, List[str]]:
        """对位得分 0~1 与文字理由。0.5 表示中性。"""
        if not draft.enemies:
            return 0.5, []
        weights = self._enemy_lane_weights(draft, position)
        total_w = sum(weights.values()) or 1.0
        delta = 0.0
        good: List[Tuple[float, str]] = []
        bad: List[Tuple[float, str]] = []
        for e, ew in weights.items():
            v = self.book.matchup(hero, e)  # 正数 = 我占优
            delta += v * ew
            if v >= 0.03:
                good.append((v, e))
            elif v <= -0.03:
                bad.append((abs(v), e))
        delta /= total_w
        score = _clamp(0.5 + delta * 5.0)  # ±10pp 打满
        reasons: List[str] = []
        good.sort(reverse=True)
        bad.sort(reverse=True)
        if good:
            reasons.append("克制: " + "、".join(f"{e}(+{v:.0%})" for v, e in good[:3]))
        if bad:
            reasons.append("被克制: " + "、".join(f"{e}(-{v:.0%})" for v, e in bad[:3]))
        return score, reasons

    # ------------------------------------------------------------- 阵容需要
    def team_needs(self, draft: Draft) -> Dict[str, float]:
        """我方阵容还缺什么：返回 {标签: 缺口(0~1)}。"""
        mine = [self.book.heroes[h] for h in draft.ally_heroes() if h in self.book.heroes]
        needs: Dict[str, float] = {}
        for key, target in NEED_TARGET.items():
            have = _trait_sum(mine, key)
            needs[key] = _clamp((target - have) / target)
        return needs

    def _need_score(self, hero: Hero, draft: Draft) -> Tuple[float, List[str]]:
        mine = [self.book.heroes[h] for h in draft.ally_heroes() if h in self.book.heroes]
        needs = self.team_needs(draft)
        if not mine:
            return 0.6, []
        gains: List[Tuple[float, str]] = []
        for key, gap in needs.items():
            if gap <= 0:
                continue
            marginal = DIMINISH[min(len(mine), len(DIMINISH) - 1)]
            gain = gap * hero.trait(key) * marginal
            if gain > 0.05:
                gains.append((gain, key))
        raw = sum(g for g, _ in gains)
        score = _clamp(0.5 + raw * 0.6)
        label = {
            "init": "先手",
            "control": "硬控",
            "frontline": "前排",
            "save": "救人",
            "push": "清线",
        }
        gains.sort(reverse=True)
        # 理由要能直接读：``补先手（全队此项缺口 60%）``
        reasons = [
            f"补{label.get(k, k)}（全队此项缺口 {needs[k]:.0%}）" for _, k in gains[:3]
        ]
        return score, reasons

    def _position_fit(self, hero: Hero, lane: Optional[int]) -> float:
        if not lane:
            return 0.5
        if not hero.can_lane(lane):
            return 0.15
        want = ROLE_TEMPLATES.get(lane, {}).get("want", ())
        if not want:
            return 0.5
        vals = [hero.trait(k) / 3.0 for k in want]
        return _clamp(0.3 + 0.7 * (sum(vals) / len(vals)))

    def _redundancy(self, hero: Hero, draft: Draft) -> float:
        """阵容冗余度 0~1：同一职责堆太多会拖慢节奏。"""
        mine = [self.book.heroes[h] for h in draft.ally_heroes() if h in self.book.heroes]
        if not mine:
            return 0.0
        key = "scaling" if hero.trait("scaling") >= 2 else "lane"
        same = sum(1 for h in mine if h.trait(key) >= 2)
        return _clamp(same / 4.0)

    def _lane_deficit(self, hero: Hero, draft: Draft, lane: Optional[int]) -> float:
        """分路安排不下的程度 0~1（缺少可用分路就是硬伤）。"""
        if not draft.allies:
            return 0.0
        open_l = draft.open_lanes()
        if not open_l:
            return 0.0
        if any(hero.can_lane(l) for l in open_l):
            return 0.0
        return 1.0

    def team_fit(self, hero: Hero, draft: Draft, lane: Optional[int]) -> Tuple[float, List[str]]:
        need_s, reasons = self._need_score(hero, draft)
        pos_s = self._position_fit(hero, lane)
        score = _clamp(0.6 * need_s + 0.4 * pos_s)
        return score, reasons

    # ------------------------------------------------------------- 版本 & 协同
    def meta_score(self, hero: str) -> float:
        wr = self.book.winrate(hero)
        return _clamp(0.5 + (wr - 0.5) / self.t.meta_span * 0.5)

    def synergy(self, hero: str, draft: Draft) -> Tuple[float, List[str]]:
        if not draft.allies:
            return 0.5, []
        vals = [(self.book.synergy(hero, a), a) for a in draft.ally_heroes()]
        if not vals:
            return 0.5, []
        best = sorted(vals, reverse=True)
        delta = max(v for v, _ in vals) * 0.7 + (sum(v for v, _ in vals) / len(vals)) * 0.3
        score = _clamp(0.5 + delta / self.t.synergy_span * 0.5)
        reasons = [f"和 {a} 有组合" for v, a in best[:2] if v >= 0.02]
        return score, reasons

    # ------------------------------------------------------------- 风险惩罚
    def penalties(self, hero: Hero, draft: Draft, lane: Optional[int], prof: float, games: int) -> List[Tuple[str, float]]:
        out: List[Tuple[str, float]] = []
        mine = [self.book.heroes[h] for h in draft.ally_heroes() if h in self.book.heroes]
        lineup = mine + [hero]
        enemy = [self.book.heroes[h] for h in draft.enemy_heroes() if h in self.book.heroes]

        # 1) 扎实的开团与硬控
        if _trait_sum(lineup, "init") < 2.5:
            out.append(("全队缺少先手，团战开不起来", 0.10))
        if _trait_sum(lineup, "control") < 3.0:
            out.append(("全队几乎没有硬控，留不住人", 0.08))

        # 2) 伤害构成
        #    「全魔法被 BKB/魔抗吃死」只取决于我方阵容，与敌方是否已选无关，
        #    因此不能放在 enemy 分支里（否则前期录入时给不出这个提示）。
        prof_d = _damage_profile(lineup)
        if len(lineup) >= 3 and prof_d["magic"] >= len(lineup) * 0.6:
            out.append(("全队魔法伤害偏多，注意对方魔抗/BKB", 0.05))

        # 3) 对面阵容带来的针对性风险
        if enemy:
            phys = sum(1 for h in enemy if h.damage in ("physical", "mixed"))
            if phys >= 3 and _trait_sum(lineup, "frontline") < 2.5:
                out.append(("敌方物理输出多，我方缺前排", 0.08))
            if _trait_sum(enemy, "push") >= 5.0 and _trait_sum(lineup, "push") < 2.0:
                out.append(("敌方推进强，我方清线不足", 0.08))

        # 4) 熟练度极低
        if games == 0 and draft.allies:
            out.append(("你没玩过这个英雄，纯靠版本/克制硬选", 0.12))
        elif prof < 0.35 and draft.allies:
            out.append(("熟练度偏低，需要手感局", 0.06))

        # 5) 分路安排不上
        if self._lane_deficit(hero, draft, lane) >= 1.0:
            out.append(("和我方已选位置冲突，排不下", 0.12))

        total = sum(p for _, p in out)
        if total > self.t.max_penalty:  # 截断，避免叠加把候选评分抹平
            scale = self.t.max_penalty / total
            out = [(k, v * scale) for k, v in out]
        return out

    # ------------------------------------------------------------- 主入口
    def rank(
        self,
        draft: Draft,
        candidates: Sequence[str],
        lane: Optional[int] = None,
        require_pool: bool = False,
    ) -> List[Candidate]:
        lane = lane if lane is not None else draft.next_lane()
        taken = set(draft.picked()) | set(draft.bans)
        need_keys = [k for k, v in self.team_needs(draft).items() if v > 0.25]
        results: List[Candidate] = []

        for name in candidates:
            hero = self.book.heroes.get(name)
            if hero is None or name in taken:
                continue
            st = self.pool.get(name)
            if require_pool and (st is None or st.games <= 0):
                continue

            prof, games, wr, prof_note = self.proficiency(name)
            match_s, match_r = self.matchup(name, draft, lane)
            fit_s, fit_r = self.team_fit(hero, draft, lane)
            meta_s = self.meta_score(name)
            syn_s, syn_r = self.synergy(name, draft)

            base = (
                self.w.proficiency * prof
                + self.w.matchup * match_s
                + self.w.team_need * fit_s
                + self.w.meta * meta_s
                + self.w.synergy * syn_s
            )
            # 早手（场上人少）时，摇摆位价值更高
            if len(draft.allies) + len(draft.enemies) <= 2:
                base += self.t.flexibility_bonus * (len(hero.lanes) / 5.0)
            base = _clamp(base)

            pens = self.penalties(hero, draft, lane, prof, games)
            mult = 1.0 - sum(p for _, p in pens)
            score = base * mult * 100.0

            reasons: List[str] = []
            if prof > 0.55:
                reasons.append(f"你擅长：{prof_note}")
            elif games == 0:
                reasons.append("未玩过（不在你的英雄池里）")
            reasons += match_r
            reasons += [f"阵容：{r}" for r in fit_r]
            reasons += syn_r
            if self.book.has_meta(name):
                reasons.append(f"版本胜率 {self.book.winrate(name):.1%}")
            else:
                reasons.append("无版本胜率数据（按中性处理）")

            risks = [f"{k} (-{v * 100:.0f}%)" for k, v in pens]

            results.append(
                Candidate(
                    hero=hero,
                    score=score,
                    breakdown={
                        "proficiency": prof,
                        "matchup": match_s,
                        "team_need": fit_s,
                        "meta": meta_s,
                        "synergy": syn_s,
                        "weighted_base": base,
                    },
                    penalties=pens,
                    reasons=reasons,
                    risks=risks,
                    position=lane,
                    games=games,
                    winrate=wr,
                )
            )

        results.sort(key=lambda c: (-c.score, c.hero.name))
        return results

    def recommend(
        self,
        draft: Draft,
        top_n: int = 8,
        lane: Optional[int] = None,
        pool_only: bool = True,
        min_games: int = 0,
    ) -> List[Candidate]:
        """给出一组推荐。

        pool_only=True 时只在「你打过的英雄」里选（默认，符合你说的「我擅长的」）；
        为 False 时在全英雄库里选，用于发现自己没想到的版本答案/克制位。
        """
        if pool_only:
            names = [h for h, s in self.pool.items() if s.games >= max(1, min_games) and h in self.book.heroes]
        else:
            names = self.book.names()
        return self.rank(draft, names, lane=lane, require_pool=pool_only)[:top_n]

    def explain(self, hero_name: str, draft: Draft, lane: Optional[int] = None) -> Optional[Candidate]:
        res = self.rank(draft, [hero_name], lane=lane)
        return res[0] if res else None

    def role_suggestion(self, draft: Draft) -> List[Tuple[int, str]]:
        """我方还需要什么位置，返回 [(位置, 说明)]，按推荐优先级排序。"""
        out: List[Tuple[int, str]] = []
        open_l = draft.open_lanes()
        # 先补核心，再补辅助
        for lane in sorted(open_l, key=lambda l: (l > 3, l)):
            tpl = ROLE_TEMPLATES.get(lane, {})
            out.append((lane, f"{tpl.get('label', lane)}：{tpl.get('note', '')}"))
        return out

    def counter_picks(self, draft: Draft, against: Optional[str] = None, top_n: int = 6) -> List[Candidate]:
        """专门找克制位：忽略熟练度，只看对位与阵容需求。"""
        targets = [against] if against else draft.enemy_heroes()
        cands = []
        for name in self.book.names():
            if name in set(draft.picked()) or name in set(draft.bans):
                continue
            if not targets:
                break
            avg = sum(self.book.matchup(name, t) for t in targets) / len(targets)
            if avg >= 0.02:
                cands.append((avg, name))
        cands.sort(reverse=True)
        names = [n for _, n in cands[: top_n * 4]]
        return self.rank(draft, names)[:top_n]


# --------------------------------------------------------------------------- 会话封装


@dataclass
class Assistant:
    """把书、引擎、草稿串起来的门面，UI 只跟它打交道。"""

    book: HeroBook
    engine: RecommendationEngine
    draft: Draft = field(default_factory=Draft)
    weights: Weights = field(default_factory=Weights)

    @classmethod
    def create(
        cls,
        book: Optional[HeroBook] = None,
        pool: Optional[Dict[str, PlayerHeroStat]] = None,
        weights: Optional[Weights] = None,
        tuning: Optional[Tuning] = None,
    ) -> "Assistant":
        b = book or HeroBook.load()
        w = weights or Weights()
        eng = RecommendationEngine(b, pool=pool, weights=w, tuning=tuning)
        return cls(book=b, engine=eng, draft=Draft(), weights=w)

    def set_weights(self, **kwargs: float) -> None:
        for k, v in kwargs.items():
            setattr(self.weights, k, float(v))
        self.engine.w = self.weights.normalized()

    def pool_from_records(self, records: Iterable[dict], resolve=("hero", "hero_name", "name")) -> int:
        """从 dict 列表导入英雄池，例如 [{'hero': 'Axe', 'games': 20, 'wins': 12}]。"""
        stats: Dict[str, PlayerHeroStat] = {}
        for rec in records:
            raw = None
            for key in resolve:
                if key in rec:
                    raw = rec[key]
                    break
            if not raw:
                continue
            hero = self.book.try_resolve(str(raw))
            if hero is None:
                continue
            g = int(rec.get("games", rec.get("matches", 0)) or 0)
            w = int(rec.get("wins", rec.get("win", 0)) or 0)
            old = stats.get(hero.name)
            if old:
                old.games += g
                old.wins += w
            else:
                stats[hero.name] = PlayerHeroStat(hero=hero.name, games=g, wins=min(w, g))
        self.engine.set_pool(stats.values())
        return len(stats)

    def set_pool_simple(self, mapping: Dict[str, Tuple[int, int]]) -> int:
        """{英雄: (局数, 胜负差)} 或 {英雄: 局数} 的简化写法。

        支持两种：``{"Axe": (20, 12)}``（局数, 胜场）与 ``{"Axe": 20}``（只有局数，
        胜率按 50% 记，并在 UI 里标注为「数据不全」）。
        """
        stats = []
        for raw_name, val in mapping.items():
            hero = self.book.try_resolve(str(raw_name))
            if hero is None:
                continue
            if isinstance(val, (tuple, list)):
                g, w = int(val[0]), int(val[1])
            else:
                g, w = int(val), int(round(int(val) * 0.5))
            stats.append(PlayerHeroStat(hero=hero.name, games=g, wins=min(w, g)))
        self.engine.set_pool(stats)
        return len(stats)
