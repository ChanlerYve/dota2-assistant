# -*- coding: utf-8 -*-
"""突出性分析（统计口径）的单元测试。

重点验证**统计方法的正确性**，而不只是"能跑"：
* Wilson 区间与已知数值一致（经典 50/100 → 0.4038）；
* MAD 对极端值稳健（标准差会被抬高 260 倍，MAD 基本不动）；
* BH-FDR 与 Benjamini–Hochberg 1995 论文的算例一致；
* 小样本不会被误判为"突出"；
* 效应量门槛、区间同侧、多重检验三道闸门各自都真的生效；
* 数据自洽性校验能抓出"只统计了部分英雄"的坏数据。

运行::

    python -m unittest tests.test_outliers -v
"""

from __future__ import annotations

import pathlib
import statistics
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a import stats  # noqa: E402
from d2a.outliers import (  # noqa: E402
    KIND_DOMINANT,
    KIND_NICHE,
    KIND_SCALES_DOWN,
    KIND_SCALES_UP,
    analyze_version,
)


class TestStatPrimitives(unittest.TestCase):
    def test_norm_cdf_known_values(self):
        for z, expect in [(0.0, 0.5), (1.0, 0.8413), (1.96, 0.975), (-1.96, 0.025), (2.576, 0.995)]:
            with self.subTest(z=z):
                self.assertAlmostEqual(stats.norm_cdf(z), expect, places=4)

    def test_two_sided_p(self):
        self.assertAlmostEqual(stats.two_sided_p(1.96), 0.05, places=4)
        self.assertAlmostEqual(stats.two_sided_p(2.576), 0.01, places=4)

    def test_wilson_matches_classic_value(self):
        """50/100 的 Wilson 下界是教科书值 0.4038。"""
        lo, hi = stats.wilson_interval(50, 100)
        self.assertAlmostEqual(lo, 0.4038, places=3)
        self.assertAlmostEqual(hi, 0.5962, places=3)

    def test_wilson_narrows_with_sample_size(self):
        narrow = stats.wilson_interval(750, 1000)   # 75%
        wide = stats.wilson_interval(3, 4)          # 75%
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_wilson_stays_in_range(self):
        for w, n in [(0, 1), (1, 1), (0, 5), (5, 5), (1, 3)]:
            with self.subTest(w=w, n=n):
                lo, hi = stats.wilson_interval(w, n)
                self.assertGreaterEqual(lo, 0.0)
                self.assertLessEqual(hi, 1.0)
                self.assertLessEqual(lo, hi)

    def test_wilson_zero_samples(self):
        self.assertEqual(stats.wilson_interval(0, 0), (0.0, 1.0))

    def test_mad_matches_hand_calculation(self):
        # [1..5]：中位数 3，绝对偏差 [2,1,0,1,2] 中位数 1，×1.4826
        self.assertAlmostEqual(stats.mad([1, 2, 3, 4, 5]), 1.4826, places=6)

    def test_mad_is_robust_but_std_is_not(self):
        """这是选 MAD 而不是标准差的**核心理由**：极端值不能把自己掩盖掉。"""
        base = [1, 2, 3, 4, 5]
        with_outlier = base + [1000]
        mad_ratio = stats.mad(with_outlier) / stats.mad(base)
        std_ratio = statistics.pstdev(with_outlier) / statistics.pstdev(base)
        self.assertGreater(std_ratio, 100)          # 标准差被抬高两个数量级
        self.assertLess(mad_ratio, 3)               # MAD 基本不动
        self.assertLess(mad_ratio, std_ratio / 50)

    def test_median_even_odd(self):
        self.assertEqual(stats.median([3, 1, 2]), 2)
        self.assertEqual(stats.median([4, 1, 3, 2]), 2.5)
        with self.assertRaises(ValueError):
            stats.median([])

    def test_robust_z(self):
        self.assertAlmostEqual(stats.robust_z(2.0, 1.0, 0.5), 2.0)
        self.assertEqual(stats.robust_z(5.0, 1.0, 0.0), 0.0)  # 零离散度不炸

    def test_shrink_to_prior_arithmetic(self):
        # prior_mean=0.5, prior_total=100 -> α=β=50
        self.assertAlmostEqual(stats.shrink_to_prior(3, 3, 0.5, 100), 53 / 103, places=12)
        self.assertAlmostEqual(stats.shrink_to_prior(2600, 5000, 0.5, 100), 2650 / 5100, places=12)
        self.assertEqual(stats.shrink_to_prior(0, 0, 0.42, 100), 0.42)

    def test_shrink_pulls_small_samples_harder(self):
        small = stats.shrink_to_prior(3, 3, 0.5, 40)
        large = stats.shrink_to_prior(30000, 30000, 0.5, 40)
        self.assertLess(abs(small - 0.5), abs(large - 0.5))

    def test_fit_beta_prior_mean_matches_sample_mean(self):
        rates = [0.48 + 0.001 * i for i in range(30)]
        mu, strength = stats.fit_beta_prior(rates)
        self.assertAlmostEqual(mu, sum(rates) / len(rates), places=12)
        self.assertGreater(strength, 0)
        self.assertLessEqual(strength, 5000)

    def test_fit_beta_prior_empty(self):
        self.assertEqual(stats.fit_beta_prior([]), (0.5, 1.0))

    def test_fit_beta_prior_strength_inverse_to_spread(self):
        tight = [0.5 + 0.001 * ((i % 5) - 2) for i in range(50)]
        wide = [0.5 + 0.05 * ((i % 5) - 2) for i in range(50)]
        _, s_tight = stats.fit_beta_prior(tight)
        _, s_wide = stats.fit_beta_prior(wide)
        self.assertGreater(s_tight, s_wide)

    def test_bh_fdr_matches_paper_example(self):
        """Benjamini–Hochberg 1995 的经典算例：这 10 个 p 值全部被拒绝。"""
        ps = [0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344, 0.0459, 0.05]
        self.assertEqual(stats.bh_fdr(ps, 0.05), [True] * 10)

    def test_bh_fdr_rejects_nothing_for_pure_noise(self):
        self.assertEqual(stats.bh_fdr([0.3, 0.5, 0.7, 0.9, 0.2], 0.05), [False] * 5)

    def test_bh_fdr_boundaries(self):
        self.assertEqual(stats.bh_fdr([], 0.05), [])
        self.assertEqual(stats.bh_fdr([0.001], 0.05), [True])
        self.assertEqual(stats.bh_fdr([0.5], 0.05), [False])

    def test_bh_fdr_respects_order(self):
        """返回值必须与输入顺序对应，不能因为内部排序而错位。"""
        ps = [0.9, 0.0001, 0.8, 0.0002]
        rej = stats.bh_fdr(ps, 0.05)
        self.assertEqual(rej, [False, True, False, True])

    def test_inflate_variance_reduces_to_binomial(self):
        v = stats.inflate_variance(0.5, 1000, rho=0.0)
        self.assertAlmostEqual(v, 0.25 / 1000, places=15)
        self.assertGreater(stats.inflate_variance(0.5, 1000, rho=0.001), v)
        self.assertEqual(stats.inflate_variance(0.5, 0), 0.0)

    def test_wilson_z_against_direction(self):
        """明显偏高的样本，z 应当很大；偏低则为大负值。"""
        high = stats.wilson_z_against(5400, 10000, 0.5, above=True)
        low = stats.wilson_z_against(4600, 10000, 0.5, above=True)
        self.assertGreater(high, 5)
        self.assertLess(low, -5)


