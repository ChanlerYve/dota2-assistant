# -*- coding: utf-8 -*-
"""单元测试：把「架构可验证」这件事落到实处。

运行::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.config import Config  # noqa: E402
from d2a.data_loader import (  # noqa: E402
    HeroBook,
    HeroNotFound,
    PlayerHeroStat,
    parse_bracket,
)
from d2a.draft import Draft  # noqa: E402
from d2a.engine import Assistant, RecommendationEngine, Weights  # noqa: E402
from d2a.steam_id import SteamIdError, account_id_to_steam64, parse_steam_input  # noqa: E402

BOOK: HeroBook


def setUpModule() -> None:
    global BOOK
    BOOK = HeroBook.load()


# --------------------------------------------------------------------------- 数据层
class TestDataLayer(unittest.TestCase):
    def test_heroes_loaded(self) -> None:
        self.assertGreaterEqual(len(BOOK.heroes), 120, "英雄库太小，数据可能没生成")

    def test_dataset_is_valid(self) -> None:
        problems = BOOK.validate()
        self.assertEqual(problems, [], f"数据集自检不通过: {problems[:5]}")

    def test_every_hero_has_lane_and_attrs(self) -> None:
        for name, h in BOOK.heroes.items():
            with self.subTest(hero=name):
                self.assertTrue(h.lanes, "必须声明可用分路")
                self.assertIn(h.attr, ("str", "agi", "int", "all"))
                self.assertTrue(0 <= h.trait("lane") <= 3)

    def test_matchup_edges_present(self) -> None:
        edges = sum(len(v) for v in BOOK.matchups.values())
        self.assertGreater(edges, 200, "克制矩阵太小，离线建议会退化")

    def test_resolve_names(self) -> None:
        cases = {
            "Axe": "Axe",
            "axe": "Axe",
            "斧王": "Axe",
            "AM": "Anti-Mage",
            "am": "Anti-Mage",
            "敌法师": "Anti-Mage",
            "crystal maiden": "Crystal Maiden",
            "cm": "Crystal Maiden",
            "撼地神牛": "Earthshaker",
            "白牛": "Spirit Breaker",
            "大牛": "Elder Titan",
            "虚空假面": "Faceless Void",
            "medusa": "Medusa",
        }
        for raw, expect in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(BOOK.resolve(raw).name, expect)

    def test_resolve_unknown(self) -> None:
        with self.assertRaises(HeroNotFound):
            BOOK.resolve("这不是英雄名字zzz")

    def test_matchup_symmetry(self) -> None:
        """克制关系必须反对称：A 对 B 的优势 = -(B 对 A 的优势)。"""
        checked = 0
        for a, row in BOOK.matchups.items():
            for b, v in row.items():
                if a in BOOK.matchups.get(b, {}):
                    self.assertAlmostEqual(BOOK.matchup(a, b), v, places=6)
                    checked += 1
        self.assertGreater(checked, 0, "应存在互指条目以便校验")

    def test_meta_optional_and_sane(self) -> None:
        for hero, rec in BOOK.meta.items():
            self.assertIn(hero, BOOK.heroes, f"版本数据含未知英雄 {hero}")
            wr = rec.get("winrate")
            if wr is not None:
                self.assertTrue(0.2 < wr < 0.8, f"{hero} 胜率异常: {wr}")


# --------------------------------------------------------------------------- Steam ID
class TestSteamId(unittest.TestCase):
    def test_forms(self) -> None:
        self.assertEqual(parse_steam_input("12345678"), 12345678)
        self.assertEqual(parse_steam_input(str(account_id_to_steam64(12345678))), 12345678)
        self.assertEqual(
            parse_steam_input("https://steamcommunity.com/profiles/" + str(account_id_to_steam64(12345678))),
            12345678,
        )
        # STEAM_0:1:6172839 -> account_id = 6172839 * 2 + 1
        self.assertEqual(parse_steam_input("STEAM_0:1:6172839"), 12345679)

    def test_roundtrip(self) -> None:
        self.assertEqual(parse_steam_input(str(account_id_to_steam64(42))), 42)

    def test_bad(self) -> None:
        for bad in ("", "abc", "0", "-5", "https://steamcommunity.com/id/someone"):
            with self.subTest(bad=bad):
                with self.assertRaises(SteamIdError):
                    parse_steam_input(bad)


# --------------------------------------------------------------------------- BP 状态机
class TestDraft(unittest.TestCase):
    def test_add_and_remove(self) -> None:
        d = Draft()
        d.add("Axe", "ally", 3)
        d.add("Juggernaut", "enemy")
        self.assertEqual(d.ally_heroes(), ["Axe"])
        self.assertEqual(d.enemy_heroes(), ["Juggernaut"])
        self.assertTrue(d.remove("Axe"))
        self.assertEqual(d.ally_heroes(), [])
        self.assertFalse(d.remove("Axe"))

    def test_no_double_pick(self) -> None:
        d = Draft()
        d.add("Axe", "ally")
        with self.assertRaises(ValueError):
            d.add("Axe", "enemy")

    def test_open_lanes_and_next(self) -> None:
        d = Draft()
        self.assertEqual(d.next_lane(), 1)
        d.add("Axe", "ally", 3)
        self.assertEqual(d.open_lanes(), [1, 2, 4, 5])
        self.assertEqual(d.next_lane(), 1, "未指定时优先补核心位")

    def test_enemy_lane_guess_is_legal(self) -> None:
        d = Draft()
        for h in ("Juggernaut", "Luna", "Magnus", "Crystal Maiden", "Lion"):
            d.add(h, "enemy")
        guess = d.guess_enemy_lanes(BOOK)
        self.assertEqual(len(guess), 5)
        for hero, lane in guess.items():
            with self.subTest(hero=hero):
                self.assertIn(lane, BOOK.heroes[hero].lanes, "推断位置必须是该英雄能打的")

    def test_serialization_roundtrip(self) -> None:
        d = Draft()
        d.add("Axe", "ally", 3)
        d.add("Luna", "enemy", 1)
        d.bans.append("Meepo")
        d.my_lane = 4
        d2 = Draft.from_dict(d.to_dict())
        self.assertEqual(d2.ally_heroes(), d.ally_heroes())
        self.assertEqual(d2.enemy_heroes(), d.enemy_heroes())
        self.assertEqual(d2.bans, d.bans)
        self.assertEqual(d2.my_lane, 4)
        self.assertEqual(d2.ally_lanes, {"Axe": 3})


# --------------------------------------------------------------------------- 引擎
def make_engine(**kw) -> RecommendationEngine:
    pool = kw.pop(
        "pool",
        {
            "Axe": PlayerHeroStat("Axe", 60, 37),
            "Magnus": PlayerHeroStat("Magnus", 44, 27),
            "Crystal Maiden": PlayerHeroStat("Crystal Maiden", 88, 50),
            "Lion": PlayerHeroStat("Lion", 52, 30),
            "Spirit Breaker": PlayerHeroStat("Spirit Breaker", 47, 27),
        },
    )
    return RecommendationEngine(BOOK, pool=pool, **kw)


class TestEngine(unittest.TestCase):
    def test_proficiency_shrinks_small_samples(self) -> None:
        """1 局 1 胜不该比 200 局 60% 更值钱。"""
        e = RecommendationEngine(
            BOOK,
            pool={
                "Axe": PlayerHeroStat("Axe", 200, 120),   # 60%
                "Luna": PlayerHeroStat("Luna", 1, 1),     # 100% 但只有 1 局
            },
        )
        axe = e.proficiency("Axe")[0]
        luna = e.proficiency("Luna")[0]
        self.assertGreater(axe, luna, "小样本必须被收缩")

    def test_no_pool_record_scores_zero(self) -> None:
        e = make_engine()
        score, games, wr, _ = e.proficiency("Meepo")
        self.assertEqual((score, games, wr), (0.0, 0, 0.0))

    def test_pool_only_excludes_unplayed(self) -> None:
        e = make_engine()
        d = Draft()
        cands = e.recommend(d, top_n=20, pool_only=True)
        names = {c.hero.name for c in cands}
        self.assertTrue(names <= set(e.pool))
        self.assertGreater(len(cands), 0)

    def test_counter_factor_uses_real_data(self) -> None:
        """从数据集中挑一个真实存在的强克制关系，验证对位因子与理由都生效。

        注意：不能写死「AM 克美杜莎」这类印象——在线刷新会用真实对局数据覆盖
        人工种子，真实的克制关系必须以数据为准，所以测试也按数据来。
        """
        picked = None
        for a, row in BOOK.matchups.items():
            for b, v in row.items():
                if v >= 0.04:
                    picked = (a, b, v)
                    break
            if picked:
                break
        self.assertIsNotNone(picked, "数据集里应存在明显克制关系")
        a, b, v = picked

        d = Draft()
        d.add(b, "enemy", 1)
        e = make_engine()
        c = e.explain(a, d, lane=1)
        self.assertIsNotNone(c)
        self.assertGreater(c.breakdown["matchup"], 0.5, f"{a} 打 {b} 应拿到对位优势 (数据 {v})")
        self.assertTrue(any("克制" in r for r in c.reasons), "理由里应出现克制说明")

    def test_counter_pick_respects_lane_weight(self) -> None:
        """同路敌人的权重应远高于其他敌人（对线期崩盘是最常见的输法）。"""
        e = make_engine()
        d = Draft()
        d.add("Sniper", "enemy", 1)
        d.add("Axe", "enemy", 3)
        w1 = e._enemy_lane_weights(d, 1)
        w5 = e._enemy_lane_weights(d, 5)
        self.assertGreater(w1["Sniper"], w1["Axe"], "打1号位时，敌方1号位应是主要对位目标")
        self.assertEqual(w1["Sniper"], 1.0)
        self.assertEqual(w5["Sniper"], e.t.enemy_pickrate_prior)

    def test_team_need_reacts_to_missing_initiation(self) -> None:
        e = make_engine()
        empty = e.team_needs(Draft())
        self.assertGreater(empty["init"], 0.5, "空阵容应显示先手缺口")
        d = Draft()
        d.add("Axe", "ally", 3)
        d.add("Magnus", "ally", 4)
        after = e.team_needs(d)
        self.assertLess(after["init"], empty["init"], "补了先手后缺口应下降")

    def test_penalties_for_all_magic_lineup(self) -> None:
        """我方已选三人都是魔法伤害时，再补一个魔法英雄应被提示魔抗风险。"""
        d = Draft()
        for h in ("Zeus", "Lina", "Lion"):
            d.add(h, "ally")
        e = make_engine()
        c = e.explain("Crystal Maiden", d)
        self.assertIsNotNone(c, "英雄池里有 CM，应该能给出解释")
        self.assertTrue(any("魔法" in k for k, _ in c.penalties), f"应有魔法伤害过载提示: {c.penalties}")

    def test_penalty_bypasses_pool_with_all_view(self) -> None:
        """全英雄视角下，不在英雄池里的英雄也要能解释（不能返回 None）。"""
        d = Draft()
        d.add("Zeus", "ally")
        e = make_engine()
        c = e.rank(d, ["Meepo"])[0]
        self.assertEqual(c.games, 0)
        self.assertTrue(any("没玩过" in r or "未玩过" in r for r in c.reasons + c.risks))

    def test_penalty_cap(self) -> None:
        """惩罚不能把候选打到 0 分：上限保护。"""
        d = Draft()
        for h in ("Juggernaut", "Luna", "Sniper", "Medusa"):
            d.add(h, "enemy")
        e = make_engine()
        cands = e.recommend(d, top_n=10, pool_only=True)
        for c in cands:
            if c.penalties:
                total = sum(p for _, p in c.penalties)
                self.assertLessEqual(round(total, 6), e.t.max_penalty + 1e-9)

    def test_taken_heroes_not_recommended(self) -> None:
        d = Draft()
        d.add("Axe", "ally", 3)
        d.add("Magnus", "enemy")
        d.bans.append("Lion")
        e = make_engine()
        names = {c.hero.name for c in e.recommend(d, top_n=30, pool_only=False)}
        for h in ("Axe", "Magnus", "Lion"):
            self.assertNotIn(h, names)

    def test_weights_normalized(self) -> None:
        w = Weights(1, 1, 1, 1, 1).normalized()
        self.assertAlmostEqual(w.proficiency + w.matchup + w.team_need + w.meta + w.synergy, 1.0, places=9)

    def test_higher_proficiency_weight_prefers_familiar(self) -> None:
        """把熟练度权重拉满，第一名应该换成你数据更好的英雄。"""
        pool = {
            "Crystal Maiden": PlayerHeroStat("Crystal Maiden", 300, 200),  # 67%
            "Axe": PlayerHeroStat("Axe", 300, 130),                        # 43%
        }
        d = Draft()
        e1 = RecommendationEngine(BOOK, pool=pool, weights=Weights(0.9, 0.025, 0.025, 0.025, 0.025))
        top1 = e1.recommend(d, top_n=1, pool_only=True)[0].hero.name
        e2 = RecommendationEngine(BOOK, pool=pool, weights=Weights(0.05, 0.4, 0.4, 0.1, 0.05))
        top2 = e2.recommend(d, top_n=1, pool_only=True)[0].hero.name
        self.assertEqual(top1, "Crystal Maiden")
        self.assertNotEqual(top1 + top2, "", "两次推荐都应给出结果")

    def test_deterministic(self) -> None:
        d = Draft()
        d.add("Juggernaut", "enemy")
        e = make_engine()
        a = [(c.hero.name, round(c.score, 6)) for c in e.recommend(d, top_n=8, pool_only=False)]
        b = [(c.hero.name, round(c.score, 6)) for c in e.recommend(d, top_n=8, pool_only=False)]
        self.assertEqual(a, b, "引擎必须可复现，不能有随机性")

    def test_explain_returns_breakdown(self) -> None:
        d = Draft()
        d.add("Luna", "enemy", 1)
        e = make_engine()
        c = e.explain("Spirit Breaker", d, lane=4)
        self.assertIsNotNone(c)
        for k in ("proficiency", "matchup", "team_need", "meta", "synergy", "weighted_base"):
            self.assertIn(k, c.breakdown)
        self.assertGreaterEqual(c.score, 0.0)
        self.assertLessEqual(c.score, 100.0)
        self.assertIn(c.stars, (1, 2, 3, 4, 5))

    def test_counter_picks_target(self) -> None:
        d = Draft()
        d.add("Medusa", "enemy", 1)
        e = make_engine()
        cands = e.counter_picks(d, against="Medusa", top_n=5)
        self.assertTrue(cands)
        for c in cands:
            self.assertGreater(BOOK.matchup(c.hero.name, "Medusa"), 0, f"{c.hero.name} 不该出现在克制位")

    def test_role_suggestion(self) -> None:
        d = Draft()
        d.add("Axe", "ally", 3)
        e = make_engine()
        lanes = [l for l, _ in e.role_suggestion(d)]
        self.assertNotIn(3, lanes)
        self.assertIn(1, lanes)


# --------------------------------------------------------------------------- 会话门面
class TestAssistant(unittest.TestCase):
    def test_pool_import_shapes(self) -> None:
        a = Assistant.create(book=BOOK)
        n = a.pool_from_records(
            [
                {"hero": "Axe", "games": 10, "wins": 6},
                {"hero_name": "斧王", "games": 5, "wins": 2},
                {"name": "unknown hero xyz", "games": 3, "wins": 1},
            ]
        )
        self.assertEqual(n, 1, "同名英雄应合并，未知英雄应忽略")
        self.assertEqual(a.engine.pool["Axe"].games, 15)
        self.assertEqual(a.engine.pool["Axe"].wins, 8)

    def test_set_pool_simple_forms(self) -> None:
        a = Assistant.create(book=BOOK)
        n = a.set_pool_simple({"斧王": (20, 12), "Medusa": 10})
        self.assertEqual(n, 2)
        self.assertEqual(a.engine.pool["Axe"].wins, 12)
        self.assertEqual(a.engine.pool["Medusa"].wins, 5, "只给局数时按 50% 保守处理")

    def test_weights_applied(self) -> None:
        a = Assistant.create(book=BOOK)
        a.set_weights(proficiency=0.5)
        self.assertAlmostEqual(a.engine.w.proficiency, 0.5 / (0.5 + 0.22 + 0.22 + 0.14 + 0.10), places=6)


# --------------------------------------------------------------------------- 配置
class TestConfig(unittest.TestCase):
    def test_defaults_and_roundtrip(self) -> None:
        import tempfile

        c = Config()
        self.assertIn("proficiency", c.weights)
        payload = {"pool": {"Axe": [10, 6]}, "weights": {"meta": 0.3}, "overlay": {"alpha": 0.8}}
        c2 = Config.from_dict(payload)
        self.assertEqual(c2.pool["Axe"], [10, 6])
        self.assertAlmostEqual(c2.weights["meta"], 0.3)
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.json"
            c2.save(p)
            c3 = Config.load(p)
            self.assertEqual(c3.pool, c2.pool)
            self.assertAlmostEqual(c3.weights["meta"], 0.3)
            opts = c3.overlay_options()
            self.assertAlmostEqual(opts["alpha"], 0.8)
            self.assertIn("width", opts)

    def test_example_config_is_valid(self) -> None:
        p = ROOT / "config.example.json"
        self.assertTrue(p.exists(), "应当提供 config.example.json")
        raw = json.loads(p.read_text(encoding="utf-8"))
        c = Config.from_dict(raw)
        self.assertTrue(c.pool)
        a = Assistant.create(book=BOOK)
        n = a.set_pool_simple(c.pool)  # type: ignore[arg-type]
        self.assertGreater(n, 0, "样例配置里的英雄名必须能解析出来")


# --------------------------------------------------------------------------- 缺陷回归
class TestReviewRegressions(unittest.TestCase):
    """review 报告里实测确认过的缺陷，逐条钉死，防止回归。"""

    # --- D2：空 BP 时位置适配必须生效（曾返回常数，第一手推荐不含位置信息）
    def test_position_fit_applies_on_empty_draft(self) -> None:
        e = make_engine()
        d = Draft()
        axe_1 = e.explain("Axe", d, lane=1)
        axe_3 = e.explain("Axe", d, lane=3)   # Axe 只能打 3
        self.assertIsNotNone(axe_1)
        self.assertIsNotNone(axe_3)
        self.assertGreater(axe_3.score, axe_1.score, "空 BP 下合法位置必须比非法位置得分高")
        self.assertNotEqual(axe_1.breakdown["team_need"], axe_3.breakdown["team_need"])

    def test_empty_draft_team_fit_is_pure_position(self) -> None:
        """无队友时 team_fit 应当就等于位置适配，而不是被常数 0.6 稀释。"""
        e = make_engine()
        hero = BOOK.heroes["Axe"]
        fit_3, _ = e.team_fit(hero, Draft(), 3)
        self.assertAlmostEqual(fit_3, e._position_fit(hero, 3), places=9)

    # --- D8：打不了目标位置必须有显式惩罚，且 position_legal 为 False
    def test_illegal_lane_gets_explicit_penalty(self) -> None:
        e = make_engine()
        c = e.explain("Axe", Draft(), lane=5)
        self.assertIsNotNone(c)
        self.assertFalse(c.position_legal, "Axe 打不了 5 号位，position_legal 应为 False")
        self.assertTrue(
            any("打不了" in k for k, _ in c.penalties),
            f"应有「打不了这个位置」的显式惩罚: {c.penalties}",
        )

    def test_legal_lane_is_flagged_legal(self) -> None:
        e = make_engine()
        c = e.explain("Axe", Draft(), lane=3)
        self.assertTrue(c.position_legal)
        self.assertFalse(any("打不了" in k for k, _ in c.penalties))

    def test_strict_lane_filters_illegal_heroes(self) -> None:
        e = make_engine()
        strict = e.recommend(Draft(), top_n=10, lane=5, strict_lane=True)
        self.assertTrue(strict, "5 号位应当有可推荐的英雄")
        for c in strict:
            self.assertTrue(BOOK.heroes[c.hero.name].can_lane(5), f"{c.hero.name} 不该出现在 5 号位推荐里")

    def test_explain_still_works_for_illegal_lane(self) -> None:
        """默认不过滤：解释一个打不了该位置的英雄也要有结果（否则 UI 会空白）。"""
        e = make_engine()
        self.assertIsNotNone(e.explain("Axe", Draft(), lane=5))

    # --- D5：min_games 在全英雄视角也必须生效
    def test_min_games_applies_in_all_view(self) -> None:
        e = RecommendationEngine(
            BOOK,
            pool={
                "Axe": PlayerHeroStat("Axe", 200, 120),
                "Lion": PlayerHeroStat("Lion", 60, 30),
                "Zeus": PlayerHeroStat("Zeus", 1, 1),
            },
        )
        d = Draft()
        d.add("Juggernaut", "enemy", 1)
        names = {c.hero.name for c in e.recommend(d, top_n=50, pool_only=False, min_games=50)}
        self.assertIn("Axe", names)
        self.assertIn("Lion", names)
        self.assertNotIn("Zeus", names, "只有 1 局的英雄应被 min_games=50 过滤掉")

    def test_never_played_penalty_without_allies(self) -> None:
        """「没玩过」的惩罚不该只在有队友时才生效。"""
        e = make_engine()
        c = e.rank(Draft(), ["Meepo"])[0]
        self.assertEqual(c.games, 0)
        self.assertTrue(
            any("没玩过" in k or "未玩过" in k for k, _ in c.penalties),
            f"空 BP 下也应有「没玩过」惩罚: {c.penalties}",
        )

    # --- D4：协同无数据时不能塞进一个常数，权重应让给其他因子
    def test_synergy_absent_is_excluded_from_contribution(self) -> None:
        e = make_engine()
        d = Draft()
        d.add("Crystal Maiden", "ally", 5)
        c = e.explain("Axe", d, lane=3)
        self.assertNotIn("synergy", c.contribution, "无协同数据时不应把该因子的权重算进去")
        # breakdown 里仍要有可显示的数值，避免前端拿到 None
        self.assertIsInstance(c.breakdown["synergy"], float)

    def test_contribution_sums_to_weighted_base(self) -> None:
        """各因子贡献之和应等于加权总分（惩罚前），否则「这条理由值几分」会骗人。"""
        e = make_engine()
        d = Draft()
        d.add("Juggernaut", "enemy", 1)
        d.add("Crystal Maiden", "ally", 5)
        c = e.explain("Axe", d, lane=3)
        self.assertAlmostEqual(sum(c.contribution.values()), c.breakdown["weighted_base"] * 100.0, places=4)

    def test_reasons_sorted_by_impact(self) -> None:
        """理由必须按影响力降序，而不是代码书写顺序。"""
        e = make_engine()
        d = Draft()
        d.add("Juggernaut", "enemy", 1)
        c = e.explain("Axe", d, lane=3)
        # 第一条应当是熟练度（权重最高且分数高）
        self.assertTrue(c.reasons[0].startswith("你擅长"), f"第一条理由: {c.reasons[0]}")

    # --- D5/D8：preferred_lane 推断
    def test_preferred_lane_support_pool(self) -> None:
        e = RecommendationEngine(
            BOOK,
            pool={
                "Crystal Maiden": PlayerHeroStat("Crystal Maiden", 88, 50),
                "Lion": PlayerHeroStat("Lion", 52, 30),
                "Witch Doctor": PlayerHeroStat("Witch Doctor", 40, 20),
            },
        )
        self.assertEqual(e.preferred_lane(), 5, "辅助池应当推断出 5 号位")

    def test_preferred_lane_core_pool(self) -> None:
        e = RecommendationEngine(
            BOOK,
            pool={
                "Axe": PlayerHeroStat("Axe", 60, 37),
                "Juggernaut": PlayerHeroStat("Juggernaut", 50, 28),
                "Sniper": PlayerHeroStat("Sniper", 40, 22),
            },
        )
        self.assertIn(e.preferred_lane(), (1, 2, 3), "核心池不该推断成辅助位")

    def test_preferred_lane_empty_pool(self) -> None:
        e = RecommendationEngine(BOOK, pool={})
        self.assertIsNone(e.preferred_lane())

    # --- D7：位置推断必须是「合法且尽量唯一」的全局分配
    def test_enemy_lane_guess_is_a_valid_assignment(self) -> None:
        cases = [
            ["Juggernaut", "Luna", "Magnus", "Crystal Maiden", "Lion"],
            ["Medusa", "Puck", "Tidehunter", "Rubick", "Disruptor"],
            ["Anti-Mage", "Storm Spirit", "Bristleback", "Lion", "Crystal Maiden"],
            ["Spectre", "Invoker", "Sand King", "Earthshaker", "Warlock"],
        ]
        import itertools

        for heroes in cases:
            with self.subTest(heroes=heroes):
                d = Draft()
                for h in heroes:
                    d.add(h, "enemy")
                guess = d.guess_enemy_lanes(BOOK)
                # 1) 每个位置都必须是该英雄合法的位置
                for h in heroes:
                    self.assertIn(guess[h], BOOK.heroes[h].lanes, f"{h} 被推断到非法位置 {guess[h]}")
                # 2) 如果理论上存在不重复的分配，就应该给出不重复的
                lanes = [list(BOOK.heroes[h].lanes) for h in heroes]
                feasible = any(len(set(p)) == len(heroes) for p in itertools.product(*lanes))
                if feasible:
                    self.assertEqual(
                        len(set(guess.values())), len(heroes),
                        f"{heroes} 理论上可唯一分配，实际得到 {guess}",
                    )

    def test_enemy_lane_guess_uses_tags_not_just_order(self) -> None:
        """摇摆位应按英雄本身倾向判断，而不是死板按第几手。"""
        d = Draft()
        d.add("Juggernaut", "enemy")
        d.add("Crystal Maiden", "enemy")
        guess = d.guess_enemy_lanes(BOOK)
        self.assertEqual(guess["Juggernaut"], 1, "先手拿的 Juggernaut 应是核心位")
        self.assertIn(guess["Crystal Maiden"], (4, 5), "后手拿的辅助应在 4/5 号位")

    def test_manual_lane_beats_guess(self) -> None:
        d = Draft()
        d.add("Juggernaut", "enemy")
        d.add("Luna", "enemy")
        d.set_lane("Juggernaut", 4)   # Juggernaut 可打 1/4
        guess = d.guess_enemy_lanes(BOOK)
        self.assertEqual(guess["Juggernaut"], 4, "手动指定的位置必须优先")

    # --- D1/D3：数据层不能有零对位英雄
    def test_no_hero_has_zero_matchups(self) -> None:
        zero = [h for h in BOOK.names() if not BOOK.matchups.get(h)]
        self.assertEqual(zero, [], f"这些英雄完全没有对位数据，对位因子会永久中性: {zero}")

    def test_matchup_matrix_is_wide_enough(self) -> None:
        """曾经被 top_k=14 截断成每英雄最多 14 条，明显偏袒热门对手。"""
        counts = [len(v) for v in BOOK.matchups.values()]
        self.assertGreater(sum(counts), 3000, f"对位边只有 {sum(counts)} 条，疑似又被截断")
        self.assertGreater(max(counts), 20, f"单个英雄最多只有 {max(counts)} 条对位，疑似被截断")

    # --- D6：版本号不能是占位符
    def test_patch_is_not_placeholder(self) -> None:
        self.assertNotIn(
            BOOK.meta_patch, ("", "current", "unknown"),
            "meta.json 的 patch 必须是真实版本号（如 7.41），否则无法判断数据是否过期",
        )


# --------------------------------------------------------------------------- 第二梯队
class TestBracketSlicing(unittest.TestCase):
    """天梯档位切片（高分段 / 低分段的不同版本答案）。"""

    def test_parse_bracket_forms(self) -> None:
        cases = {
            "divine": 7, "DIVINE": 7, "Divine": 7, 7: 7, "7": 7,
            "immortal": 8, "herald": 1, "超凡": 7, "冠绝": 8,
            "": None, None: None, "all": None, "xxx": None, 0: None, 9: None,
        }
        for raw, expect in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_bracket(raw), expect)

    def test_meta_has_bracket_data(self) -> None:
        """meta.json 必须带分档位样本，否则切片是空的。"""
        with_brackets = [h for h in BOOK.names() if (BOOK.meta.get(h) or {}).get("brackets")]
        self.assertGreater(len(with_brackets), 100, f"只有 {len(with_brackets)} 个英雄有分档位数据")

    def test_bracket_winrate_reasonable(self) -> None:
        for b in (1, 4, 7):
            wr = BOOK.bracket_winrate("Axe", b)
            self.assertIsNotNone(wr, f"Axe 在档位 {b} 应该有数据")
            self.assertTrue(0.3 < wr < 0.7, f"档位 {b} 胜率异常: {wr}")
            self.assertGreater(BOOK.bracket_picks("Axe", b), 0)

    def test_bracket_winrate_missing_returns_none(self) -> None:
        """没有该档位数据时要返回 None，而不是编一个数字。"""
        self.assertIsNone(BOOK.bracket_winrate("Axe", None))
        self.assertIsNone(BOOK.bracket_winrate("Axe", 99))

    def test_bracket_label(self) -> None:
        self.assertEqual(BOOK.bracket_label(7), "Divine")
        self.assertEqual(BOOK.bracket_label(None), "全体")

    def test_set_bracket_affects_meta_score(self) -> None:
        """切换档位应当真的改变 meta 因子（不同分段版本答案不同）。"""
        e = RecommendationEngine(BOOK, pool={"Axe": PlayerHeroStat("Axe", 60, 37)})
        e.set_bracket(None)
        all_score = e.meta_score("Axe")
        e.set_bracket(7)
        div_score = e.meta_score("Axe")
        self.assertNotAlmostEqual(all_score, div_score, places=6, msg="切片没有生效")
        self.assertEqual(e.bracket, 7)

    def test_meta_note_names_the_bracket(self) -> None:
        e = RecommendationEngine(BOOK)
        e.set_bracket(7)
        self.assertIn("Divine", e.meta_note("Axe"))
        e.set_bracket(None)
        self.assertIn("版本胜率", e.meta_note("Axe"))

    def test_bracket_slicing_can_change_ranking(self) -> None:
        """全体平均与高分段应当存在可观测的排序差异（否则切片无意义）。"""
        all_top = sorted(BOOK.names(), key=lambda h: -BOOK.winrate(h))[:15]
        div_top = sorted(
            [h for h in BOOK.names() if BOOK.bracket_winrate(h, 7)],
            key=lambda h: -(BOOK.bracket_winrate(h, 7) or 0),
        )[:15]
        overlap = len(set(all_top) & set(div_top))
        self.assertLess(overlap, 15, "全体与 Divine 段的前 15 完全一致，说明切片没有区分度")


class TestLaneProficiency(unittest.TestCase):
    """分位置熟练度：「你打 3 号位的斧王」≠「你玩斧王」。"""

    def test_player_hero_stat_lane_helpers(self) -> None:
        st = PlayerHeroStat("Axe", 10, 6)
        st.add_lane_result(3, True)
        st.add_lane_result(3, False)
        st.add_lane_result(3, True)
        st.add_lane_result(4, False)
        self.assertEqual(st.lane_games(3), 3)
        self.assertEqual(st.lane_wins(3), 2)
        self.assertAlmostEqual(st.lane_winrate(3) or 0, 2 / 3)
        self.assertEqual(st.lane_games(2), 0)
        self.assertIsNone(st.lane_winrate(2))

    def test_lane_roundtrip_json(self) -> None:
        st = PlayerHeroStat("Axe", 10, 6)
        st.add_lane_result(3, True)
        st.add_lane_result(5, False)
        st.last_match_id = 12345
        payload = json.loads(json.dumps(st.to_dict()))  # 过一遍 JSON，模拟落盘
        back = PlayerHeroStat.from_dict(payload)
        self.assertEqual(back.by_lane, {3: [1, 1], 5: [1, 0]})
        self.assertEqual(back.last_match_id, 12345)
        self.assertEqual(back.games, 10)

    def test_proficiency_uses_lane_when_available(self) -> None:
        """同一英雄在两个位置表现相反时，熟练度必须跟着位置走。"""
        st = PlayerHeroStat("Magnus", 80, 50)   # 总体 62.5%
        for _ in range(20):
            st.add_lane_result(3, True)          # 3 号位全胜
        for _ in range(20):
            st.add_lane_result(4, False)         # 4 号位全败
        e = RecommendationEngine(BOOK, pool={"Magnus": st})
        s3, _, _, n3 = e.proficiency("Magnus", 3)
        s4, _, _, n4 = e.proficiency("Magnus", 4)
        self.assertGreater(s3, s4, f"3 号位({s3}) 应明显高于 4 号位({s4})")
        self.assertGreater(s3 - s4, 0.5, "位置差异应当很显著")
        self.assertIn("3 号位", n3)
        self.assertIn("4 号位", n4)

    def test_proficiency_falls_back_without_lane_data(self) -> None:
        st = PlayerHeroStat("Axe", 60, 37)
        e = RecommendationEngine(BOOK, pool={"Axe": st})
        with_lane, _, _, note = e.proficiency("Axe", 3)
        without, _, _, _ = e.proficiency("Axe", None)
        self.assertAlmostEqual(with_lane, without, places=9, msg="没有位置数据时应与总体一致")
        self.assertIn("无独立记录", note)

    def test_proficiency_none_still_zero(self) -> None:
        e = RecommendationEngine(BOOK, pool={})
        self.assertEqual(e.proficiency("Meepo", 3)[0], 0.0)

    def test_pool_from_stats_keeps_lane_data(self) -> None:
        a = Assistant.create(book=BOOK)
        n = a.pool_from_stats(
            {
                "斧王": {"games": 20, "wins": 12, "by_lane": {"3": [10, 7]}, "last_match_id": 999},
                "不存在的英雄": {"games": 5, "wins": 1},
            }
        )
        self.assertEqual(n, 1, "未知英雄应被忽略")
        st = a.engine.pool["Axe"]
        self.assertEqual(st.by_lane, {3: [10, 7]})
        self.assertEqual(st.last_match_id, 999)

    def test_rank_uses_lane_in_reason(self) -> None:
        """带位置数据时，推荐理由要说明是哪个位置的战绩。"""
        st = PlayerHeroStat("Axe", 60, 37)
        for _ in range(10):
            st.add_lane_result(3, True)
        a = Assistant.create(book=BOOK)
        a.engine.set_pool([st])
        a.draft.my_lane = 3
        c = a.engine.explain("Axe", a.draft, lane=3)
        self.assertTrue(any("3 号位" in r for r in c.reasons), f"理由里应提到位置: {c.reasons}")


class TestPositionExtraction(unittest.TestCase):
    """赛后复盘闭环的取数逻辑（用真实 OpenDota 响应的形状做纯函数测试）。"""

    def _match(self, **over):
        p = {
            "account_id": 42,
            "hero_id": 2,
            "win": 1,
            "isRadiant": True,
            "position_est": 3,
            "lane_role": 3,
        }
        p.update(over)
        return {"players": [{"account_id": 1, "hero_id": 9}, p], "radiant_win": True}

    def test_extracts_hero_win_position(self) -> None:
        import importlib.util
        import pathlib as _p

        spec = importlib.util.spec_from_file_location(
            "sync_results", _p.Path(ROOT) / "tools" / "sync_results.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        hid, win, pos, lane_role = mod.position_of(self._match(), 42)
        self.assertEqual(hid, 2)
        self.assertTrue(win)
        self.assertEqual(pos, 3)
        self.assertEqual(lane_role, 3)

    def test_position_none_for_unparsed(self) -> None:
        import importlib.util
        import pathlib as _p

        spec = importlib.util.spec_from_file_location(
            "sync_results2", _p.Path(ROOT) / "tools" / "sync_results.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _, _, pos, _ = mod.position_of(self._match(position_est=None), 42)
        self.assertIsNone(pos, "未解析的对局没有位置，不能编一个")

    def test_missing_player_returns_none(self) -> None:
        import importlib.util
        import pathlib as _p

        spec = importlib.util.spec_from_file_location(
            "sync_results3", _p.Path(ROOT) / "tools" / "sync_results.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        hid, win, pos, lane_role = mod.position_of({"players": []}, 42)
        self.assertIsNone(hid)
        self.assertFalse(win)
        self.assertIsNone(pos)


class TestConfigRichPool(unittest.TestCase):
    """配置要能往返「完整记录池」（带 by_lane / 水位）。"""

    def test_rich_pool_roundtrip(self) -> None:
        import tempfile

        c = Config()
        c.pool = {"Lion": [10, 6]}
        c.pool_records = {"Axe": {"games": 20, "wins": 12, "by_lane": {"3": [8, 5]}, "last_match_id": 777}}
        c.bracket = "7"
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.json"
            c.save(p)
            raw = json.loads(p.read_text(encoding="utf-8"))
            # 两种写法合并进同一个 "pool" 键
            self.assertIn("Axe", raw["pool"])
            self.assertIn("Lion", raw["pool"])
            self.assertIsInstance(raw["pool"]["Axe"], dict)
            self.assertEqual(raw["bracket"], "7")
            back = Config.load(p)
            self.assertEqual(back.pool_records["Axe"]["by_lane"], {"3": [8, 5]})
            self.assertEqual(back.pool["Lion"], [10, 6])
            self.assertEqual(back.bracket, "7")

    def test_legacy_short_pool_still_loads(self) -> None:
        c = Config.from_dict({"pool": {"Axe": [10, 6], "Lion": 20}})
        self.assertEqual(c.pool, {"Axe": [10, 6], "Lion": 20})
        self.assertEqual(c.pool_records, {})

    def test_build_assistant_uses_both_pools(self) -> None:
        import tempfile

        from d2a.cli import build_assistant

        c = Config()
        c.pool = {"Lion": [10, 6]}
        c.pool_records = {"Axe": {"games": 20, "wins": 12, "by_lane": {"3": [8, 5]}}}
        c.bracket = "divine"
        a = build_assistant(c)
        self.assertIn("Lion", a.engine.pool)
        self.assertIn("Axe", a.engine.pool)
        self.assertEqual(a.engine.pool["Axe"].by_lane, {3: [8, 5]})
        self.assertEqual(a.engine.bracket, 7)


class TestCliDispatchRobustness(unittest.TestCase):
    """命令行分发的健壮性（管道输入、BOM、未知命令）。"""

    def _cli(self):
        from d2a.cli import Cli

        a = Assistant.create(book=BOOK)
        return Cli(a, Config())

    def test_bom_prefixed_command_is_recognized(self) -> None:
        """管道/文件喂输入时第一行常带 BOM，曾经导致「未知命令: ﻿pool」。"""
        import io
        from contextlib import redirect_stdout

        cli = self._cli()
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.dispatch("\ufeffpool add 斧王 10 6")
        out = buf.getvalue()
        self.assertNotIn("未知命令", out, out)
        self.assertIn("Axe", cli.a.engine.pool)

    def test_blank_and_whitespace(self) -> None:
        cli = self._cli()
        self.assertTrue(cli.dispatch(""))
        self.assertTrue(cli.dispatch("   "))

    def test_bracket_command_sets_and_clears(self) -> None:
        import io
        from contextlib import redirect_stdout

        cli = self._cli()
        with redirect_stdout(io.StringIO()):
            cli._bracket(["divine"])
        self.assertEqual(cli.a.engine.bracket, 7)
        with redirect_stdout(io.StringIO()):
            cli._bracket(["all"])
        self.assertIsNone(cli.a.engine.bracket)

    def test_bracket_command_bad_input_keeps_state(self) -> None:
        import io
        from contextlib import redirect_stdout

        cli = self._cli()
        with redirect_stdout(io.StringIO()):
            cli._bracket(["divine"])
            cli._bracket(["nonsense"])
        self.assertEqual(cli.a.engine.bracket, 7, "无效输入不该把切片清掉")


if __name__ == "__main__":
    unittest.main(verbosity=2)
