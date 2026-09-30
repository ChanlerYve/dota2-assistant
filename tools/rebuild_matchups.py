# -*- coding: utf-8 -*-
"""从本地缓存离线重建 data/matchups_live.json（不联网）。

为什么需要这个脚本
------------------
早期版本的 ``refresh_data.py`` 有**两个同类的方向性偏差**（见 review 报告 D1）：

1. 把每个英雄的对位按 ``abs(差值)`` **截断到前 14 条**——按显著性截断，等于
   「对手越热门越容易留下」，冷门对手即使被强克制也会被丢掉；
2. 用**绝对**样本阈值（120 场）过滤——同样依赖热度：热门英雄有 87 条对位过线，
   而 Spectre / Meepo / Io 这类英雄**最热的一个对位也只有几十场**，于是整表为空，
   对位因子永久中性。

这里的做法
----------
* **相对阈值**：按该英雄自己的对位分布取分位（默认保留前 60%），不设全局绝对线；
* **绝对地板**：至少 ``--floor`` 场（默认 20），避免 1~2 场的噪声；
* **样本收缩**：``adv = (wins/games - 0.5) * g/(g+k)``，样本越小越向 0 收缩，
  而不是把边直接删掉。这样冷门英雄也有信号，但不会被小样本吹成强克制；
* **不截断**：不设 top-k。

用法::

    python tools/rebuild_matchups.py                    # 默认
    python tools/rebuild_matchups.py --keep-pct 0.8     # 保留更多边
    python tools/rebuild_matchups.py --floor 60         # 更严格的地板
    python tools/rebuild_matchups.py --dry-run          # 只报告不写入

缓存缺失时请先联网跑 ``python tools/refresh_data.py --matchups``。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
CACHE = DATA / "cache"
LIVE = DATA / "matchups_live.json"


def load_json(p: pathlib.Path):
    raw = json.loads(p.read_text(encoding="utf-8"))
    # 兼容两种结构：OpenDota 原生 list，或包装后的 {"data": [...]}
    if isinstance(raw, dict) and "data" in raw:
        return raw["data"]
    return raw


def build_pairs(hero_map: dict, known: set, floor: int, keep_pct: float, shrink_k: float):
    """返回 (pairs, stats)。pairs = {英雄: {对手: 收缩后优势值}}。

    阈值全部**相对该英雄自己的分布**，另外只加一个很小的绝对地板防噪声。
    原因：Elder Titan 这类冷门英雄，它最热的一个对位也只有 21 场，
    任何稍高的绝对线都会让整表为空——这正是 review 报告 D3 的成因。
    低频边保留但被 ``g/(g+k)`` 收缩，实际影响很小，相当于「有信号但低置信」。
    """
    pairs: dict = {}
    stats: dict = {}
    for name in sorted(known):
        hid = next((k for k, v in hero_map.items() if v == name), None)
        if hid is None:
            continue
        f = CACHE / f"opendota_matchups_{hid}.json"
        if not f.exists():
            continue
        rows = load_json(f)
        raw = []
        for r in rows:
            opp = hero_map.get(int(r.get("hero_id", 0)))
            g = int(r.get("games_played") or 0)
            w = int(r.get("wins") or 0)
            if not opp or opp not in known or g <= 0:
                continue
            raw.append((opp, g, w))
        if not raw:
            stats[name] = 0
            continue
        # 相对地板：该英雄自己 games_played 分布的分位（至少 floor 场）
        games_sorted = sorted(g for _, g, _ in raw)
        # 取 (1-keep_pct) 分位作为地板，但不得低于 floor
        idx = max(0, int(len(games_sorted) * (1.0 - keep_pct)))
        rel_floor = max(floor, games_sorted[idx])
        cand = []
        for opp, g, w in raw:
            if g < rel_floor:
                continue
            raw_adv = w / g - 0.5
            shrunk = raw_adv * (g / (g + shrink_k))  # 样本收缩，越小越向 0 靠
            cand.append((opp, g, shrunk))
        if not cand:
            stats[name] = 0
            continue
        cand.sort(key=lambda x: -x[1])
        keep = min(len(cand), 40)  # 只加上限，不做按差值截断
        row = {opp: round(adv, 4) for opp, _, adv in cand[:keep] if abs(adv) >= 0.005}
        if row:
            pairs[name] = dict(sorted(row.items()))
        stats[name] = len(row)
    return pairs, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", type=int, default=5, help="绝对样本地板（默认 5，只用于防噪声）")
    ap.add_argument("--keep-pct", type=float, default=0.6, help="按该英雄自己的样本分布保留前百分之几（默认 0.6）")
    ap.add_argument("--shrink-k", type=float, default=40.0, help="样本收缩强度：g/(g+k)（默认 40）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    args = ap.parse_args()

    hp = CACHE / "opendota_heroes.json"
    if not hp.exists():
        raise SystemExit(f"缺少 {hp}；请先运行 python tools/refresh_data.py --meta")
    hero_map = {int(h["id"]): h["localized_name"] for h in load_json(hp)}
    known = {h["name"] for h in json.loads((DATA / "heroes.json").read_text(encoding="utf-8"))["heroes"]}
    print(f"英雄表: {len(hero_map)} 个（本地英雄库 {len(known)} 个）")

    if not list(CACHE.glob("opendota_matchups_*.json")):
        raise SystemExit("没有 opendota_matchups_*.json 缓存；请先联网跑 refresh_data.py --matchups")

    pairs, stats = build_pairs(hero_map, known, args.floor, args.keep_pct, args.shrink_k)
    edges = sum(len(v) for v in pairs.values())
    counts = [len(v) for v in pairs.values()]
    before = json.loads(LIVE.read_text(encoding="utf-8"))["pairs"] if LIVE.exists() else {}

    print(f"重建结果: {edges} 条对位，覆盖 {len(pairs)} 个英雄")
    if counts:
        print(f"  每英雄对位数: min={min(counts)} 中位={sorted(counts)[len(counts) // 2]} max={max(counts)}")
    print(f"  现有文件: {sum(len(v) for v in before.values())} 条，覆盖 {len(before)} 个英雄")
    zero = sorted(n for n in known if not pairs.get(n))
    print(f"  零对位英雄: {len(zero)} 个" + (f" {zero}" if zero else " ✅"))
    no_cache = sorted(n for n in known if stats.get(n) is None)
    if no_cache:
        print(f"  ! 无缓存英雄（保留人工种子兜底）: {no_cache}")

    if args.dry_run:
        print("--dry-run：未写入")
        return 0

    payload = {
        "schema": 2,
        "source": "opendota/heroes/{id}/matchups",
        "unit": "winrate delta vs 50%, shrunk by g/(g+k)",
        "method": {
            "floor_games": args.floor,
            "keep_pct": args.keep_pct,
            "shrink_k": args.shrink_k,
            "no_top_k_truncation": True,
        },
        "fetched_at": int(time.time()),
        "note": "相对分位阈值 + 样本收缩，不做 top-k 截断（见 review 报告 D1）",
        "pairs": pairs,
    }
    LIVE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {LIVE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