class TestAnalyze(unittest.TestCase):
    def _flat_pool(self, n_heroes=20, picks=10000, wins=5000):
        return {f"Hero{i:02d}": (picks, wins) for i in range(n_heroes)}

    def test_empty_input(self):
        rep = stats.analyze({})
        self.assertEqual(rep.n_heroes, 0)
        self.assertTrue(rep.notes)

    def test_all_zero_picks(self):
        rep = stats.analyze({"A": (0, 0), "B": (0, 0)})
        self.assertEqual(rep.n_heroes, 0)

    def test_detects_single_strong_outlier(self):
        s = self._flat_pool()
        s["Broken"] = (10000, 5400)
        s["Trash"] = (10000, 4600)
        rep = stats.analyze(s, patch="test")
        self.assertEqual([x.hero for x in rep.outliers_high], ["Broken"])
        self.assertEqual([x.hero for x in rep.outliers_low], ["Trash"])

    def test_small_sample_not_flagged(self):
        """3 局全胜必须不被判为突出（收缩 + 区间宽度共同作用）。"""
        s = self._flat_pool()
        s["Lucky"] = (3, 3)
        rep = stats.analyze(s)
        lucky = next(x for x in rep.signals if x.hero == "Lucky")
        self.assertFalse(lucky.significant)
        self.assertTrue(any("样本偏少" in f for f in lucky.flags))

    def test_effect_size_gate_blocks_tiny_difference(self):
        """差距小于 min_excess 时，即使样本极大也不能判突出。"""
        picks, wins = 1_000_000, 500_000
        s = {f"Hero{i:02d}": (picks, wins) for i in range(20)}
        s["TinyGain"] = (picks, wins + 2000)   # +0.2pp
        rep = stats.analyze(s, min_excess=0.015)
        self.assertAlmostEqual(rep.pool_median, 0.5, places=9)
        tiny = next(x for x in rep.signals if x.hero == "TinyGain")
        self.assertAlmostEqual(tiny.excess, 0.002, places=6)
        self.assertFalse(tiny.significant, "效应量不足 1.5pp 不该判显著")

    def test_effect_size_gate_raises_flag_count(self):
        s = self._flat_pool()
        for i in range(5):
            s[f"Good{i}"] = (10000, 5200)   # +2pp
        loose = stats.analyze(s, min_excess=0.01)
        strict = stats.analyze(s, min_excess=0.03)
        self.assertGreater(len(loose.outliers_high), len(strict.outliers_high))

    def test_ci_must_be_entirely_on_one_side(self):
        """显著偏强的英雄，其 Wilson 下界必须高于池中位数。"""
        s = self._flat_pool()
        s["Broken"] = (10000, 5400)
        rep = stats.analyze(s)
        broken = next(x for x in rep.signals if x.hero == "Broken")
        self.assertTrue(broken.significant)
        self.assertGreater(broken.ci_low, rep.pool_median)

    def test_zero_mad_does_not_break(self):
        """所有英雄胜率完全相同时 MAD=0，不能导致除零或 z 全为 0 之外的问题。"""
        s = {f"H{i}": (1000, 500) for i in range(10)}
        rep = stats.analyze(s)
        self.assertGreater(rep.pool_mad, 0.0)
        self.assertEqual(len(rep.signals), 10)

    def test_pool_spread_uses_raw_rates_not_shrunk(self):
        """回归：池的离散度必须用**原始观测胜率**算。

        曾经用收缩值算 MAD，收缩会把 10 个均衡英雄全部拉到同一个先验值上，
        使中位数恰好落在那个值、MAD 变成 0；兜底逻辑随后把所有英雄压到
        同一把尺子上——实测表现为三个胜率差很大的档位算出**完全相同**的 z。
        """
        s = {f"H{i}": (5000, 2500) for i in range(10)}   # 全部 0.5
        s["Strong"] = (5000, 3100)                       # 0.62
        rep = stats.analyze(s)
        # 2 个不同取值时，中位数仍是 0.5，MAD 非 0
        self.assertGreater(rep.pool_mad, 1e-6, "MAD 不该被收缩抹成 0")
        strong = next(x for x in rep.signals if x.hero == "Strong")
        self.assertGreater(strong.z, 1.0, "应有明显的稳健 z")

    def test_scale_invariance_reality_check(self):
        """把「z 尺度不变」这个事实钉死，防止以后有人误用 z 比较档位。"""
        def z_for(excess):
            pool = {f"H{i}": (10000, 5000) for i in range(20)}
            pool["X"] = (10000, 5000 + excess)
            rep = stats.analyze(pool)
            return next(x for x in rep.signals if x.hero == "X").z

        z_small = z_for(200)    # +2pp
        z_big = z_for(1000)     # +10pp
        # 池形状不变时 MAD 与效应量成正比，于是 z 恒定
        self.assertAlmostEqual(z_small, z_big, places=6)
        # 所以 z 只能回答"偏了几个离散单位"，不能回答"哪个档位更强"
        self.assertLess(abs(z_small), 100)

    def test_integrity_check_catches_partial_data(self):
        """加权总胜率明显偏离 50% 时，必须给出数据自洽性告警。"""
        # 人为造一批「胜率都很高」的数据，加权总胜率会远高于 0.5
        s = {f"H{i}": (1000, 800) for i in range(10)}
        rep = stats.analyze(s)
        self.assertGreater(rep.weighted_winrate, 0.7)
        self.assertTrue(any("数据自洽性异常" in n for n in rep.notes))

    def test_integrity_check_passes_on_balanced_data(self):
        s = {f"H{i}": (1000, 500) for i in range(10)}
        rep = stats.analyze(s)
        self.assertAlmostEqual(rep.weighted_winrate, 0.5, places=9)
        self.assertTrue(any("自洽性校验通过" in n for n in rep.notes))

    def test_signals_sorted_by_z_desc(self):
        s = self._flat_pool()
        s["Best"] = (10000, 5400)
        s["Worst"] = (10000, 4600)
        rep = stats.analyze(s)
        zs = [x.z for x in rep.signals]
        self.assertEqual(zs, sorted(zs, reverse=True))

    def test_pick_share_sums_to_one(self):
        s = self._flat_pool()
        rep = stats.analyze(s)
        self.assertAlmostEqual(sum(x.pick_share for x in rep.signals), 1.0, places=9)


