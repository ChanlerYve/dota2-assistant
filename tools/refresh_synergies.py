# -*- coding: utf-8 -*-
"""在线刷新英雄协同数据（OpenDota Explorer → data/synergies_live.json）。

为什么要这个
------------
「配合」因子此前只依赖人工维护的 35 个英雄 / 81 条边，对绝大多数英雄等于没算——
引擎里的权重会被白白消耗掉（详见 README 的局限说明）。这个脚本用真实的
公开对局数据把协同补全。

数据来源与关键限制（实测结论，不是推测）
----------------------------------------
OpenDota 的 **Explorer**（`/api/explorer?sql=`）是一个对公开库的只读 SQL 入口，
里面有两张关键表：

* ``picks_bans``：2,469,123 条 pick 记录（约 24.7 万场）——**这是规模所在**；
* ``matches``：只有约 25 万行，且**近期窗口极小**（近 30 天仅 414 场）。

所以本工具**不设时间窗**，用全量数据。协同（例如猛犸+虚空）比胜率更跨版本稳定，
因此用全量是可接受的；但这也意味着它混合了多个版本，属于已知取舍。

协同的定义与噪声（重要）
------------------------
    synergy(A,B) = WR(A、B 同队) − (WR_A + WR_B) / 2

即「这两人的组合是否优于各自单打」。**必须做收缩**，原因：

* 公开数据里单配对样本中位数约 1280 场，纯二项噪声标准差 ≈ √(0.25/n) ≈ 1.37%；
* 实测协同的真实标准差约 1.9%，其中噪声占 1.37%——
  真实/噪声方差比约 1.39，**信号存在但很弱**；
* 若直接用原始值，多数配对的数字主要是噪声。

所以本脚本用 James–Stein 式收缩：

    shrunk = raw × n·s² / (n·s² + 0.25)

其中 ``s²`` 是**从数据本身估计**的真实信号方差（观测方差减去平均噪声方差）。
样本越大越接近原始值；n 小则趋近 0（=「没有意见」）。
这与项目里其它地方的贝叶斯收缩是同一套思路。

用法::

    python tools/refresh_synergies.py --report          # 只看统计，不写文件
    python tools/refresh_synergies.py                   # 拉取并写入
    python tools/refresh_synergies.py --min-games 300   # 更严格
    python tools/refresh_synergies.py --chunk-size 16   # 每次查几个英雄（防超时）
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
CACHE = DATA / "cache"
OUT = DATA / "synergies_live.json"
EXPLORER = "https://api.opendota.com/api/explorer"
USER_AGENT = "dota2-assistant/0.2 (personal draft helper; public data only)"

# 低于该绝对值的协同视为「无信号」不写入：这个量级已低于噪声，
# 留着只会让「配合」因子看起来有数据其实是噪声。
MIN_ABS_SYNERGY = 0.002


def explorer(sql: str, timeout: int = 300, retries: int = 5) -> dict:
    """调 Explorer。会重试——该接口实测有 SSL 握手抖动。"""
    url = f"{EXPLORER}?sql={urllib.parse.quote(sql)}"
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001 - 网络/SSL/JSON 都可能
            last = e
            wait = 2 + 3 * i
            print(f"    [重试 {i + 1}/{retries}] {type(e).__name__}: {str(e)[:70]}；{wait}s 后再试", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Explorer 请求连续失败 {retries} 次: {last}")


def load_hero_ids() -> tuple:
    """返回 ``(名字 -> id, id -> 名字)``，用于把 SQL 结果映射回英雄名。"""
    p = CACHE / "opendota_heroes.json"
    if not p.exists():
        raise SystemExit(f"缺少 {p}；请先运行 python tools/refresh_data.py --meta")
    raw = json.loads(p.read_text(encoding="utf-8"))
    rows = raw.get("data", raw) if isinstance(raw, dict) else raw
    name_to_id = {h["localized_name"]: int(h["id"]) for h in rows}
    id_to_name = {v: k for k, v in name_to_id.items()}
    local = {
        h["name"]
        for h in json.loads((DATA / "heroes.json").read_text(encoding="utf-8"))["heroes"]
    }
    # 只保留本地认识的英雄
    name_to_id = {k: v for k, v in name_to_id.items() if k in local}
    id_to_name = {v: k for k, v in name_to_id.items()}
    return name_to_id, id_to_name


def build_query(hero_ids: list, min_games: int) -> str:
    """按 h1 的英雄 id 分批查询，避免一次扫全表导致超时。

    ``a.hero_id IN (...)`` 限定左表的英雄；右表可以是任意英雄，
    这样跨批次的两个英雄也能被覆盖到（每对只会以 id 较小者为 h1 出现一次）。
    """
    ids = ",".join(str(int(i)) for i in hero_ids)
    return f"""
