# -*- coding: utf-8 -*-
"""版本突出性分析：命令行报表。

回答「**这个版本有哪些英雄异常突出**」，并给出可核对的数值依据。

数据来源：``data/cache/opendota_hero_stats.json``（OpenDota ``heroStats``）。
它同时提供全体与**分天梯档位**的 pick/win 计数，是唯一能同时支撑
「显著性」与「分段一致性」分析的公开数据。

统计口径见 :mod:`d2a.stats` 与 :mod:`d2a.outliers` 的文档：
不是「按胜率排序」，而是
    效应量（≥1.5pp）+ Wilson 区间同侧 + BH-FDR 多重检验校正
三重闸门，再按「胜率 × 热度 × 分段一致性」给出分类。

用法::

    python tools/analyze_meta.py                     # 控制台表格
    python tools/analyze_meta.py --top 15            # 只看前 15
    python tools/analyze_meta.py --json out.json     # 导出 JSON
    python tools/analyze_meta.py --markdown out.md   # 导出 Markdown 报告
    python tools/analyze_meta.py --bracket 7         # 只看 Divine 段
    python tools/analyze_meta.py --min-excess 0.02   # 提高效应量门槛
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Dict, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.data_loader import BRACKETS  # noqa: E402
from d2a.outliers import analyze_version  # noqa: E402
from d2a.stats import MIN_RELIABLE_PICKS  # noqa: E402

DATA = ROOT / "data"
CACHE = DATA / "cache"
STATS_CACHE = CACHE / "opendota_hero_stats.json"
HEROES_CACHE = CACHE / "opendota_heroes.json"


def _load(path: pathlib.Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "data" in raw:
        return raw["data"]
    return raw


def load_samples() -> Tuple[Dict[str, Tuple[int, int]], Dict[int, Dict[str, Tuple[int, int]]], str]:
    """读缓存，返回 ``(全体样本, 分档位样本, 版本号)``。

    只保留 ``data/heroes.json`` 里认识的英雄：OpenDota 的 heroStats 会包含
    我们英雄库还没有的新英雄，混进来会污染池的统计量（池中位数、MAD）。
    """
    if not STATS_CACHE.exists():
        raise SystemExit(
            f"缺少 {STATS_CACHE}\n请先运行：python tools/refresh_data.py --meta"
        )
    rows = _load(STATS_CACHE)
    known = {
        h["name"]
        for h in json.loads((DATA / "heroes.json").read_text(encoding="utf-8"))["heroes"]
    }
    # hero_id -> 名字（用 OpenDota 官方表，避免依赖 meta.json 的筛选结果）
    id_to_name: Dict[int, str] = {}
    if HEROES_CACHE.exists():
        for h in _load(HEROES_CACHE):
            id_to_name[int(h["id"])] = h["localized_name"]

    overall: Dict[str, Tuple[int, int]] = {}
    per_bracket: Dict[int, Dict[str, Tuple[int, int]]] = {}
    skipped = 0
    for r in rows:
        name = id_to_name.get(int(r.get("id", 0)))
        if not name:
            continue
        if name not in known:
            skipped += 1
            continue
        pub_pick = int(r.get("pub_pick") or 0)
        pub_win = int(r.get("pub_win") or 0)
        if pub_pick > 0:
            overall[name] = (pub_pick, min(pub_win, pub_pick))
        for b in BRACKETS:
            bp = int(r.get(f"{b}_pick") or 0)
            bw = int(r.get(f"{b}_win") or 0)
            if bp > 0:
                per_bracket.setdefault(b, {})[name] = (bp, min(bw, bp))

    patch = "unknown"
    meta_path = DATA / "meta.json"
    if meta_path.exists():
        patch = json.loads(meta_path.read_text(encoding="utf-8")).get("patch", "unknown")
    if skipped:
        print(f"（跳过 {skipped} 个不在英雄库里的英雄）", file=sys.stderr)
    return overall, per_bracket, patch


# --------------------------------------------------------------------------- 渲染
def render_table(rep, top: int = 20, show_weak: bool = True) -> str:
    lines = []
    o = rep.overall
    lines.append("=" * 92)
    lines.append(f"版本突出性分析  patch={rep.patch}  英雄 {o.n_heroes} 个  样本 {o.total_picks:,} 场")
    lines.append("=" * 92)
    lines.append(
        f"池中位数 {o.pool_median:.4f} | 稳健离散 MAD {o.pool_mad:.4f} | 标准差 {o.pool_std:.4f}"
    )
    for n in rep.notes:
        lines.append("· " + n)
    lines.append("")

    def block(title: str, rows, limit: int) -> None:
        lines.append(f"── {title} " + "─" * max(0, 86 - len(title)))
        if not rows:
            lines.append("  （无）")
            lines.append("")
            return
        lines.append(
            f"  {'英雄':<20}{'胜率':>8}{'超出':>8}{'稳健z':>8}{'出场占比':>10}{'样本':>10}  分类"
        )
        for x in rows[:limit]:
            kinds = "、".join(x.kinds) or "—"
            warn = "" if x.reliable else " ⚠样本少"
            lines.append(
                f"  {x.hero:<20}{x.winrate:>8.2%}{x.excess:>+8.2%}{x.z:>+8.2f}"
                f"{x.pick_share:>10.2%}{x.picks:>10,}  {kinds}{warn}"
            )
        lines.append("")

    block("异常突出（显著偏强）", rep.notable, top)
    if show_weak:
        block("异常偏弱（显著偏弱）", rep.weakest, top)

    # 分段一致性：只看显著偏强的
    lines.append("── 分段一致性（只看显著偏强的英雄）" + "─" * 40)
    rows = [x for x in rep.notable if x.bracket_z]
    if not rows:
        lines.append("  （没有可比较的分档位数据）")
    else:
        lines.append(
            f"  {'英雄':<20}{'最低z':>8}{'最高z':>8}{'一致度':>8}{'趋势':>10}  解释"
        )
        for x in rows[:top]:
            trend = "越高分越强" if x.trend_slope > 0.04 else ("越低分越强" if x.trend_slope < -0.04 else "无明显趋势")
            lines.append(
                f"  {x.hero:<20}{x.worst_z:>+8.2f}{x.best_z:>+8.2f}"
                f"{x.agreement:>8.0%}{trend:>10}  {x.summary}"
            )
    lines.append("")
    return "\n".join(lines)


def render_markdown(rep, top: int = 20) -> str:
    o = rep.overall
    L = []
    L.append(f"# 版本突出性分析（patch {rep.patch}）")
    L.append("")
    L.append(
        f"- 覆盖英雄：**{o.n_heroes}** 个，样本量 **{o.total_picks:,}** 场\n"
        f"- 池中位数：**{o.pool_median:.4f}**，稳健离散度 MAD：**{o.pool_mad:.4f}**\n"
        f"- 显著偏强：**{len(rep.notable)}** 个；显著偏弱：**{len(rep.weakest)}** 个"
    )
    L.append("")
    L.append("## 判定口径")
    L.append("")
    L.append("本报告不是「按胜率排序」，判定一个英雄「突出」需要同时通过三道闸门：")
    L.append("")
    L.append("1. **效应量**：与池中位数的胜率差 ≥ 1.5 个百分点（纯统计显著太廉价）；")
    L.append("2. **区间证据**：Wilson 置信区间必须完全位于池中位数同侧；")
    L.append("3. **多重检验**：对全部英雄做 Benjamini–Hochberg FDR 校正。")
    L.append("")
    L.append("另外用 **MAD**（中位数绝对偏差）而不是标准差来度量离散度——"
             "标准差会被极端英雄抬高，从而让极端英雄自己显得不显著。")
    L.append("")
    for n in rep.notes:
        L.append(f"> {n}")
    L.append("")

    def table(title: str, rows) -> None:
        L.append(f"## {title}")
        L.append("")
        if not rows:
            L.append("（无）")
            L.append("")
            return
        L.append("| 英雄 | 胜率 | 相对池中位 | 稳健 z | 出场占比 | 样本 | 分类 |")
        L.append("|---|---:|---:|---:|---:|---:|---|")
        for x in rows[:top]:
            L.append(
                f"| {x.hero} | {x.winrate:.2%} | {x.excess:+.2%} | {x.z:+.2f} "
                f"| {x.pick_share:.2%} | {x.picks:,} | {'、'.join(x.kinds) or '—'} |"
            )
        L.append("")

    table("异常突出（显著偏强）", rep.notable)
    table("异常偏弱（显著偏弱）", rep.weakest)

    L.append("## 分段一致性")
    L.append("")
    L.append("「越高分越强」通常意味着操作上限高；「越低分越强」通常意味着"
             "依赖对手不会应对，照搬到高分局会失效。")
    L.append("")
    rows = [x for x in rep.notable if x.bracket_z]
    if not rows:
        L.append("（没有可比较的分档位数据）")
    else:
        L.append("| 英雄 | 最低 z | 最高 z | 一致度 | 趋势 | 说明 |")
        L.append("|---|---:|---:|---:|---|---|")
        for x in rows[:top]:
            if x.trend_slope > 0.04:
                trend = "越高分越强"
            elif x.trend_slope < -0.04:
                trend = "越低分越强"
            else:
                trend = "无明显趋势"
            L.append(
                f"| {x.hero} | {x.worst_z:+.2f} | {x.best_z:+.2f} "
                f"| {x.agreement:.0%} | {trend} | {x.summary} |"
            )
    L.append("")
    return "\n".join(L)


def to_json(rep) -> dict:
    return {
        "patch": rep.patch,
        "overall": {
            "heroes": rep.overall.n_heroes,
            "total_picks": rep.overall.total_picks,
            "pool_median": round(rep.overall.pool_median, 6),
            "pool_mad": round(rep.overall.pool_mad, 6),
            "pool_std": round(rep.overall.pool_std, 6),
            "outliers_high": len(rep.notable),
            "outliers_low": len(rep.weakest),
        },
        "notes": rep.notes,
        "outliers": [
            {
                "hero": x.hero,
                "picks": x.picks,
                "winrate": round(x.winrate, 6),
                "excess": round(x.excess, 6),
                "z": round(x.z, 4),
                "ci": [round(x.ci_low, 6), round(x.ci_high, 6)],
                "pick_share": round(x.pick_share, 6),
                "significant": x.significant,
                "reliable": x.reliable,
                "kinds": x.kinds,
                "flags": x.flags,
                "worst_z": round(x.worst_z, 4),
                "best_z": round(x.best_z, 4),
                "agreement": round(x.agreement, 4),
                "trend_slope": round(x.trend_slope, 4),
                "bracket_z": {str(k): round(v, 4) for k, v in sorted(x.bracket_z.items())},
                "bracket_winrate": {str(k): round(v, 6) for k, v in sorted(x.bracket_winrate.items())},
                "summary": x.summary,
            }
            for x in rep.outliers
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="版本突出性分析（统计口径，不是简单排序）")
    ap.add_argument("--top", type=int, default=20, help="每个榜单显示多少条（默认 20）")
    ap.add_argument("--json", type=str, default="", help="把完整结果写到该 JSON 路径")
    ap.add_argument("--markdown", type=str, default="", help="导出 Markdown 报告")
    ap.add_argument("--bracket", type=int, default=None, help="只看某个档位（1-8）")
    ap.add_argument("--min-excess", type=float, default=0.015, help="判定突出的最小胜率差（默认 0.015）")
    ap.add_argument("--fdr", type=float, default=0.05, help="BH-FDR 水平（默认 0.05）")
    ap.add_argument("--no-brackets", action="store_true", help="跳过档位分析（更快）")
    ap.add_argument("--quiet", action="store_true", help="只写文件，不打印表格")
    args = ap.parse_args()

    overall, per_bracket, patch = load_samples()
    if args.bracket:
        b = int(args.bracket)
        if b not in per_bracket:
            raise SystemExit(f"档位 {b} 没有数据（可用：{sorted(per_bracket)}）")
        # 只看单档位时，把该档位当"全体"用，输出更直观
        overall = per_bracket[b]
        per_bracket = {b: overall} if not args.no_brackets else {}
        patch = f"{patch} / {BRACKETS.get(b, b)}"
    if args.no_brackets:
        per_bracket = {}

    rep = analyze_version(
        overall,
        per_bracket=per_bracket,
        patch=patch,
        fdr=args.fdr,
        min_excess=args.min_excess,
    )

    if args.json:
        p = pathlib.Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(to_json(rep), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 {p}", file=sys.stderr)
    if args.markdown:
        p = pathlib.Path(args.markdown)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(rep, top=args.top), encoding="utf-8")
        print(f"已写入 {p}", file=sys.stderr)
    if not args.quiet and not (args.json or args.markdown):
        print(render_table(rep, top=args.top))
    elif not args.quiet and (args.json or args.markdown):
        print(render_table(rep, top=args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
