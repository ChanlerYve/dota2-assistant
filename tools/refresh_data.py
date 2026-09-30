# -*- coding: utf-8 -*-
"""在线刷新：从公开 API 拉取版本强度与真实对位胜率，覆盖/补充内置数据。

数据来源（全部是公开只读接口，不涉及游戏进程）
--------------------------------------------
* OpenDota ``/api/heroStats``            -> 各英雄当前版本胜率、出场率
* OpenDota ``/api/heroes/{id}/matchups`` -> 每个英雄对各英雄的真实对局优势（样本加权）

用法::

    python tools/refresh_data.py --meta            # 只更新版本强度（快，推荐）
    python tools/refresh_data.py --meta --matchups # 同时用真实数据覆盖克制矩阵（慢）
    python tools/refresh_data.py --heroes          # 用官方英雄表校对英雄名清单

说明：内置的人工克制矩阵是「离线兜底」；这里拉到的真实数据一旦存在，
引擎会优先使用（``data/matchups_live.json`` 的优先级高于 ``matchups.json``）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.steam_api import ApiError, PublicDataClient  # noqa: E402

DATA = ROOT / "data"
CACHE = DATA / "cache"


def fetch_meta(client: PublicDataClient, min_games_pct: float = 0.0) -> dict:
    """取版本强度，并**同时保留各天梯档位的胜率**。

    OpenDota 的 heroStats 自带 ``1_pick/1_win`` … ``8_pick/8_win``，
    1~8 对应 Herald→Immortal（实测 8 恒为 0，所以高分段实际最高到 7 Divine）。
    这些字段让「高分段切片」不需要额外数据源——全体平均会掩盖版本答案，
    而某个英雄可能全体 48%、Divine 段 54%。
    """
    rows = client.opendota_hero_stats()
    heroes = {int(h["id"]): h["localized_name"] for h in client.opendota_heroes()}
    out = {}
    for r in rows:
        name = heroes.get(int(r.get("id", 0)))
        if not name:
            continue
        # OpenDota 的 heroStats 直接给出各分档胜率，公开对局用 pub 档
        picks = int(r.get("pub_pick") or 0)
        wins = int(r.get("pub_win") or 0)
        if picks <= 0:
            # 回退到 pro/合计字段
            picks = int(r.get("pro_pick") or 0)
            wins = int(r.get("pro_win") or 0)
        if picks <= 0:
            continue
        brackets = {}
        for b in range(1, 9):
            bp = int(r.get(f"{b}_pick") or 0)
            bw = int(r.get(f"{b}_win") or 0)
            # 只保留有样本的档位，别在数据里塞一堆 0
            if bp > 0:
                brackets[str(b)] = [bp, min(bw, bp)]
        rec = {
            "winrate": round(wins / picks, 4),
            "pickrate": picks,
            "pro_pick": int(r.get("pro_pick") or 0),
            "pro_win": int(r.get("pro_win") or 0),
            "source": "opendota/heroStats",
        }
        if brackets:
            rec["brackets"] = brackets
        out[name] = rec
    # 出场率归一化（0~1，用于 UI 展示热度）
    total = sum(v["pickrate"] for v in out.values()) or 1
    for v in out.values():
        v["pickrate_pct"] = round(v["pickrate"] / total, 5)
    return out


def fetch_matchups(client: PublicDataClient, hero_map: dict, min_sample: int = 300) -> dict:
    """拉取每个英雄对各英雄的真实优势值。

    OpenDota 的 matchups 返回 ``games_played`` / ``wins``：即「该英雄面对这个对手时」
    的胜场与场次，可以直接换算成优势值 ``wins/games - 0.5``。

    **只按样本量过滤，绝不按差值大小截断**。早先的实现会按 ``abs(adv)`` 取前 14 条，
    那等于按显著性截断：对手越热门（样本越大、差值越稳），这条克制关系越容易留下；
    冷门对手哪怕被强克制也会被丢掉，结果让推荐系统性偏向热门英雄。
    每条边只有两个 float，全量保存的体积完全可以接受。
    """
    id_by_name = {v: k for k, v in hero_map.items()}
    pairs: dict = {}
    for i, (name, hid) in enumerate(sorted(id_by_name.items())):
        try:
            rows = client.opendota_matchups(int(hid))
        except ApiError as e:
            print(f"  ! {name}: {e}", file=sys.stderr)
            continue
        row: dict = {}
        for r in rows:
            opp = hero_map.get(int(r.get("hero_id", 0)))
            g = int(r.get("games_played") or 0)
            w = int(r.get("wins") or 0)
            if not opp or g < min_sample or g <= 0:
                continue
            adv = w / g - 0.5
            if abs(adv) >= 0.01:
                row[opp] = round(adv, 4)
        if row:
            pairs[name] = dict(sorted(row.items()))
        if i % 10 == 0:
            print(f"  已处理 {i + 1}/{len(id_by_name)}: {name}", flush=True)
    return pairs


def fetch_patch(client: PublicDataClient) -> tuple:
    """取当前版本号（如 ``7.39c``）与发布日期。

    OpenDota 的 ``constants/patch`` 是一个 ``[{name, date, id}, ...]`` 列表，
    取 ``date`` 最新的那一条。拿不到就返回 ``("unknown", "")``，
    绝不编造版本号（早先这里恒为字符串 ``"current"``，用户无法判断数据是否过期）。
    """
    try:
        rows = client.get_json(
            "https://api.opendota.com/api/constants/patch", cache_key="opendota_patches", min_interval=0.2
        )
    except ApiError:
        return "unknown", ""
    if not isinstance(rows, list) or not rows:
        return "unknown", ""
    dated = [r for r in rows if isinstance(r, dict) and r.get("date")]
    if not dated:
        return "unknown", ""
    latest = max(dated, key=lambda r: str(r.get("date")))
    return str(latest.get("name") or "unknown"), str(latest.get("date") or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", action="store_true", help="更新版本强度 data/meta.json")
    ap.add_argument("--matchups", action="store_true", help="更新真实对位 data/matchups_live.json")
    ap.add_argument("--heroes", action="store_true", help="校对英雄名清单")
    ap.add_argument("--min-sample", type=int, default=300, help="对位最小样本量")
    ap.add_argument("--offline", action="store_true", help="只用缓存，不发起网络请求")
    args = ap.parse_args()

    if not (args.meta or args.matchups or args.heroes):
        args.meta = True

    client = PublicDataClient(CACHE, ttl=12 * 3600)
    if args.offline:
        print("离线模式：仅使用本地缓存")

    # 英雄 ID -> 名字的映射（后续几个接口都要用）
    hero_map: dict = {}
    try:
        hero_map = {int(h["id"]): h["localized_name"] for h in client.opendota_heroes()}
        print(f"英雄表: {len(hero_map)} 个")
    except ApiError as e:
        print(f"! 无法获取英雄表: {e}", file=sys.stderr)
        if args.matchups:
            print("  跳过对位刷新（需要英雄表做 ID 映射）")
            args.matchups = False

    if args.heroes and hero_map:
        local = {h["name"] for h in json.loads((DATA / "heroes.json").read_text(encoding="utf-8"))["heroes"]}
        remote = set(hero_map.values())
        missing = sorted(remote - local)
        extra = sorted(local - remote)
        print(f"官方英雄 {len(remote)} 个；本地缺少 {len(missing)} 个: {missing[:20]}")
        print(f"本地多出（可能是别名/拼写差异） {len(extra)} 个: {extra[:20]}")
        unknown = sorted(local - remote)
        if unknown:
            (DATA / "unknown_heroes.json").write_text(
                json.dumps(unknown, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    if args.meta:
        try:
            meta = fetch_meta(client)
        except ApiError as e:
            print(f"! 版本强度刷新失败: {e}", file=sys.stderr)
            meta = {}
        if meta:
            patch, patch_date = fetch_patch(client)
            payload = {
                "schema": 1,
                "source": "opendota/heroStats",
                "fetched_at": int(time.time()),
                "patch": patch,
                "patch_date": patch_date,
                "heroes": meta,
            }
            (DATA / "meta.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            top = sorted(meta.items(), key=lambda kv: -kv[1]["winrate"])[:5]
            print(f"已写入 data/meta.json（{len(meta)} 个英雄，版本 {patch}）")
            print("  当前胜率最高: " + "、".join(f"{n} {v['winrate']:.1%}" for n, v in top))

    if args.matchups and hero_map:
        print("拉取真实对位数据（每个英雄一次请求，请耐心等待）...")
        pairs = fetch_matchups(client, hero_map, min_sample=args.min_sample)
        if pairs:
            payload = {
                "schema": 1,
                "source": "opendota/heroes/{id}/matchups",
                "unit": "winrate delta vs 50%",
                "min_sample": args.min_sample,
                "fetched_at": int(time.time()),
                "pairs": pairs,
            }
            (DATA / "matchups_live.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            edges = sum(len(v) for v in pairs.values())
            print(f"已写入 data/matchups_live.json（{edges} 条对位，覆盖 {len(pairs)} 个英雄）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
