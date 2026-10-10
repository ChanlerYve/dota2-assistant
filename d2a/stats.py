# -*- coding: utf-8 -*-
"""统计工具：用来判断「某个英雄的胜率是否真的异常」。

为什么不能直接看胜率排序
------------------------
Dota 里**每场比赛必有且只有一个胜者**，所以全英雄的胜率在数学上被迫围绕 50%
分布。这带来两个后果：

1. 不能用「和 50% 做二项检验」来判断强弱——50% 不是零假设，而是构造必然；
2. 英雄之间不是独立的（同一场比赛里一方赢、另一方就输），
   教科书式的两比例检验会**低估**方差。

所以这里的做法是「**经验零分布**」：把全部英雄的观测胜率当成一个分布，
先用该分布本身的水平与离散度构造零假设，再看谁离得远。这也是
Dota 2 学术研究里判断英雄强弱的常见思路（阈值法 ``μ + k·σ``，
见 Kinetik 期刊 2022 年那篇 Dota 2 英雄削弱预测），差别在于本模块：

* 用 **MAD** 而不是标准差来度量离散度（少数极端英雄不会把 σ 抬高从而掩盖自己）；
* 用 **经验贝叶斯**估计零假设的 Beta 先验，而不是假设一个固定的 50%；
* 用 **AR(1) 相关结构**的二项方差修正英雄间相关性（同场对手负相关）；
* 做 **Benjamini–Hochberg FDR** 多重检验校正（一次比较 127 个英雄，
  不校正的话按 α=0.05 会有约 6 个纯靠运气"显著"）。

纯标准库实现，不引入 numpy/scipy：需要的只是正态 CDF、不完全 Beta
和几次排序，用 math 手写即可，且便于单测逐条核对数值。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- 基础
Z_95 = 1.959963984540054
Z_99 = 2.5758293035489004

# 样本量低于此值时，稳健 z 会因点估计极端而显得很大，但区间其实很宽。
# 这种情况必须显式标注，否则用户会把一个 3 局全胜的英雄当成版本答案。
MIN_RELIABLE_PICKS = 300


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def norm_cdf(z: float) -> float:
    """标准正态 CDF。用 erf 实现，避免依赖 scipy。"""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_sided_p(z: float) -> float:
    """双侧 p 值。"""
    return 2.0 * (1.0 - norm_cdf(abs(z)))


def median(xs: Sequence[float]) -> float:
    if not xs:
        raise ValueError("median() 需要非空序列")
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def mad(xs: Sequence[float], scale: float = 1.4826) -> float:
    """中位数绝对偏差（MAD）。

    ``scale=1.4826`` 使其在正态分布下与标准差一致（一致性因子
    ``1/Φ⁻¹(0.75)``）。之所以用 MAD 而不是标准差：标准差会被极端值抬高，
    从而让极端值自己变得"不显著"——正是我们要找的那批英雄会掩盖自己。
    """
    if not xs:
        raise ValueError("mad() 需要非空序列")
    med = median(xs)
    return scale * median([abs(x - med) for x in xs])


def robust_z(x: float, center: float, spread: float) -> float:
    """稳健 z 分数：偏离中心多少个稳健离散单位。"""
    if spread <= 0:
        return 0.0
    return (x - center) / spread


# --------------------------------------------------------------------------- 区间
def wilson_interval(wins: int, n: int, z: float = Z_95) -> Tuple[float, float]:
    """Wilson 得分区间（二项比例的置信区间）。

    比正态近似好在：小样本、比例接近 0/1 时仍然给出合理区间，
    且不会越界。用于给「保守胜率下界」排序，避免小样本英雄靠运气排前面。
    """
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return clamp(center - half), clamp(center + half)


def wilson_lower(wins: int, n: int, z: float = Z_95) -> float:
    """Wilson 下界——「保守估计这个英雄至少有多强」。"""
    return wilson_interval(wins, n, z)[0]


def wilson_z_against(wins: int, n: int, reference: float, above: bool = True) -> float:
    """在 Wilson 区间框架下，检验观测比例与 ``reference`` 的差异。

    为什么不用朴素的两比例 z 检验：Dota 的胜率基准（"典型英雄有多强"）本身
    也是从同一批数据估出来的，直接套两比例公式会把基准当成已知常数，
    **高估显著性**。这里改用 Wilson 区间的隐式方差 ``p̃(1-p̃)/ñ``，
    它天然偏向保守（小样本时区间更宽 → z 更小），不会制造虚假显著。

    ``above=True`` 时返回"高于基准"方向的 z（负值表示其实低于基准）。
    """
    if n <= 0:
        return 1e9 if above else -1e9
    p = wins / n
    z0 = Z_95
    z2 = z0 * z0
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z0 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    se = half / z0  # 由区间半宽反推标准误
    if se <= 0:
        return 0.0
    z = (center - reference) / se
    return z if above else -z


# --------------------------------------------------------------------------- 收缩
def shrink_to_prior(wins: int, n: int, prior_mean: float, prior_total: float) -> float:
    """把观测胜率向先验收缩（经验贝叶斯的点估计）。

    参数说明（这里容易搞混，特意写成明确的名字）：
      ``prior_mean``  = α/(α+β)，先验均值；
      ``prior_total`` = α+β，先验的"虚拟样本量"（**不是** α 本身）。

    后验均值 = (wins + α) / (n + α + β)，其中 α = prior_mean·prior_total、
    β = (1-prior_mean)·prior_total。样本越少收缩越狠，
    所以 3 局 3 胜不会排到 200 局 60% 前面。
    """
    if n <= 0:
        return prior_mean
    alpha = prior_mean * prior_total
    beta = (1.0 - prior_mean) * prior_total
    return (wins + alpha) / (n + alpha + beta)


def shrink_with(prior: Tuple[float, float], wins: int, n: int) -> float:
    """:func:`shrink_to_prior` 的便捷包装，直接吃 :func:`fit_beta_prior` 的返回值。"""
    return shrink_to_prior(wins, n, prior[0], prior[1])


def fit_beta_prior(rates: Sequence[float], weights: Optional[Sequence[float]] = None) -> Tuple[float, float]:
    """由观测到的各英雄胜率，用矩估计拟合一个 Beta 先验。

    返回 ``(mean, strength)``，其中 ``strength = α + β``（先验的"虚拟样本量"）。
    这比"假设先验是 50%"更贴近现实：不同版本的胜率整体分布本来就会偏移。

    做法：样本均值为 μ、样本方差为 v，则
        μ = α/(α+β)， v = αβ/((α+β)²(α+β+1))
    解出 ``α+β = μ(1-μ)/v - 1``。
    """
    if not rates:
        return 0.5, 1.0
    if weights and len(weights) == len(rates) and sum(weights) > 0:
        wsum = sum(weights)
        mu = sum(r * w for r, w in zip(rates, weights)) / wsum
        var = sum(w * (r - mu) ** 2 for r, w in zip(rates, weights)) / wsum
    else:
        mu = sum(rates) / len(rates)
        var = sum((r - mu) ** 2 for r in rates) / len(rates)
    mu = clamp(mu, 1e-4, 1 - 1e-4)
    # 观测方差里含有二项抽样噪声，先扣掉一个保守的量，避免 strength 被压到 0
    var = max(var, 1e-9)
    strength = mu * (1 - mu) / var - 1.0
    if not math.isfinite(strength) or strength < 1.0:
        strength = 1.0
    # 上限防止"完全不相信数据"（等价于忽略观测）
    strength = min(strength, 5000.0)
    return mu, strength


# --------------------------------------------------------------------------- 检验
def inflate_variance(p: float, n: int, rho: float = 0.0) -> float:
    """二项方差的相关性修正。

    Dota 里英雄之间不是独立的：一方的胜对应另一方的负（同场负相关），
    而同一队里的英雄则正相关。这会让「英雄胜率的离散度」与独立假设不同。
    用 AR(1) 近似给出一个膨胀/收缩因子：

        Var(p̂) ≈ p(1-p)/n · (1 + (n-1)·ρ) / n · n = p(1-p)/n · (1+(n-1)ρ)

    取 ``rho=0`` 即退化成标准二项方差。默认 0 是**刻意保守**：我们不假设
    任何相关性，让显著性判定更严格而不是更宽松。
    """
    if n <= 0:
        return 0.0
    factor = 1.0 + (n - 1) * rho
    factor = max(factor, 1e-6)
    return p * (1.0 - p) / n * factor


def bh_fdr(pvalues: Sequence[float], alpha: float = 0.05) -> List[bool]:
    """Benjamini–Hochberg 多重检验：返回每个假设是否在 FDR=alpha 下被拒绝。

    一次比较 127 个英雄，α=0.05 下平均有 6 个纯靠运气"显著"。
    BH 控制的是"被判显著的人里假阳性的期望比例"，比 Bonferroni 温和得多，
    适合这种「宁可多报几个候选、但要知道有多少是噪声」的场景。
    """
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    reject = [False] * n
    # 从最大的 p 往回找第一个满足 p <= (i/n)*alpha 的位置
    k_max = -1
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= rank / n * alpha:
            k_max = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= k_max:
            reject[idx] = True
    return reject


# --------------------------------------------------------------------------- 报告
@dataclass
class HeroSignal:
    """单个英雄在某个切片下的突出性指标。"""

    hero: str
    picks: int
    wins: int
    winrate: float
    # 区间与收缩
    ci_low: float = 0.0
    ci_high: float = 1.0
    shrunk: float = 0.5
    pick_share: float = 0.0        # 该切片内的出场占比
    # 突出性
    z: float = 0.0                 # 稳健 z（相对英雄池，用 MAD 度量离散）
    p_value: float = 1.0
    significant: bool = False      # 通过效应量 + 区间 + BH-FDR 三重闸门
    excess: float = 0.0            # 相对池中位数的胜率超出
    above_50: bool = False         # 保守区间是否整体高于/低于 50%
    flags: List[str] = field(default_factory=list)


@dataclass
class MetaReport:
    """整个英雄池的突出性分析结果。"""

    patch: str = "unknown"
    bracket: Optional[int] = None
    bracket_label: str = "全体"
    n_heroes: int = 0
    total_picks: int = 0
    # 池的统计量
    pool_mean: float = 0.5
    pool_median: float = 0.5
    pool_mad: float = 0.0
    pool_std: float = 0.0
    weighted_winrate: float = 0.5   # 按出场加权的总胜率，理论上必须 = 0.5
    prior_mean: float = 0.5
    prior_strength: float = 1.0
    signals: List[HeroSignal] = field(default_factory=list)
    outliers_high: List[HeroSignal] = field(default_factory=list)
    outliers_low: List[HeroSignal] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- 主流程
def analyze(
    samples: Dict[str, Tuple[int, int]],
    patch: str = "unknown",
    bracket: Optional[int] = None,
    bracket_label: str = "全体",
    fdr: float = 0.05,
    min_excess: float = 0.015,
    rho: float = 0.0,
) -> MetaReport:
    """对 ``{英雄: (pick, win)}`` 做突出性分析。

    判定「突出」要同时过三道闸门，缺一不可：

    1. **效应量**：与池中位数的差距 >= ``min_excess``（默认 1.5 个百分点）。
       样本量在 Dota 里动辄几十万，纯统计显著太廉价——0.1 个百分点的差距
       也能"显著"。先把没实际意义的差距排除掉。
    2. **区间证据**：Wilson 区间必须**完全**位于中位数同侧
       （上界 < 中位数 判为偏弱，下界 > 中位数 判为偏强）。
    3. **多重检验**：对 127 个英雄做 BH-FDR 校正，控制假阳性比例。

    另外给出**稳健 z** 作为"在英雄池里偏了多少个离散单位"的描述性度量
    （用 MAD，不受少数极端英雄影响），以及相对 50% 的绝对强弱。
    """
    rep = MetaReport(patch=patch, bracket=bracket, bracket_label=bracket_label)
    if not samples:
        rep.notes.append("没有样本数据")
        return rep

    picks = {h: int(p) for h, (p, w) in samples.items() if int(p) > 0}
    wins = {h: int(min(w, p)) for h, (p, w) in samples.items() if int(p) > 0}
    if not picks:
        rep.notes.append("没有有效样本（pick 全为 0）")
        return rep

    total = sum(picks.values())
    rates = {h: wins[h] / picks[h] for h in picks}
    rep.n_heroes = len(picks)
    rep.total_picks = total

    # 1) 经验 Beta 先验（只用于收缩展示与池的稳健中心，不用于显著性判定）
    prior = fit_beta_prior(list(rates.values()), [picks[h] for h in picks])
    rep.prior_mean, rep.prior_strength = prior
    shrunk = {h: shrink_with(prior, wins[h], picks[h]) for h in picks}

    # 1b) 数据自洽性校验。
    #     每场比赛必有且只有一个胜者，所以**按出场加权**的总胜率必然等于 50%。
    #     偏离说明数据有缺（例如只统计了部分英雄，或某一方的记录被截断）。
    #     注意这与「按英雄等权平均」不是一回事：英雄出场率不等，
    #     等权平均可以偏离 50%（只有出场次数相同的英雄才会相等）。
    weighted = sum(wins.values()) / total if total else 0.0
    rep.weighted_winrate = weighted
    if abs(weighted - 0.5) > 0.005:
        rep.notes.append(
            f"⚠ 数据自洽性异常：按出场加权的总胜率为 {weighted:.4f}，"
            f"偏离 50% 超过 0.5 个百分点，可能只统计了部分英雄"
        )
    else:
        rep.notes.append(f"数据自洽性校验通过（加权总胜率 {weighted:.4f} ≈ 50%）")

    # 2) 池的稳健中心与离散度。
    #    **必须用原始观测胜率**，不能用收缩值：收缩会把很多英雄拉到同一个先验值上，
    #    使中位数恰好落在那个值、MAD 变成 0，从而在兜底时把所有英雄压到同一把尺子上
    #    （实测过的坑：三个不同胜率的档位算出完全相同的 z）。
    raw_vals = sorted(rates.values())
    rep.pool_mean = sum(raw_vals) / len(raw_vals)
    rep.pool_median = median(raw_vals)
    rep.pool_std = math.sqrt(sum((v - rep.pool_mean) ** 2 for v in raw_vals) / len(raw_vals))
    m = mad(raw_vals)
    if m <= 1e-12:
        # 胜率完全相同（退化情况）：用标准差兜底，再兜一个最小可分辨差异防除零
        m = 0.5 * rep.pool_std if rep.pool_std > 1e-12 else 0.005
    rep.pool_mad = m

    # 3) 逐英雄：效应量 + 区间证据
    signals: List[HeroSignal] = []
    for h in sorted(picks):
        n, w = picks[h], wins[h]
        obs = rates[h]
        lo, hi = wilson_interval(w, n)
        excess = obs - rep.pool_median
        z = robust_z(obs, rep.pool_median, rep.pool_mad)

        # 方向性检验：偏强看下界是否高于中位数，偏弱看上界是否低于中位数
        if excess >= min_excess:
            zstat = wilson_z_against(w, n, rep.pool_median, above=True)
            p = two_sided_p(zstat) / 2.0      # 单侧
        elif excess <= -min_excess:
            zstat = wilson_z_against(w, n, rep.pool_median, above=True)
            p = two_sided_p(zstat) / 2.0
            zstat = -zstat
        else:
            zstat = 0.0
            p = 1.0   # 效应量不够，直接判为不显著
        signals.append(
            HeroSignal(
                hero=h, picks=n, wins=w, winrate=obs,
                ci_low=lo, ci_high=hi, shrunk=shrunk[h],
                pick_share=(n / total if total else 0.0),
                z=z, p_value=max(0.0, min(1.0, p)), excess=excess,
                above_50=(lo > 0.5) if excess > 0 else ((hi < 0.5) if excess < 0 else False),
            )
        )

    # 4) BH-FDR 多重检验校正
    reject = bh_fdr([s.p_value for s in signals], alpha=fdr)

    # 5) 标记
    share_med = median([s.pick_share for s in signals])
    for sig, ok in zip(signals, reject):
        # 双闸门：FDR 通过 + 区间完全落在中位数同侧
        if ok and sig.excess >= min_excess and sig.ci_low > rep.pool_median:
            sig.significant = True
            sig.flags.append("显著偏强")
        elif ok and sig.excess <= -min_excess and sig.ci_high < rep.pool_median:
            sig.significant = True
            sig.flags.append("显著偏弱")

        if sig.significant and sig.z > 0:
            if sig.pick_share >= share_med:
                sig.flags.append("强势且热门")
            if sig.pick_share < share_med * 0.5:
                sig.flags.append("冷门强点")
            if sig.ci_low > 0.5:
                sig.flags.append("保守下界仍高于 50%")
        if sig.significant and sig.z < 0 and sig.ci_high < 0.5:
            sig.flags.append("保守上界仍低于 50%")

        # 样本量提示：z 用的是点估计，样本太少时点估计不可靠，
        # 但区间很宽所以不会被判显著——必须额外提醒，否则容易误读。
        if sig.picks < MIN_RELIABLE_PICKS:
            sig.flags.append(f"样本偏少({sig.picks}场)，结论不可靠")

    signals.sort(key=lambda s: -s.z)
    rep.signals = signals
    rep.outliers_high = [s for s in signals if s.significant and s.z > 0]
    rep.outliers_low = [s for s in signals if s.significant and s.z < 0]

    rep.notes.append(
        f"池中位数 {rep.pool_median:.4f}，稳健离散度(MAD) {rep.pool_mad:.4f}，"
        f"标准差 {rep.pool_std:.4f}"
    )
    rep.notes.append(
        f"判定口径：与中位数差距 ≥ {min_excess:.2%} 且 Wilson 区间完全同侧，"
        f"再做 BH-FDR({fdr:.0%}) 校正"
    )
    rep.notes.append(
        f"结果：显著偏强 {len(rep.outliers_high)} 个、偏弱 {len(rep.outliers_low)} 个"
        f"（共 {rep.n_heroes} 次比较）"
    )
    return rep


def bracket_signals(
    per_bracket: Dict[int, Dict[str, Tuple[int, int]]],
    overall: Dict[str, Tuple[int, int]],
) -> Dict[str, List[Tuple[int, float]]]:
    """跨档位一致性：返回 ``{英雄: [(档位, z)]}``，用于识别「只在某分段强」。

    一个真正突出的英雄应当在**多个档位**都偏强；只在低分段强通常意味着
    它靠对手不会应对，而不是这个版本真的强。
    """
    out: Dict[str, List[Tuple[int, float]]] = {}
    for b, samples in sorted(per_bracket.items()):
        if not samples:
            continue
        rep = analyze(samples, bracket=b)
        for s in rep.signals:
            out.setdefault(s.hero, []).append((b, s.z))
    for h, rows in list(out.items()):
        if h not in overall:
            del out[h]
        else:
            out[h] = sorted(rows)
    return out


def consistency_score(rows: Sequence[Tuple[int, float]]) -> Tuple[float, float]:
    """由「各档位 z」算出 (最低 z, 一致性)。

    * 最低 z（``worst``）：最保守的"到哪都不弱"证据，避免靠单一段位撑起来；
    * 一致性：各档位 z 的**符号一致比例**，1.0 表示每个档位都偏强。
    """
    if not rows:
        return 0.0, 0.0
    zs = [z for _, z in rows]
    worst = min(zs)
    pos = sum(1 for z in zs if z > 0)
    neg = sum(1 for z in zs if z < 0)
    agree = max(pos, neg) / len(zs)
    return worst, agree
