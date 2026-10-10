# -*- coding: utf-8 -*-
"""队友视角（阵容补位建议）的测试。

此前引擎只能回答「我这一手选什么」，但 BP 是**和队友一起**做的——
你需要能告诉队友「你拿这个」。这个功能是同一套引擎的横向用法：
对每个空位用 ``lane=该位置`` + ``strict_lane`` 重新排序。

设计上要保证的性质：
* 只推荐**真能打这个位置**的英雄（不能给 5 号位推一个只能打 3 的英雄）；
* 已选/已 ban 的不能出现；
* 优先给「你自己能补」的答案，不够才补全英雄库；
* 候选要标明是不是你英雄池里的（「建议队友拿」≠「我能拿」）；
* 队伍短板只在整体层报一次，不在每个位置重复；
* 位置 1/2 不该被硬套上「缺口」标签（它们的职责不在可量化的缺口词汇里）。

运行::

    python -m unittest tests.test_team -v
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.data_loader import HeroBook  # noqa: E402
from d2a.draft import Draft  # noqa: E402
from d2a.engine import Assistant, TeamPlan, TeamSlot  # noqa: E402

BOOK = HeroBook.load()

POOL = {
    "Axe": (60, 37),
    "Crystal Maiden": (88, 50),
    "Lion": (52, 30),
    "Magnus": (44, 27),
    "Spirit Breaker": (47, 27),
}


def make_assistant(pool=None) -> Assistant:
    a = Assistant.create(book=BOOK)
    a.set_pool_simple(pool if pool is not None else POOL)
    return a


class TestTeamPlanShape(unittest.TestCase):
    def test_empty_draft_returns_all_five_lanes(self):
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft())
        self.assertIsInstance(plan, TeamPlan)
        self.assertEqual(plan.open_lanes, [1, 2, 3, 4, 5])
        self.assertEqual([s.lane for s in plan.slots], [1, 2, 3, 4, 5])

    def test_full_team_returns_no_slots(self):
        a = make_assistant()
        for h, l in (("Axe", 3), ("Crystal Maiden", 5), ("Magnus", 4),
                     ("Zeus", 2), ("Juggernaut", 1)):
            a.draft.add(h, "ally", l)
        plan = a.engine.team_suggestions(a.draft)
        self.assertEqual(plan.open_lanes, [])
        self.assertEqual(plan.slots, [])
        self.assertTrue(plan.note)

    def test_open_lanes_reflect_partial_picks(self):
        a = make_assistant()
        a.draft.add("Crystal Maiden", "ally", 5)
        plan = a.engine.team_suggestions(a.draft)
        self.assertEqual(plan.open_lanes, [1, 2, 3, 4])
        self.assertNotIn(5, [s.lane for s in plan.slots])

    def test_explicit_lanes_argument(self):
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft(), lanes=[2, 4])
        self.assertEqual(plan.open_lanes, [2, 4])
        self.assertEqual([s.lane for s in plan.slots], [2, 4])

    def test_slot_has_label_and_note(self):
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft(), lanes=[3])
        s = plan.slots[0]
        self.assertIsInstance(s, TeamSlot)
        self.assertTrue(s.label)
        self.assertTrue(s.note, "位置应有角色说明")
        self.assertTrue(s.candidates)

    def test_to_dict_is_json_serializable(self):
        import json

        a = make_assistant()
        plan = a.engine.team_suggestions(Draft())
        payload = json.loads(json.dumps(plan.to_dict(), ensure_ascii=False))
        self.assertIn("open_lanes", payload)
        self.assertIn("top_needs", payload)
        self.assertTrue(payload["slots"])
        self.assertTrue(payload["slots"][0]["candidates"])


class TestCandidateLegality(unittest.TestCase):
    def test_every_candidate_can_play_that_lane(self):
        """核心不变量：不能给某个位置推荐打不了它的英雄。"""
        a = make_assistant()
        a.draft.add("Doom", "enemy")
        plan = a.engine.team_suggestions(a.draft, per_lane=3)
        for s in plan.slots:
            for c in s.candidates:
                with self.subTest(lane=s.lane, hero=c.hero.name):
                    self.assertTrue(
                        c.hero.can_lane(s.lane),
                        f"{c.hero.name} 打不了 {s.lane} 号位（它只能打 {c.hero.lanes}）",
                    )

    def test_taken_and_banned_excluded(self):
        a = make_assistant()
        a.draft.add("Axe", "enemy")
        a.draft.add("Magnus", "ally", 4)
        a.draft.bans.append("Lion")
        plan = a.engine.team_suggestions(a.draft, per_lane=5)
        seen = {c.hero.name for s in plan.slots for c in s.candidates}
        for h in ("Axe", "Magnus", "Lion"):
            self.assertNotIn(h, seen, f"{h} 已被选/ban，不该出现在建议里")

    def test_per_lane_limits_count(self):
        a = make_assistant()
        for n in (1, 2, 4):
            with self.subTest(per_lane=n):
                plan = a.engine.team_suggestions(Draft(), per_lane=n)
                for s in plan.slots:
                    self.assertLessEqual(len(s.candidates), n)

    def test_no_duplicate_candidates_within_a_slot(self):
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft(), per_lane=5)
        for s in plan.slots:
            names = [c.hero.name for c in s.candidates]
            self.assertEqual(len(names), len(set(names)), f"{s.lane} 号位有重复候选")


class TestPoolPreference(unittest.TestCase):
    def test_pool_heroes_come_first(self):
        """优先给「你自己能补」的答案：池内英雄应排在池外之前。"""
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft(), per_lane=5)
        pool = set(a.engine.pool)
        for s in plan.slots:
            tags = [c.hero.name in pool for c in s.candidates]
            # 一旦出现池外，后面不该再有池内
            if False in tags:
                first_out = tags.index(False)
                self.assertNotIn(
                    True, tags[first_out:],
                    f"{s.lane} 号位池内/池外顺序不对: {tags}",
                )

    def test_empty_pool_still_gives_answers(self):
        """没有英雄池也要能给建议（只是全部标为池外）。"""
        a = make_assistant(pool={})
        plan = a.engine.team_suggestions(Draft(), per_lane=2)
        allc = [c for s in plan.slots for c in s.candidates]
        self.assertTrue(allc, "空英雄池时仍应从全英雄库给出建议")
        self.assertTrue(all(c.hero.name not in a.engine.pool for c in allc))

    def test_include_all_false_restricts_to_pool(self):
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft(), per_lane=5, include_all=False)
        pool = set(a.engine.pool)
        for s in plan.slots:
            for c in s.candidates:
                self.assertIn(c.hero.name, pool, "include_all=False 时只应给池内英雄")

    def test_empty_pool_with_include_all_false_is_empty(self):
        a = make_assistant(pool={})
        plan = a.engine.team_suggestions(Draft(), per_lane=3, include_all=False)
        self.assertTrue(plan.open_lanes)
        self.assertTrue(all(not s.candidates for s in plan.slots))


class TestTeamNeedsReporting(unittest.TestCase):
    def test_top_needs_present_and_sorted(self):
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft())
        self.assertTrue(plan.top_needs)
        vals = [v for _, v in plan.top_needs]
        self.assertEqual(vals, sorted(vals, reverse=True), "队伍缺口应降序")

    def test_needs_reported_once_not_per_slot(self):
        """队伍短板是全局信息，不该在每个位置上重复报——那是误导性冗余。"""
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft())
        # 缺口只挂在 plan 上；slot.gaps 只表达「该位置能补哪些」
        for s in plan.slots:
            for g in s.gaps:
                labels = [k for k, _ in plan.top_needs]
                self.assertIn(g, labels, f"{s.lane} 号位标了未在队伍缺口里的 {g}")

    def test_lane_1_and_2_have_no_gap_tags(self):
        """1/2 号位的职责（发育/节奏）不在缺口词汇里，不该被硬套标签。"""
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft())
        by_lane = {s.lane: s for s in plan.slots}
        self.assertEqual(by_lane[1].gaps, [], "1 号位不该有缺口标签")
        self.assertEqual(by_lane[2].gaps, [], "2 号位不该有缺口标签")

    def test_lane_3_and_4_get_gap_tags_on_empty_draft(self):
        """空 BP 时全队什么都缺，3/4 号位（前排/先手/硬控）应当标出来。"""
        a = make_assistant()
        plan = a.engine.team_suggestions(Draft())
        by_lane = {s.lane: s for s in plan.slots}
        self.assertTrue(by_lane[3].gaps, "3 号位应标出可补的短板")
        self.assertTrue(by_lane[4].gaps, "4 号位应标出可补的短板")

    def test_needs_shrink_after_filling(self):
        """补了前排之后，「前排」缺口应下降。"""
        a = make_assistant()
        before = dict(a.engine.team_suggestions(a.draft).top_needs)
        a.draft.add("Axe", "ally", 3)   # Axe 的 frontline 标签是 3，是典型前排
        # 注意必须传 a.draft（而不是新建 Draft()），否则测的是空 BP
        after = dict(a.engine.team_suggestions(a.draft).top_needs)
        b = before.get("前排", 0.0)
        af = after.get("前排", 0.0)
        self.assertLess(af, b, f"补了 Axe 后前排缺口应下降（{b:.2f} -> {af:.2f}）")


class TestAgainstEnemies(unittest.TestCase):
    def test_enemy_picks_change_scores(self):
        """敌方已亮时，至少应有候选的分数发生变化（说明对位被纳入）。"""
        a1 = make_assistant()
        a1.draft.add("Medusa", "enemy")
        a2 = make_assistant()

        def scores(a):
            plan = a.engine.team_suggestions(a.draft, per_lane=3)
            return {s.lane: {c.hero.name: round(c.score, 2) for c in s.candidates}
                    for s in plan.slots}

        s1, s2 = scores(a1), scores(a2)
        changed = [
            (lane, h) for lane in s1 for h in s1[lane]
            if s1[lane][h] != s2[lane].get(h)
        ]
        self.assertTrue(changed, f"敌方亮 Medusa 后应有候选分数变化：{s1} vs {s2}")

    def test_many_enemies_can_reorder(self):
        """敌方亮得多时（末手）排序本身应当可能改变——这才是对位起作用。

        只亮 1 个敌人时在早手，对位权重被刻意压低（×0.55），
        而且敌方位置还是猜的，所以排序不变是**预期行为**，不是缺陷。
        """
        a1 = make_assistant()
        for h in ("Doom", "Viper", "Ancient Apparition", "Abaddon"):
            a1.draft.add(h, "enemy")
        a2 = make_assistant()

        def order(a, lane=4):
            plan = a.engine.team_suggestions(a.draft, per_lane=3)
            for s in plan.slots:
                if s.lane == lane:
                    return [c.hero.name for c in s.candidates]
            return []

        self.assertNotEqual(
            order(a1), order(a2),
            "末手（4 个敌人）时 4 号位排序应随对位改变",
        )

    def test_matchup_reason_appears_when_enemy_known(self):
        a = make_assistant()
        a.draft.add("Medusa", "enemy")
        a.draft.add("Juggernaut", "enemy", 1)
        plan = a.engine.team_suggestions(a.draft, per_lane=3)
        reasons = [r for s in plan.slots for c in s.candidates for r in c.reasons]
        self.assertTrue(
            any("克制" in r or "对位" in r or "阵容" in r for r in reasons),
            f"敌方已亮时理由里应体现对位/阵容：{reasons[:4]}",
        )


class TestDeterminism(unittest.TestCase):
    def test_repeated_calls_identical(self):
        a = make_assistant()
        a.draft.add("Crystal Maiden", "ally", 5)
        a.draft.add("Doom", "enemy")
        p1 = a.engine.team_suggestions(a.draft, per_lane=3).to_dict()
        p2 = a.engine.team_suggestions(a.draft, per_lane=3).to_dict()
        self.assertEqual(p1, p2, "同一输入必须给出同一结果")

    def test_does_not_mutate_draft(self):
        a = make_assistant()
        a.draft.add("Crystal Maiden", "ally", 5)
        before = a.draft.to_dict()
        a.engine.team_suggestions(a.draft, per_lane=3)
        self.assertEqual(a.draft.to_dict(), before, "建议过程不该改动 BP 状态")


if __name__ == "__main__":
    unittest.main(verbosity=2)