class TestVersionAnalysis(unittest.TestCase):
    def _data(self):
        overall = {f"H{i}": (10000, 5000) for i in range(10)}
        overall["Strong"] = (10000, 5300)
        # 分档位：Strong 在低分段更强（z 随段位下降），差距要够大才看得出趋势。
        # 每档 10 个均衡英雄 (2500/5000) + Strong。
        per_bracket = {}
        for b, extra in ((3, 600), (5, 400), (7, 200)):
            d = {f"H{i}": (5000, 2500) for i in range(10)}
            d["Strong"] = (5000, 2500 + extra)
            per_bracket[b] = d
        return overall, per_bracket

    def test_analyze_version_without_brackets(self):
        overall, _ = self._data()
        rep = analyze_version(overall, patch="t")
        self.assertIsNotNone(rep.overall)
        self.assertTrue(rep.notes)
        self.assertTrue(any(x.hero == "Strong" for x in rep.notable))

    def test_analyze_version_with_brackets(self):
        overall, per_bracket = self._data()
        rep = analyze_version(overall, per_bracket=per_bracket, patch="t")
        self.assertEqual(set(rep.brackets), {3, 5, 7})
        strong = next(x for x in rep.outliers if x.hero == "Strong")
        self.assertTrue(strong.bracket_z, "应有分档位 z")
        self.assertGreater(strong.agreement, 0.0)
        self.assertLess(strong.trend_slope, 0, "低分段更强应给出负斜率")

    def test_z_is_scale_invariant_so_trend_must_use_excess(self):
        """**关键设计约束**：稳健 z 是尺度不变的，不能用来比较档位强弱。

        当每个档位的池形状相同（同样是 10 个均衡英雄 + 1 个异常），
        MAD 与效应量成正比（这里恰好 0.48×），于是 z = excess/MAD 是常数——
        即使三个档位的胜率是 62% / 58% / 54%，z 也完全一样。

        所以趋势必须用「该档位内相对中位数的优势(excess)」来算，
        这正是 analyze_version 里我特意绕开的坑。
        """
        overall = {f"H{i}": (10000, 5000) for i in range(10)}
        overall["Strong"] = (10000, 5300)
        per_bracket = {}
        for b, extra in ((3, 600), (5, 400), (7, 200)):
            d = {f"H{i}": (5000, 2500) for i in range(10)}
            d["Strong"] = (5000, 2500 + extra)
            per_bracket[b] = d
        rep = analyze_version(overall, per_bracket=per_bracket, patch="t")
        strong = next(x for x in rep.outliers if x.hero == "Strong")
        zs = set(round(v, 6) for v in strong.bracket_z.values())
        self.assertEqual(len(zs), 1, f"同形状池下 z 应当恒定，实际 {zs}")
        # 而胜率其实差别很大 —— 说明 z 不能当跨档位比较的尺子
        wrs = sorted(strong.bracket_winrate.values())
        self.assertGreater(wrs[-1] - wrs[0], 0.05)
        # 趋势仍能正确识别方向
        self.assertLess(strong.trend_slope, 0)

    def test_kind_classification_dominant_vs_niche(self):
        """出场率高于中位数 → 热门；远低于中位数 → 冷门强点。"""
        overall = {f"H{i}": (10000, 5000) for i in range(20)}
        overall["Popular"] = (40000, 21600)   # 强 + 高出场
        overall["Rare"] = (150, 84)           # 强 + 极低出场（低于 MIN_RELIABLE_PICKS）
        rep = analyze_version(overall, patch="t")
        pop = next(x for x in rep.outliers if x.hero == "Popular")
        rare = next(x for x in rep.outliers if x.hero == "Rare")
        self.assertIn(KIND_DOMINANT, pop.kinds)
        # 150 场低于 MIN_RELIABLE_PICKS，必须被标为样本不足
        self.assertFalse(rare.reliable)
        self.assertTrue(any("样本偏少" in f for f in rare.flags))
        # 冷门强点只对"显著且样本够"的才下结论，这里不应误标
        if rare.significant:
            self.assertIn(KIND_NICHE, rare.kinds)

    def test_summary_is_human_readable(self):
        overall, per_bracket = self._data()
        rep = analyze_version(overall, per_bracket=per_bracket, patch="t")
        strong = next(x for x in rep.outliers if x.hero == "Strong")
        self.assertIn("胜率", strong.summary)
        self.assertIn("稳健 z", strong.summary)

    def test_top_n_limits_output(self):
        overall, _ = self._data()
        rep = analyze_version(overall, top_n=3, patch="t")
        self.assertEqual(len(rep.outliers), 3)

    def test_empty_overall(self):
        rep = analyze_version({}, patch="t")
        self.assertEqual(rep.notes, ["全体数据为空，无法分析"])


