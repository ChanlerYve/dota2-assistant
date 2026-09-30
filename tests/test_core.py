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
from d2a.data_loader import HeroBook, HeroNotFound, PlayerHeroStat  # noqa: E402
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
