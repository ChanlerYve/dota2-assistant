# -*- coding: utf-8 -*-
"""BP 阶段化权重的测试。

设计意图：第一手和最后一手是**两个不同的问题**。
* 早手（敌方几乎没亮）没有可针对的目标，对位因子基本是噪声，该优先拿版本强势、
  自己熟练、位置摇摆的英雄；
* 末手（敌方大多已亮）对位成为决定性因素——版本再强，被针对死也没用。

实现是对用户权重做**乘性调制**再归一化，不是替换，所以你的相对偏好仍然保留。
这些测试就是钉住这几条性质。

运行::

    python -m unittest tests.test_phase -v
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.data_loader import HeroBook  # noqa: E402
from d2a.draft import Draft  # noqa: E402
from d2a.engine import (  # noqa: E402
    PHASE_EARLY,
    PHASE_LATE,
    PHASE_MID,
    Assistant,
    RecommendationEngine,
    Weights,
)

BOOK = HeroBook.load()

# 一批用于构造"敌方已亮 N 人"的英雄
ENEMY_POOL = ["Juggernaut", "Zeus", "Lina", "Sniper", "Medusa", "Axe"]


def make_engine(**kw) -> RecommendationEngine:
    return RecommendationEngine(BOOK, pool=kw.pop("pool", None), **kw)


def draft_with(enemies: int = 0, allies: int = 0) -> Draft:
    d = Draft()
    for i in range(allies):
        d.add(ENEMY_POOL[i], "ally")
    for i in range(enemies):
        d.add(ENEMY_POOL[-(i + 1)], "enemy")
    return d


class TestPhaseDetection(unittest.TestCase):
    def test_phase_boundaries(self):
        """按敌方已亮人数划分：0~1 早手、2~3 中盘、>=4 末手。"""
        e = make_engine()
        cases = {0: PHASE_EARLY, 1: PHASE_EARLY, 2: PHASE_MID, 3: PHASE_MID,
                 4: PHASE_LATE, 5: PHASE_LATE}
        for n, expect in cases.items():
            with self.subTest(enemies=n):
                ph = e.draft_phase(draft_with(enemies=n))
                self.assertEqual(ph.name, expect, f"敌方 {n} 人应为 {expect}")

    def test_phase_depends_on_enemies_not_allies(self):
        """能不能针对，只取决于敌方亮了多少，与我自己选了几个无关。"""
        e = make_engine()
        ph_a = e.draft_phase(draft_with(enemies=0, allies=3))
        ph_b = e.draft_phase(draft_with(enemies=0, allies=0))
        self.assertEqual(ph_a.name, ph_b.name)
        self.assertEqual(ph_a.name, PHASE_EARLY)
        self.assertEqual(ph_a.my_pick_index, 4)
        self.assertEqual(ph_b.my_pick_index, 1)

    def test_phase_reports_counts(self):
        e = make_engine()
        d = draft_with(enemies=3, allies=2)
        ph = e.draft_phase(d)
        self.assertEqual(ph.enemy_revealed, 3)
        self.assertEqual(ph.picks_on_field, 5)
        self.assertEqual(ph.my_pick_index, 3)
        self.assertTrue(ph.note)


class TestWeightModulation(unittest.TestCase):
    def test_weights_stay_normalized(self):
        """调制后必须仍然是归一化的概率分布。"""
        e = make_engine()
        for n in range(6):
            with self.subTest(enemies=n):
                w = e.effective_weights(draft_with(enemies=n))
                total = w.proficiency + w.matchup + w.team_need + w.meta + w.synergy
                self.assertAlmostEqual(total, 1.0, places=9)

    def test_early_phase_downweights_matchup_upweights_meta(self):
        """早手：对位降权、版本升权。"""
        e = make_engine()
        base = e.w
        early = e.effective_weights(draft_with(enemies=0))
        self.assertLess(early.matchup, base.matchup, "早手应降低对位权重")
        self.assertGreater(early.meta, base.meta, "早手应提高版本权重")
        self.assertGreater(early.proficiency, base.proficiency, "早手应提高熟练度权重")

    def test_late_phase_upweights_matchup_downweights_meta(self):
        """末手：对位升权、版本降权。"""
        e = make_engine()
        base = e.w
        late = e.effective_weights(draft_with(enemies=5))
        self.assertGreater(late.matchup, base.matchup, "末手应提高对位权重")
        self.assertLess(late.meta, base.meta, "末手应降低版本权重")

    def test_matchup_weight_monotonic_across_phases(self):
        """对位权重应随阶段单调上升：早手 < 中盘 < 末手。"""
        e = make_engine()
        early = e.effective_weights(draft_with(enemies=0)).matchup
        mid = e.effective_weights(draft_with(enemies=2)).matchup
        late = e.effective_weights(draft_with(enemies=5)).matchup
        self.assertLess(early, mid)
        self.assertLess(mid, late)

    def test_meta_weight_monotonic_down(self):
        """版本权重应随阶段单调下降。"""
        e = make_engine()
        early = e.effective_weights(draft_with(enemies=0)).meta
        mid = e.effective_weights(draft_with(enemies=2)).meta
        late = e.effective_weights(draft_with(enemies=5)).meta
        self.assertGreater(early, mid)
        self.assertGreater(mid, late)

    def test_mid_phase_equals_user_weights(self):
        """中盘的乘数应基本为 1，即贴近用户设定。"""
        e = make_engine()
        base = e.w
        mid = e.effective_weights(draft_with(enemies=2))
        for attr in ("proficiency", "matchup", "meta"):
            with self.subTest(attr=attr):
                b, m = getattr(base, attr), getattr(mid, attr)
                self.assertLess(abs(m - b), 0.03, f"{attr} 中盘应与设定接近 ({b:.3f} vs {m:.3f})")

    def test_user_preference_is_preserved(self):
        """调制是乘性的，所以用户把某因子拉高，它在所有阶段都仍是最高。"""
        w = Weights(proficiency=0.60, matchup=0.10, team_need=0.10, meta=0.10, synergy=0.10)
        w = w.normalized()
        e = make_engine(weights=w)
        for n in range(6):
            with self.subTest(enemies=n):
                eff = e.effective_weights(draft_with(enemies=n))
                vals = {
                    "proficiency": eff.proficiency, "matchup": eff.matchup,
                    "team_need": eff.team_need, "meta": eff.meta, "synergy": eff.synergy,
                }
                top = max(vals, key=lambda k: vals[k])
                self.assertEqual(top, "proficiency",
                                 f"用户把熟练度拉最高，敌方 {n} 人时它仍应最高：{vals}")

    def test_disabled_uses_fixed_weights(self):
        """关掉阶段化后，生效权重应等于用户设定（全程一致）。"""
        e = make_engine()
        e.t.phase_weighting = False
        base = e.w
        for n in (0, 2, 5):
            with self.subTest(enemies=n):
                eff = e.effective_weights(draft_with(enemies=n))
                for attr in ("proficiency", "matchup", "team_need", "meta", "synergy"):
                    self.assertAlmostEqual(
                        getattr(eff, attr), getattr(base, attr), places=9,
                        msg=f"关闭后 {attr} 应与设定一致",
                    )

    def test_disabled_phase_is_neutral(self):
        e = make_engine()
        e.t.phase_weighting = False
        ph = e.draft_phase(draft_with(enemies=0))
        self.assertFalse(ph.active, "关闭时不该被标为「阶段生效」")
        self.assertTrue(all(abs(v - 1.0) < 1e-9 for v in ph.multipliers.values()))


class TestFlexibilityScaling(unittest.TestCase):
    def test_flexibility_multiplier_shrinks_over_phases(self):
        """摇摆位价值随阶段下降：末手位置已定，摇摆不再有意义。"""
        e = make_engine()
        early = e.draft_phase(draft_with(enemies=0)).flexibility
        mid = e.draft_phase(draft_with(enemies=2)).flexibility
        late = e.draft_phase(draft_with(enemies=5)).flexibility
        self.assertGreater(early, mid)
        self.assertGreater(mid, late)
        self.assertGreater(early, 0.0)
        self.assertLess(late, 0.3)


class TestRankingEffect(unittest.TestCase):
    """受控对比：固定 draft，只切阶段化开关，看排序是否按对位方向变化。

    这是验证「阶段化真的有用」的关键测试——只看权重数字不够，
    必须证明它改变了排序，而且方向正确。
    """

    def _engine(self):
        a = Assistant.create(book=BOOK)
        # 所有英雄同等熟练度，隔离出对位的影响
        a.set_pool_simple({h: (40, 22) for h in BOOK.names()})
        return a

    def _ranks(self, a, draft):
        cs = a.engine.recommend(draft, top_n=200, pool_only=True)
        return {c.hero.name: (i + 1, c.score) for i, c in enumerate(cs)}

    def test_late_phase_favours_good_matchups(self):
        target = "Medusa"
        # 真正克制 Medusa 的英雄：在**对方自己的行**里对 Medusa 的优势最高
        counters = [
            x for x, _ in sorted(
                ((x, BOOK.matchups.get(x, {}).get(target, 0.0))
                 for x in BOOK.names() if x != target),
                key=lambda kv: -kv[1],
            )[:4]
        ]
        d = Draft()
        for c in counters:
            d.add(c, "enemy")
        # 前提校验：这些确实克制她，且当前判定为末手
        for c in counters:
            self.assertLess(BOOK.matchup(target, c), 0, f"{c} 应当克制 {target}")
        a = self._engine()
        self.assertEqual(a.engine.draft_phase(d).name, PHASE_LATE)

        a.engine.t.phase_weighting = False
        off = self._ranks(a, d)
        a.engine.t.phase_weighting = True
        on = self._ranks(a, d)

        # 1) 被克制的目标分数应当下降
        self.assertLess(on[target][1], off[target][1],
                        f"{target} 被 4 个克星针对，末手分数应下降")
        # 2) 对这帮敌人对位最好的英雄应当更靠前
        taken = set(counters) | {target}
        best = max((h for h in BOOK.names() if h not in taken),
                   key=lambda h: sum(BOOK.matchup(h, c) for c in counters))
        self.assertLess(on[best][0], off[best][0],
                        f"{best} 克制这批敌人，末手应更靠前")
        # 3) 整体上排位应更受对位影响
        self.assertLess(self._corr(a, d, on), self._corr(a, d, off))

    def _corr(self, a, draft, ranks):
        """对位得分与最终排位名的相关系数（越负 = 对位影响越大）。"""
        xs, ys = [], []
        for h in BOOK.names():
            if h not in ranks:
                continue
            ms, _ = a.engine.matchup(h, draft, None)
            xs.append(ms)
            ys.append(ranks[h][0])
        n = len(xs)
        if n < 3:
            return 0.0
        mx, my = sum(xs) / n, sum(ys) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = sum((x - mx) ** 2 for x in xs) ** 0.5
        sy = sum((y - my) ** 2 for y in ys) ** 0.5
        return cov / (sx * sy) if sx * sy else 0.0

    def test_phase_effects_are_substantial(self):
        """阶段化应有可观测的实质影响，而不是小数点后的抖动。

        注意要用**末手**（敌方 >=4 人）来测：中盘的乘数刻意接近 1（中性），
        本来就不该有明显影响——这是设计意图，不是缺陷。
        """
        a = self._engine()
        d = Draft()
        for c in ["Doom", "Viper", "Ancient Apparition", "Abaddon", "Nyx Assassin"]:
            d.add(c, "enemy")
        self.assertEqual(a.engine.draft_phase(d).name, PHASE_LATE)

        a.engine.t.phase_weighting = False
        off = self._ranks(a, d)
        a.engine.t.phase_weighting = True
        on = self._ranks(a, d)
        moved = [abs(off[h][0] - on[h][0]) for h in on if h in off]
        self.assertTrue(moved)
        self.assertGreater(max(moved), 5, f"最大排位变化只有 {max(moved)} 名，影响过小")

    def test_mid_phase_has_little_effect(self):
        """中盘刻意保持中性：开关阶段化几乎不改变排序。"""
        a = self._engine()
        d = Draft()
        for c in ["Doom", "Viper", "Ancient Apparition"]:
            d.add(c, "enemy")
        self.assertEqual(a.engine.draft_phase(d).name, PHASE_MID)
        a.engine.t.phase_weighting = False
        off = self._ranks(a, d)
        a.engine.t.phase_weighting = True
        on = self._ranks(a, d)
        moved = [abs(off[h][0] - on[h][0]) for h in on if h in off]
        self.assertLessEqual(max(moved), 10, f"中盘不该有大幅变化（最大 {max(moved)} 名）")

    def test_flexibility_contribution_scales_with_phase(self):
        """摇摆位加分在早手远大于末手（末手位置已定，摇摆无意义）。"""
        a = self._engine()
        multi = max(BOOK.names(), key=lambda h: len(BOOK.heroes[h].lanes))
        self.assertGreaterEqual(len(BOOK.heroes[multi].lanes), 3, "需要一个多位置英雄")

        early = a.engine.explain(multi, Draft())
        late_draft = Draft()
        for c in ["Juggernaut", "Zeus", "Lina", "Sniper", "Medusa"]:
            late_draft.add(c, "enemy")
        a.engine.t.phase_weighting = True
        late = a.engine.explain(multi, late_draft)

        f_early = early.contribution.get("flexibility", 0.0)
        f_late = late.contribution.get("flexibility", 0.0)
        self.assertGreater(f_early, 0.0, "早手应给摇摆位加分")
        self.assertLess(f_late, f_early, "末手的摇摆位加分应更小")
        # 倍率 1.0 -> 0.15，所以大约衰减到 1/6
        self.assertAlmostEqual(f_late / f_early, 0.15, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