class TestRealData(unittest.TestCase):
    """对仓库自带数据做不变量校验（不需要网络）。"""

    @classmethod
    def setUpClass(cls):
        import json

        p = ROOT / "data" / "cache" / "opendota_hero_stats.json"
        if not p.exists():
            raise unittest.SkipTest("缺少 heroStats 缓存")
        cls.rows = json.loads(p.read_text(encoding="utf-8"))
        h = ROOT / "data" / "cache" / "opendota_heroes.json"
        cls.id_to_name = {}
        if h.exists():
            for x in json.loads(h.read_text(encoding="utf-8")):
                cls.id_to_name[int(x["id"])] = x["localized_name"]

    def _samples(self):
        known = {
            x["name"]
            for x in __import__("json").loads(
                (ROOT / "data" / "heroes.json").read_text(encoding="utf-8")
            )["heroes"]
        }
        out = {}
        for r in self.rows:
            n = self.id_to_name.get(int(r.get("id", 0)))
            if n in known and int(r.get("pub_pick") or 0) > 0:
                out[n] = (int(r["pub_pick"]), min(int(r.get("pub_win") or 0), int(r["pub_pick"])))
        return out

    def test_real_data_is_self_consistent(self):
        """真实数据的加权总胜率必须严格等于 50%——这是数据完整性的硬校验。"""
        s = self._samples()
        self.assertGreater(len(s), 100)
        rep = stats.analyze(s)
        self.assertAlmostEqual(rep.weighted_winrate, 0.5, places=6)
        self.assertFalse(any("自洽性异常" in n for n in rep.notes))

    def test_real_data_produces_some_outliers(self):
        s = self._samples()
        rep = stats.analyze(s)
        self.assertGreater(len(rep.outliers_high), 0)
        self.assertGreater(len(rep.outliers_low), 0)
        # 不应把大半个英雄池都判成异常
        self.assertLess(len(rep.outliers_high), rep.n_heroes * 0.5)

    def test_real_data_pool_mad_is_positive_and_sane(self):
        s = self._samples()
        rep = stats.analyze(s)
        self.assertGreater(rep.pool_mad, 0.0)
        self.assertLess(rep.pool_mad, 0.1, "胜率离散度过大，数据可能有问题")
        # 池中位数应贴近 50%
        self.assertLess(abs(rep.pool_median - 0.5), 0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
