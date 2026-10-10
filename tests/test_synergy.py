# -*- coding: utf-8 -*-
"""英雄协同数据（真实数据 + 收缩）的测试。

协同此前只依赖人工种子（35 个英雄 / 81 条边），对约 72% 的英雄对查询返回
「无数据」——引擎的「配合」因子因此对多数英雄等于没算，白白消耗权重。
这些测试保证真实数据接得上、收缩方向正确、且不会退化回那个状态。

运行::

    python -m unittest tests.test_synergy -v
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.data_loader import HeroBook  # noqa: E402

BOOK = HeroBook.load()


def _tool():
    spec = importlib.util.spec_from_file_location(
        "d2a_refresh_syn", ROOT / "tools" / "refresh_synergies.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestSynergyLoading(unittest.TestCase):
    def test_live_source_recorded(self) -> None:
        """真实协同数据若存在，必须被记录来源，便于 UI 标注数据出处。"""
        p = ROOT / "data" / "synergies_live.json"
        if not p.exists():
            self.skipTest("没有 synergies_live.json")
        self.assertNotEqual(BOOK.synergy_source, "builtin")
        self.assertGreater(BOOK.synergy_fetched_at, 0)

    def test_coverage_far_exceeds_seed(self) -> None:
        """覆盖率是这次修复的核心指标：必须远超人工种子的 35 个英雄。"""
        covered = [h for h in BOOK.names() if BOOK.synergies.get(h)]
        self.assertGreater(
            len(covered), 100, f"协同只覆盖 {len(covered)} 个英雄，疑似退化回人工种子"
        )

    def test_edges_far_exceed_seed(self) -> None:
        edges = sum(len(v) for v in BOOK.synergies.values()) // 2
        self.assertGreater(edges, 2000, f"协同边只有 {edges} 条，疑似退化")

    def test_synergy_is_symmetric(self) -> None:
        """A→B 与 B→A 必须一致，否则引擎取哪个方向会决定结果。"""
        checked = 0
        for a, row in BOOK.synergies.items():
            for b, v in row.items():
                rev = BOOK.synergies.get(b, {}).get(a)
                if rev is not None:
                    checked += 1
                    self.assertAlmostEqual(v, rev, places=6, msg=f"{a}/{b} 不对称")
        self.assertGreater(checked, 100)
        # 记录一下：实测应接近 2 倍边数（双向都存）
        self.assertGreater(checked, 2000)

    def test_synergy_values_within_sane_range(self) -> None:
        """收缩后的协同不该出现离谱值；超出 ±15% 说明口径或数据有问题。"""
        worst = 0.0
        for row in BOOK.synergies.values():
            for v in row.values():
                worst = max(worst, abs(v))
        self.assertLess(worst, 0.15, f"协同极值 {worst:.3f} 过大")

    def test_synergy_lookup_missing_returns_zero(self) -> None:
        """未收录的配对返回 0.0（无信息），而不是报错。"""
        self.assertEqual(BOOK.synergy("Axe", "不存在的英雄"), 0.0)

    def test_known_combos_positive(self) -> None:
        """抽查几个公认的组合，应当为正（否则口径有问题）。"""
        for a, b in (("Drow Ranger", "Vengeful Spirit"), ("Mirana", "Shadow Demon")):
            with self.subTest(pair=f"{a}+{b}"):
                self.assertGreater(BOOK.synergy(a, b), 0.005, f"{a}+{b} 应为正协同")

    def test_stats_metadata_present(self) -> None:
        """信噪比等口径元数据要留下来，否则以后无法判断数据是否可信。"""
        if not BOOK.synergy_stats:
            self.skipTest("没有 stats 元数据")
        for k in ("raw_pairs", "kept_pairs", "signal_std", "noise_std", "snr_ratio"):
            self.assertIn(k, BOOK.synergy_stats)
        # 信噪比必须 > 1（否则说明全是噪声，不该启用这个因子）
        self.assertGreater(float(BOOK.synergy_stats["snr_ratio"]), 1.0)


class TestShrinkage(unittest.TestCase):
    """协同的收缩逻辑（James–Stein）——方向与量级都要正确。

    注意：信号方差是从**整批配对**估出来的（观测方差扣掉二项噪声）。
    所以合成数据必须有**足够多条且取值有真实离散度**的配对，
    否则估出的 s²≈0，所有配对都会被收缩到 0 并被阈值丢弃——
    这不是 bug，而是算法在「看不出信号」时的正确保守行为。
    """

    def _rows(self, pairs):
        """构造 Explorer 返回形状的行。"""
        out = []
        for h1, h2, g, wr_pair, wr1, wr2 in pairs:
            out.append({
                "h1": h1, "h2": h2, "g": g, "w": int(wr_pair * g),
                "g1": 100000, "w1": int(wr1 * 100000),
                "g2": 100000, "w2": int(wr2 * 100000),
            })
        return out

    def _background(self, n=40, g=5000):
        """造一批有真实离散度的背景配对，让 s² 能被正确估出来。

        一半 +4%、一半 -4%，外加中心附近若干，模拟真实数据的分布形状。
        返回的已是 Explorer 行格式（dict），可直接喂给 compute_synergies。
        """
        pairs = []
        for i in range(n):
            hid1 = 1000 + i * 2
            hid2 = hid1 + 1
            delta = 0.04 if i % 3 == 0 else (-0.04 if i % 3 == 1 else 0.01)
            pairs.append((hid1, hid2, g, 0.50 + delta, 0.50, 0.50))
        return self._rows(pairs)

    def _names(self, n=40):
        ids = {}
        for i in range(n):
            ids[1000 + i * 2] = f"BG{i}a"
            ids[1000 + i * 2 + 1] = f"BG{i}b"
        return ids

    def test_shrink_pulls_small_samples_toward_zero(self) -> None:
        """同样的原始协同，样本越小被收缩得越狠。"""
        m = _tool()
        id2name = self._names()
        id2name.update({1: "A", 2: "B", 3: "C", 4: "D"})
        rows = self._background()
        # 两对都是 +8% 原始协同，但样本量差 20 倍
        rows += self._rows([
            (1, 2, 300, 0.58, 0.50, 0.50),      # 小样本
            (3, 4, 6000, 0.58, 0.50, 0.50),     # 大样本
        ])
        pairs, _ = m.compute_synergies(rows, id2name, min_games=100)
        self.assertIn("A", pairs, "小样本配对不该被整个丢掉（背景已提供 s²）")
        small = pairs["A"]["B"]
        large = pairs["C"]["D"]
        self.assertLess(small, large, "小样本应被收缩得更多")
        self.assertGreater(small, 0.0, "方向不应被反转")
        self.assertLessEqual(large, 0.08 * 1.001, "大样本不该超过原始值")

    def test_no_signal_spread_means_heavy_shrinkage(self) -> None:
        """当所有配对取值都相同（看不出信号）时，应几乎全部收缩为 0。

        这是刻意的保守行为：无法区分信号与噪声时就不该给出意见。
        """
        m = _tool()
        id2name = self._names(20)
        rows = self._rows([(1000 + i * 2, 1000 + i * 2 + 1, 5000, 0.55, 0.50, 0.50)
                           for i in range(20)])
        pairs, meta = m.compute_synergies(rows, id2name, min_games=100)
        self.assertLessEqual(meta["signal_std"], 0.005, "同值数据估不出信号方差")
        self.assertEqual(len(pairs), 0, "无信号时不该输出任何配对")

    def test_below_threshold_dropped(self) -> None:
        """收缩后绝对值过小的配对应被丢弃（视为无信号）。"""
        m = _tool()
        id2name = self._names()
        id2name.update({1: "A", 2: "B"})
        rows = self._background()
        rows += self._rows([(1, 2, 500, 0.5001, 0.50, 0.50)])
        pairs, _ = m.compute_synergies(rows, id2name, min_games=100)
        if "A" in pairs:
            self.assertGreaterEqual(abs(pairs["A"]["B"]), m.MIN_ABS_SYNERGY)

    def test_unknown_heroes_skipped(self) -> None:
        m = _tool()
        rows = self._rows([(999, 998, 5000, 0.55, 0.50, 0.50)])
        pairs, meta = m.compute_synergies(rows, {1: "A"}, min_games=100)
        self.assertEqual(pairs, {})

    def test_min_games_filter_applied(self) -> None:
        m = _tool()
        id2name = {1: "A", 2: "B"}
        rows = self._rows([(1, 2, 50, 0.60, 0.50, 0.50)])
        pairs, _ = m.compute_synergies(rows, id2name, min_games=200)
        self.assertEqual(pairs, {}, "低于 min_games 的配对应被过滤")

    def test_stored_both_directions(self) -> None:
        m = _tool()
        id2name = self._names()
        id2name.update({1: "A", 2: "B"})
        rows = self._background()
        rows += self._rows([(1, 2, 30000, 0.60, 0.50, 0.50)])
        pairs, _ = m.compute_synergies(rows, id2name, min_games=100)
        self.assertIn("A", pairs)
        self.assertEqual(pairs["A"]["B"], pairs["B"]["A"])

    def test_signal_variance_estimated_from_data(self) -> None:
        """s² 必须从数据估计（观测方差扣噪声），不能写死。"""
        m = _tool()
        id2name = self._names()
        rows = self._background()
        _, meta = m.compute_synergies(rows, id2name, min_games=100)
        self.assertGreater(meta["signal_std"], 0.0)
        self.assertGreater(meta["obs_std"], meta["noise_std"] - 1e-9)
        self.assertGreater(meta["snr_ratio"], 1.0)


class TestEngineSynergyFactor(unittest.TestCase):
    """「配合」因子现在应当对多数英雄对有实际取值。"""

    def test_factor_fires_for_most_pairs(self) -> None:
        import random

        from d2a.draft import Draft
        from d2a.engine import Assistant

        if not any(BOOK.synergies.get(h) for h in BOOK.names()):
            self.skipTest("没有协同数据")
        a = Assistant.create(book=BOOK)
        names = [h for h in BOOK.names() if BOOK.synergies.get(h)]
        random.seed(7)
        have = 0
        trials = 300
        for _ in range(trials):
            x, y = random.sample(names, 2)
            d = Draft()
            d.add(x, "ally")
            sc, _ = a.engine.synergy(y, d)
            if sc is not None:
                have += 1
        ratio = have / trials
        self.assertGreater(ratio, 0.5, f"只有 {ratio:.0%} 的组合有协同数据，疑似退化")

    def test_synergy_reason_mentions_partner(self) -> None:
        from d2a.draft import Draft
        from d2a.engine import Assistant

        a = Assistant.create(book=BOOK)
        # 找一对正协同的组合
        found = None
        for x in BOOK.names():
            for y, v in (BOOK.synergies.get(x) or {}).items():
                if v >= 0.02:
                    found = (x, y)
                    break
            if found:
                break
        if not found:
            self.skipTest("没有找到强正协同")
        x, y = found
        d = Draft()
        d.add(x, "ally")
        sc, reasons = a.engine.synergy(y, d)
        self.assertIsNotNone(sc)
        self.assertTrue(any(x in r for r in reasons), f"理由应提到队友 {x}: {reasons}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