WITH picks AS (
  SELECT pb.match_id, pb.hero_id, pb.team,
         CASE WHEN (pb.team = 0 AND m.radiant_win)
                OR (pb.team = 1 AND NOT m.radiant_win) THEN 1 ELSE 0 END AS win
  FROM picks_bans pb
  JOIN matches m ON m.match_id = pb.match_id
  WHERE pb.is_pick
),
solo AS (
  SELECT hero_id, COUNT(*) AS g, SUM(win) AS w FROM picks GROUP BY hero_id
),
cp AS (
  SELECT a.hero_id AS h1, b.hero_id AS h2, COUNT(*) AS g, SUM(a.win) AS w
  FROM picks a
  JOIN picks b ON a.match_id = b.match_id AND a.team = b.team AND a.hero_id < b.hero_id
  WHERE a.hero_id IN ({ids})
  GROUP BY a.hero_id, b.hero_id
)
SELECT cp.h1, cp.h2, cp.g, cp.w, s1.g AS g1, s1.w AS w1, s2.g AS g2, s2.w AS w2
FROM cp
JOIN solo s1 ON s1.hero_id = cp.h1
JOIN solo s2 ON s2.hero_id = cp.h2
WHERE cp.g >= {int(min_games)}
"""


def fetch_pairs(hero_ids: list, min_games: int, chunk_size: int) -> tuple:
    """拉取配对数据，返回 ``(rows, failed_hero_ids)``。

    策略（按顺序降级，尽量少请求、并在失败时能补齐）：
      1. 若 ``chunk_size<=0``：先尝试**一次查完**所有英雄——实测单次全量约 10~15s，
         比把 2.5M 行的 picks 表扫 8 遍更省时，也少 8 倍的失败面；
      2. 单次失败或指定了 chunk_size：按批查询；
      3. 最后对失败的批次**再补一轮**（Explorer 会 SSL 抖动，重试常常就好了）。

    返回失败批次的英雄 id，便于上层明确报告"覆盖不全"而不是假装完整。
    """
    rows: dict = {}

    def absorb(got: list) -> int:
        n = 0
        for r in got:
            rows[(int(r["h1"]), int(r["h2"]))] = r
            n += 1
        return n

    def run_batch(ids: list, label: str) -> bool:
        t0 = time.time()
        try:
            d = explorer(build_query(ids, min_games))
        except Exception as e:  # noqa: BLE001
            print(f"  {label} 失败: {e}", file=sys.stderr)
            return False
        got = d.get("rows") or []
        absorb(got)
        print(f"  {label}：{len(got):>5} 行，{time.time() - t0:.1f}s（累计 {len(rows)}）")
        return True

    failed_ids: list = []
    if chunk_size <= 0:
        print(f"英雄 {len(hero_ids)} 个，先尝试单次全量查询…")
        if not run_batch(hero_ids, "全量"):
            print("  单次全量失败，降级为分批查询")
            chunk_size = 16

    if chunk_size > 0:
        chunks = [hero_ids[i:i + chunk_size] for i in range(0, len(hero_ids), chunk_size)]
        print(f"英雄 {len(hero_ids)} 个，分 {len(chunks)} 批查询（每批 {chunk_size} 个）")
        pending = []
        for idx, chunk in enumerate(chunks, 1):
            if not run_batch(chunk, f"第 {idx}/{len(chunks)} 批"):
                pending.append((idx, chunk))
        # 补一轮：SSL 抖动通常重试即可
        for idx, chunk in pending:
            print(f"  补跑第 {idx} 批…")
            if not run_batch(chunk, f"第 {idx} 批(补)"):
                failed_ids.extend(chunk)

    return list(rows.values()), failed_ids


def compute_synergies(rows: list, id_to_name: dict, min_games: int) -> tuple:
    """算出协同并做收缩。返回 ``(pairs, meta)``。"""
    raw = []
    for r in rows:
        h1, h2 = int(r["h1"]), int(r["h2"])
        name1 = id_to_name.get(h1)
        name2 = id_to_name.get(h2)
        if not name1 or not name2:
            continue
        g = int(r["g"])
        if g < min_games:
            continue
        wr_pair = int(r["w"]) / g
        wr1 = int(r["w1"]) / int(r["g1"])
        wr2 = int(r["w2"]) / int(r["g2"])
        raw.append((name1, name2, g, wr_pair - (wr1 + wr2) / 2.0))

    if not raw:
        return {}, {"reason": "没有拿到任何配对数据"}

    vals = [v for _, _, _, v in raw]
    obs_var = statistics.pvariance(vals)
    # 每个配对的二项噪声方差（用 p=0.5 取上界，偏保守）
    noise_vars = [0.25 / g for _, _, g, _ in raw]
    mean_noise = statistics.mean(noise_vars)
    # James–Stein：从观测方差里扣掉噪声，得到真实信号方差
    signal_var = max(obs_var - mean_noise, 1e-9)

    pairs: dict = {}
    kept = 0
    for name1, name2, g, v in raw:
        shrink = (g * signal_var) / (g * signal_var + 0.25)
        shrunk = v * shrink
        if abs(shrunk) < MIN_ABS_SYNERGY:
            continue
        r = round(shrunk, 4)
        pairs.setdefault(name1, {})[name2] = r
        pairs.setdefault(name2, {})[name1] = r
        kept += 1

    meta = {
        "raw_pairs": len(raw),
        "kept_pairs": kept,
        "obs_std": round(obs_var ** 0.5, 5),
        "noise_std": round(mean_noise ** 0.5, 5),
        "signal_std": round(signal_var ** 0.5, 5),
        "snr_ratio": round((obs_var / mean_noise) ** 0.5, 3) if mean_noise else 0.0,
        "median_games": int(statistics.median([g for _, _, g, _ in raw])),
        "min_abs_synergy": MIN_ABS_SYNERGY,
    }
    return pairs, meta


def main() -> int:
    ap = argparse.ArgumentParser(description="刷新英雄协同数据（OpenDota Explorer）")
    ap.add_argument("--min-games", type=int, default=200, help="单配对最小样本（默认 200）")
    ap.add_argument("--chunk-size", type=int, default=0,
                    help="每次查询覆盖几个英雄；0=先试单次全量（默认），失败自动降级分批")
    ap.add_argument("--report", action="store_true", help="只打印统计，不写文件")
    ap.add_argument("--top", type=int, default=15, help="报告中显示多少个最强/最弱协同")
    args = ap.parse_args()

    name_to_id, id_to_name = load_hero_ids()
    print(f"本地英雄库匹配到 {len(name_to_id)} 个 OpenDota 英雄 id")

    rows, failed_ids = fetch_pairs(list(name_to_id.values()), args.min_games, args.chunk_size)
    print(f"\n共取回 {len(rows)} 个配对（样本 >= {args.min_games}）")
    if failed_ids:
        names = [id_to_name.get(i, str(i)) for i in failed_ids]
        print(f"⚠ 有 {len(failed_ids)} 个英雄的批次最终仍失败，覆盖不完整：{names}")
    if not rows:
        print("没有数据，退出", file=sys.stderr)
        return 1

    pairs, meta = compute_synergies(rows, id_to_name, args.min_games)
    heroes_covered = len(pairs)
    edges = sum(len(v) for v in pairs.values()) // 2

    print()
    print("== 统计口径 ==")
    print(f"  原始配对 {meta['raw_pairs']} 个，保留 {meta['kept_pairs']} 个"
          f"（|协同| < {meta['min_abs_synergy']} 视为无信号）")
    print(f"  观测标准差 {meta['obs_std']:.4f} | 二项噪声 {meta['noise_std']:.4f} "
          f"| 推出真实信号 {meta['signal_std']:.4f}")
    print(f"  信噪比 {meta['snr_ratio']:.2f}（>1 说明有真实信号）")
    print(f"  单配对样本中位数 {meta['median_games']} 场")
    print()
    print(f"== 覆盖 ==")
    print(f"  英雄 {heroes_covered} 个（此前人工种子仅 35 个）")
    print(f"  无向边 {edges} 条（此前 81 条）")

    flat = []
    for a, row in pairs.items():
        for b, v in row.items():
            if a < b:
                flat.append((v, a, b))
    flat.sort(reverse=True)
    print()
    print(f"== 最强协同 top {args.top} ==")
    for v, a, b in flat[:args.top]:
        print(f"  {v:+.4f}  {a} + {b}")
    print(f"== 最弱协同（互相拖累）top {args.top} ==")
    for v, a, b in flat[-args.top:]:
        print(f"  {v:+.4f}  {a} + {b}")

    if args.report:
        print("\n--report：未写入文件")
        return 0

    payload = {
        "schema": 1,
        "source": "opendota/explorer picks_bans (all-time, no time window)",
        "unit": "winrate of pair minus average of individual winrates, shrunk",
        "method": {
            "synergy": "WR(A,B together) - (WR_A + WR_B)/2",
            "shrinkage": "James-Stein: n*s^2/(n*s^2+0.25), s^2 estimated from data",
            "min_games": args.min_games,
            "min_abs_synergy": MIN_ABS_SYNERGY,
            "time_window": "none (all rows in public Explorer)",
        },
        "stats": meta,
        "fetched_at": int(time.time()),
        "note": "由 tools/refresh_synergies.py 生成；全量数据混合多个版本，属已知取舍",
        "pairs": pairs,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
