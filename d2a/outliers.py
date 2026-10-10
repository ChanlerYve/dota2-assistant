# -*- coding: utf-8 -*-
"""版本突出性分析：从数值层面找出「这个版本里异常突出」的英雄。

回答什么问题
------------
「**当前版本有没有哪个英雄特别离谱？**」——不是把所有英雄按胜率排个序
（那只是排序，不是分析），而是回答三件更有用的事：

1. **谁真的偏离了英雄池**，偏离多少（相对池中位数 + 稳健 z + 保守区间）；
2. **偏离是否可信**（效应量、区间证据、多重检验校正三道闸门）；
3. **偏离属于哪一类**——这决定了怎么应对：

   * ``强势且热门``   ：又强又常被拿 → 版本答案，值得练，也值得针对；
   * ``冷门强点``     ：很强但没人拿 → 信息差，最容易上分；
   * ``分段限定``     ：只在某个分数段强 → 别照抄高分局出装/打法；
   * ``高分段更强``   ：随段位升高越强 → 操作上限高，低分局反而别玩；
   * ``低分段更强``   ：随段位降低越强 → 靠对手不会应对，高分局别选。

为什么不能只看胜率
------------------
因为 Dota 的**每场比赛必有且只有一个胜者**，所有英雄胜率在数学上被迫围绕
50% 分布（见 :mod:`d2a.stats` 的说明）。所以：

* 「胜率 52%」本身不说明强，要看**它在英雄池里偏了几个离散单位**；
* 出场率是**独立的第二个维度**：胜率高 + 出场率高 = 真强势；
  胜率高 + 出场率极低 = 可能只是小众群体的熟练度红利（或样本偏差）；
* 只看全体平均会**掩盖版本答案**——同一个英雄在不同分段可能强弱相反。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .data_loader import BRACKETS
from .stats import (
    MIN_RELIABLE_PICKS,
    MetaReport,
    analyze,
    consistency_score,
    median,
)

# 判定「突出」的最小效应量（与池中位数的胜率差），默认 1.5 个百分点。
# 样本量在 Dota 里动辄几十万，纯统计显著太廉价，所以必须设一个实际意义的门槛。
DEFAULT_MIN_EXCESS = 0.015

# 档位标签（用于报告文案）。8(Immortal) 在公开数据里恒为 0，会被自动跳过。
BRACKET_ORDER: Tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8)

# 突出性分类
KIND_DOMINANT = "强势且热门"
KIND_NICHE = "冷门强点"
KIND_BRACKET_LOCKED = "分段限定"
KIND_SCALES_UP = "高分段更强"
KIND_SCALES_DOWN = "低分段更强"
KIND_STRONG = "强势"
KIND_WEAK = "弱势"


@dataclass
class HeroOutlier:
    """一个英雄的完整突出性画像。"""

    hero: str
    # 全体的突出性
    picks: int = 0
    winrate: float = 0.0
    excess: float = 0.0            # 相对池中位数
    z: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 1.0
    pick_share: float = 0.0
    significant: bool = False
    # 分档位
    bracket_z: Dict[int, float] = field(default_factory=dict)
    bracket_winrate: Dict[int, float] = field(default_factory=dict)
    worst_z: float = 0.0           # 各档位里最差的那个 z（保守证据）
    best_z: float = 0.0
    agreement: float = 0.0         # 各档位方向一致比例 0~1
    trend_slope: float = 0.0       # z 随段位上升的斜率（>0 = 越高分越强）
    # 结论
    kinds: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    summary: str = ""

    @property
    def reliable(self) -> bool:
        return self.picks >= MIN_RELIABLE_PICKS


@dataclass
class VersionReport:
    """整个版本的突出性分析结果。"""

    patch: str = "unknown"
    overall: Optional[MetaReport] = None
    brackets: Dict[int, MetaReport] = field(default_factory=dict)
    outliers: List[HeroOutlier] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def notable(self) -> List[HeroOutlier]:
        """只看真正突出的（显著偏强）。"""
        return [o for o in self.outliers if o.significant and o.excess > 0]

    @property
    def weakest(self) -> List[HeroOutlier]:
        return [o for o in self.outliers if o.significant and o.excess < 0]


# --------------------------------------------------------------------------- 工具
def _slope(points: Sequence[Tuple[int, float]]) -> float:
    """对 (档位, z) 做最小二乘斜率，衡量「随段位上升 z 是涨还是跌」。"""
    if len(points) < 2:
        return 0.0
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    num = sum((x - mx) * (y - my) for x, y in points)
    den = sum((x - mx) ** 2 for x, _ in points)
    return num / den if den > 1e-12 else 0.0


def _classify(o: HeroOutlier, share_median: float) -> List[str]:
    """给出突出性分类（可能同时命中多个，比如又热门又在高分段更强）。"""
    kinds: List[str] = []
    if not o.significant:
        return kinds
    if o.excess > 0:
        # 热度维度：出场占比显著高于中位数 = 被广泛认可
        if o.pick_share >= share_median:
            kinds.append(KIND_DOMINANT)
        elif o.pick_share < share_median * 0.5:
            kinds.append(KIND_NICHE)
        # 档位维度：一致性低说明只是某分段的现象
        if o.bracket_z and o.agreement < 0.75:
            kinds.append(KIND_BRACKET_LOCKED)
        # 趋势维度：只在足够多档位且趋势明显时才下结论
        if len(o.bracket_z) >= 4 and abs(o.trend_slope) >= 0.04:
            kinds.append(KIND_SCALES_UP if o.trend_slope > 0 else KIND_SCALES_DOWN)
        if not kinds:
            kinds.append(KIND_STRONG)
    else:
        kinds.append(KIND_WEAK)
    return kinds


def _summarize(o: HeroOutlier) -> str:
    """一句话结论，供报表与 UI 直接用。"""
    if not o.significant:
        if o.picks < MIN_RELIABLE_PICKS:
            return f"样本不足（{o.picks} 场），无法判断"
        return "处于正常范围"
    direction = "偏强" if o.excess > 0 else "偏弱"
    parts = [
        f"胜率 {o.winrate:.2%}（{direction} {abs(o.excess):.2%}，稳健 z={o.z:+.2f}）"
    ]
    if o.kinds:
        parts.append("、".join(o.kinds))
    if o.bracket_z:
        bz = o.bracket_z
        hi = max(bz, key=lambda b: bz[b])
        lo = min(bz, key=lambda b: bz[b])
        parts.append(
            f"{BRACKETS.get(hi, hi)} 最高({bz[hi]:+.2f})、"
            f"{BRACKETS.get(lo, lo)} 最低({bz[lo]:+.2f})"
        )
    return "；".join(parts)


def outlier_lookup(
    rep: "VersionReport",
    heroes: Optional[Sequence[str]] = None,
) -> Dict[str, dict]:
    """把 :class:`VersionReport` 压成引擎可用的查找表 ``{英雄: {...}}``。

    只导出**显著**的条目——不显著的去干扰反而会误导；
    ``heroes`` 可以再按英雄库过滤一遍，避免把未知英雄塞进引擎。
    """
    allow = set(heroes) if heroes else None
    out: Dict[str, dict] = {}
    for o in rep.outliers:
        if not o.significant:
            continue
        if allow is not None and o.hero not in allow:
            continue
        out[o.hero] = {
            "excess": o.excess,
            "z": o.z,
            "significant": True,
            "kinds": list(o.kinds),
            "summary": o.summary,
        }
    return out


def load_outlier_lookup(
    stats_cache,
    hero_names: Sequence[str],
    patch: str = "unknown",
    min_excess: float = DEFAULT_MIN_EXCESS,
    fdr: float = 0.05,
) -> Dict[str, dict]:
    """从 OpenDota ``heroStats`` 缓存直接算出查找表（读文件 + 分析，约几毫秒）。

    这样 CLI / Web / 悬浮窗三处不必各自重复实现取数与统计。
    """
    import json
    import pathlib

    p = pathlib.Path(stats_cache)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    rows = raw.get("data", raw) if isinstance(raw, dict) else raw

    # 英雄 ID -> 名字：优先用同目录的 opendota_heroes.json
    id2name: Dict[int, str] = {}
    hc = p.parent / "opendota_heroes.json"
    if hc.exists():
        hraw = json.loads(hc.read_text(encoding="utf-8"))
        for h in (hraw.get("data", hraw) if isinstance(hraw, dict) else hraw):
            id2name[int(h["id"])] = h["localized_name"]

    known = set(hero_names)
    overall: Dict[str, Tuple[int, int]] = {}
    per_bracket: Dict[int, Dict[str, Tuple[int, int]]] = {}
    for r in rows:
        nm = id2name.get(int(r.get("id", 0)))
        if nm not in known:
            continue
        pk = int(r.get("pub_pick") or 0)
        if pk > 0:
            overall[nm] = (pk, min(int(r.get("pub_win") or 0), pk))
        for b in BRACKET_ORDER:
            bp = int(r.get(f"{b}_pick") or 0)
            if bp > 0:
                per_bracket.setdefault(b, {})[nm] = (bp, min(int(r.get(f"{b}_win") or 0), bp))
    if not overall:
        return {}
    rep = analyze_version(overall, per_bracket=per_bracket or None,
                          patch=patch, fdr=fdr, min_excess=min_excess)
    return outlier_lookup(rep, heroes=hero_names)


# --------------------------------------------------------------------------- 主流程
def analyze_version(
    overall: Dict[str, Tuple[int, int]],
    per_bracket: Optional[Dict[int, Dict[str, Tuple[int, int]]]] = None,
    patch: str = "unknown",
    fdr: float = 0.05,
    min_excess: float = DEFAULT_MIN_EXCESS,
    top_n: Optional[int] = None,
) -> VersionReport:
    """做完整的版本突出性分析。

    ``overall``     : ``{英雄: (pick, win)}``，全体数据；
    ``per_bracket`` : ``{档位: {英雄: (pick, win)}}``，可选，用于一致性与趋势；
    ``min_excess``  : 判定突出的最小胜率差（默认 1.5pp）；
    ``top_n``       : 只返回前 N 个（None = 全部，便于 UI 自己分页）。
    """
    rep = VersionReport(patch=patch)
    rep.overall = analyze(overall, patch=patch, bracket_label="全体",
                          fdr=fdr, min_excess=min_excess)
    base = {s.hero: s for s in rep.overall.signals}
    if not base:
        rep.notes.append("全体数据为空，无法分析")
        return rep

    share_median = median([s.pick_share for s in rep.overall.signals]) if rep.overall.signals else 0.0

    # 把全体分析的说明（含数据自洽性校验、池统计量）带上来，
    # 否则这些关键口径信息在版本报告里会丢失。
    rep.notes.extend(rep.overall.notes)

    # 分档位分析
    bracket_z: Dict[str, Dict[int, float]] = {}
    bracket_wr: Dict[str, Dict[int, float]] = {}
    bracket_excess: Dict[str, Dict[int, float]] = {}
    if per_bracket:
        for b in sorted(per_bracket):
            samples = per_bracket[b]
            if not samples:
                continue
            br = analyze(samples, patch=patch, bracket=b,
                         bracket_label=BRACKETS.get(b, str(b)),
                         fdr=fdr, min_excess=min_excess)
            rep.brackets[b] = br
            for s in br.signals:
                # 只保留样本量足够的档位结论，避免小样本档位污染一致性判断
                if s.picks >= MIN_RELIABLE_PICKS:
                    bracket_z.setdefault(s.hero, {})[b] = s.z
                    bracket_wr.setdefault(s.hero, {})[b] = s.winrate
                    # 用**该档位内相对中位数**的优势做趋势判断。
                    # 不能直接用 z：z = excess / 固定离散度，是 excess 的线性变换，
                    # 用它算斜率只会得到常数（踩过这个坑）。
                    bracket_excess.setdefault(s.hero, {})[b] = s.excess
        rep.notes.append(
            f"分档位分析覆盖 {len(rep.brackets)} 个档位："
            + "、".join(BRACKETS.get(b, str(b)) for b in sorted(rep.brackets))
        )
    else:
        rep.notes.append("未提供分档位数据，跳过一致性与趋势维度")

    # 组装画像
    out: List[HeroOutlier] = []
    for hero, sig in base.items():
        o = HeroOutlier(
            hero=hero, picks=sig.picks, winrate=sig.winrate, excess=sig.excess,
            z=sig.z, ci_low=sig.ci_low, ci_high=sig.ci_high,
            pick_share=sig.pick_share, significant=sig.significant,
            flags=list(sig.flags),
        )
        bz = bracket_z.get(hero) or {}
        o.bracket_z = dict(bz)
        o.bracket_winrate = dict(bracket_wr.get(hero) or {})
        if bz:
            rows = sorted(bz.items())
            o.worst_z, o.agreement = consistency_score(rows)
            o.best_z = max(bz.values())
        # 趋势用「各档位相对优势」而不是 z（见上面注释）
        be = bracket_excess.get(hero) or {}
        if len(be) >= 2:
            o.trend_slope = _slope(sorted(be.items()))
        o.kinds = _classify(o, share_median)
        o.summary = _summarize(o)
        out.append(o)

    # 排序：先显著偏强，再按稳健 z 降序
    out.sort(key=lambda x: (not (x.significant and x.excess > 0), -x.z))
    rep.outliers = out[:top_n] if top_n else out

    # 说明文案统一由 analyze() 提供（口径与结果都已包含），
    # 这里不再重复追加，避免同一句话出现两次。
    return rep
