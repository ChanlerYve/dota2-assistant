# -*- coding: utf-8 -*-
"""赛后复盘闭环：把真实对局结果增量回写英雄池。

为什么需要它
------------
引擎里的熟练度用的是贝叶斯收缩，本身就具备「随真实战绩自我校准」的数学形式；
缺的只是把结果灌回去的管道。手工维护英雄池很快就会过期（版本、手感、英雄改动），
这个脚本让你每次打完几局跑一次就自动跟上。

怎么拿到「位置」
----------------
OpenDota 的 ``players/{id}/matches`` **不返回位置**，但单场详情
``matches/{id}`` 里有 ``position_est``（1~5 号位）——已实测可用。
所以流程是「先拉列表 → 按 match_id 水位筛出新对局 → 逐场取详情 → 按位置聚合」。

配额提示：每场详情是一次请求（免费 60/分钟、3000/天），所以默认只回溯
``--limit`` 局；已经缓存过的比赛不会重复请求。

用法::

    python tools/sync_results.py --account-id 12345678 --probe   # 只看，不写
    python tools/sync_results.py --account-id 12345678           # 同步最近 20 局
    python tools/sync_results.py --steam 76561198... --limit 50  # 用 64 位 ID
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.config import Config, default_config_path  # noqa: E402
from d2a.data_loader import HeroBook, PlayerHeroStat  # noqa: E402
from d2a.steam_api import ApiError, PublicDataClient  # noqa: E402
from d2a.steam_id import parse_steam_input  # noqa: E402

DATA = ROOT / "data"
CACHE = DATA / "cache"


def position_of(match: dict, account_id: int):
    """从单场详情里取出「我」这一局玩了什么、赢没赢、打几号位。

    返回 ``(hero_id, win, position, lane_role)``，取不到位置时 position 为 None。
    位置优先用 ``position_est``；为空的旧对局用 ``lane_role`` 粗推
    （1=优势路，2=中路，3=劣势路 —— 这只是「路」，比 1~5 号位粗）。
    """
    my_slot = None
    for p in match.get("players") or []:
        if int(p.get("account_id") or 0) == int(account_id):
            my_slot = p
            break
    if my_slot is None:
        return None, False, None, None

    hero_id = int(my_slot.get("hero_id") or 0)
    win = bool(my_slot.get("win")) or bool(
        (match.get("radiant_win") and my_slot.get("isRadiant"))
        or ((not match.get("radiant_win")) and (not my_slot.get("isRadiant")))
    )
    pos = my_slot.get("position_est")
    try:
        pos = int(pos) if pos else None
    except (TypeError, ValueError):
        pos = None
    lane_role = my_slot.get("lane_role")
    return hero_id, win, pos, lane_role


def main() -> int:
    ap = argparse.ArgumentParser(description="把真实对局结果增量回写到英雄池")
    ap.add_argument("--account-id", type=int, default=0, help="Steam32 位 account_id")
    ap.add_argument("--steam", type=str, default="", help="也可给 64 位 ID / 主页链接 / STEAM_x:y:z")
    ap.add_argument("--limit", type=int, default=20, help="回溯最近多少局（每局一次详情请求）")
    ap.add_argument("--probe", action="store_true", help="只显示识别结果，不写配置")
    ap.add_argument("--config", type=str, default=None, help="配置文件路径")
    ap.add_argument("--offline", action="store_true", help="只用缓存，不发起网络请求")
    args = ap.parse_args()

    account_id = args.account_id
    if not account_id and args.steam:
        account_id = parse_steam_input(args.steam)
    cfg_path = pathlib.Path(args.config) if args.config else None
    cfg = Config.load(cfg_path)
    if not account_id and cfg.steam_id:
        account_id = parse_steam_input(cfg.steam_id)
    if not account_id:
        print("需要账号：--account-id <32位ID> 或 --steam <64位ID/链接>，", file=sys.stderr)
        print("或先在 config.json 里填 steam_id。", file=sys.stderr)
        return 2

    book = HeroBook.load()
    client = PublicDataClient(CACHE, ttl=(10 ** 9 if args.offline else 6 * 3600))

    # 1) 现有英雄池：简写池 + 完整记录池合并（完整记录优先，它带 by_lane / 水位）
    pool: dict = {}
    for raw_name, val in (cfg.pool or {}).items():
        hero = book.try_resolve(str(raw_name))
        if hero is None:
            continue
        if isinstance(val, (list, tuple)):
            g = int(val[0])
            w = int(val[1]) if len(val) > 1 else round(g / 2)
        else:
            g = int(val)
            w = round(g / 2)
        pool[hero.name] = PlayerHeroStat(hero=hero.name, games=g, wins=min(w, g))
    for raw_name, rec in (cfg.pool_records or {}).items():
        hero = book.try_resolve(str(raw_name))
        if hero is None:
            continue
        st = PlayerHeroStat.from_dict({**rec, "hero": hero.name})
        pool[hero.name] = st
    print(f"英雄池: {len(pool)} 个英雄（来自 {cfg.source}）")

    # 2) 拉最近对局列表
    try:
        matches = client.opendota_player_matches(account_id, limit=args.limit)
    except ApiError as e:
        print(f"拉取对局列表失败: {e}", file=sys.stderr)
        return 1
    if not matches:
        print("这个账号没有公开对局。请确认已在 Dota 2 里开启「Expose Public Match Data」。")
        return 1

    # 3) 水位：跳过已经处理过的 match_id
    seen_ids = {st.last_match_id for st in pool.values() if st.last_match_id}
    watermark = max(seen_ids) if seen_ids else 0
    print(f"水位: {watermark or '（无，首次同步）'}；列表返回 {len(matches)} 局")

    hero_by_id = {}
    for h in client.opendota_heroes():
        hero_by_id[int(h["id"])] = h["localized_name"]

    changed: dict = {}
    skipped_no_pos = 0
    skipped_old = 0
    errors = 0
    for m in matches:
        mid = int(m.get("match_id") or 0)
        if not mid:
            continue
        if watermark and mid <= watermark:
            skipped_old += 1
            continue
        try:
            detail = client.opendota_match(mid)
        except ApiError as e:
            errors += 1
            print(f"  ! {mid}: {e}", file=sys.stderr)
            continue
        hero_id, win, pos, lane_role = position_of(detail, account_id)
        name = hero_by_id.get(hero_id)
        if not name or name not in book.heroes:
            continue
        st = pool.get(name) or PlayerHeroStat(hero=name)
        if pos is None:
            skipped_no_pos += 1
        st.games += 1
        if win:
            st.wins += 1
        if pos:
            st.add_lane_result(pos, win)
        st.last_match_id = max(st.last_match_id or 0, mid)
        st.last_played = int(detail.get("start_time") or m.get("start_time") or 0) or st.last_played
        pool[name] = st
        changed.setdefault(name, []).append((mid, pos, lane_role, win))

    if not changed:
        print(f"没有新对局需要同步（跳过 {skipped_old} 局已处理的）。")
        return 0

    print()
    print(f"本次同步到 {sum(len(v) for v in changed.values())} 局，涉及 {len(changed)} 个英雄：")
    for name in sorted(changed, key=lambda n: -len(changed[n])):
        rows = changed[name]
        wins = sum(1 for *_x, w in rows if w)
        pos_txt = "、".join(sorted({str(p) for _m, p, _l, _w in rows if p})) or "未知"
        print(f"  {name:20s} {len(rows)} 局 {wins} 胜  位置: {pos_txt}")
    if skipped_no_pos:
        print(f"  （其中 {skipped_no_pos} 局没有位置数据——旧对局常未被解析，只计总体战绩）")

    if args.probe:
        print("\n--probe：未写入配置。")
        return 0

    # 4) 回写配置
    cfg.steam_id = cfg.steam_id or str(account_id)
    cfg.pool_records = {name: st.to_dict() for name, st in sorted(pool.items())}
    # 简写池已经被完整记录覆盖，清掉避免两份数据打架
    cfg.pool = {}
    try:
        out = cfg.save(cfg_path)
    except Exception as e:
        print(f"写入配置失败: {e}", file=sys.stderr)
        return 1
    print(f"\n已回写英雄池 → {out}")
    print("下次启动悬浮窗/面板时，熟练度会自动用上这些新战绩（含分位置数据）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
