# -*- coding: utf-8 -*-
"""从本地缓存离线重建 data/meta.json（不联网）。

存在的原因：``meta.json`` 的 schema 从 1 升到 2，新增了各天梯档位的胜率
（``brackets``），用于「高分段切片」。这个脚本用已经缓存的
``data/cache/opendota_hero_stats.json`` 直接重建，不必重新联网拉 127 个英雄。

同时会补上**真实版本号**（从 ``data/cache/opendota_patches.json`` 取最新一条；
该缓存由 ``refresh_data.py --meta`` 写入）。

用法::

    python tools/rebuild_meta.py              # 重建并写入
    python tools/rebuild_meta.py --dry-run    # 只报告
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


def load(p: pathlib.Path):
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "data" in raw:
        return raw["data"]
    return raw


def latest_patch() -> tuple:
    p = CACHE / "opendota_patches.json"
    if not p.exists():
        return "unknown", ""
    try:
        rows = load(p)
    except (json.JSONDecodeError, OSError):
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
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    hs = CACHE / "opendota_hero_stats.json"
    hh = CACHE / "opendota_heroes.json"
    if not hs.exists() or not hh.exists():
        raise SystemExit("缺少 heroStats/heroes 缓存；请先跑 python tools/refresh_data.py --meta")

    heroes = {int(h["id"]): h["localized_name"] for h in load(hh)}
    known = {h["name"] for h in json.loads((DATA / "heroes.json").read_text(encoding="utf-8"))["heroes"]}
    out: dict = {}
    bracket_rows = 0
    for r in load(hs):
        name = heroes.get(int(r.get("id", 0)))
        if not name or name not in known:
            continue
        picks = int(r.get("pub_pick") or 0)
        wins = int(r.get("pub_win") or 0)
        if picks <= 0:
            picks = int(r.get("pro_pick") or 0)
            wins = int(r.get("pro_win") or 0)
        if picks <= 0:
            continue
        brackets = {}
        for b in range(1, 9):
            bp = int(r.get(f"{b}_pick") or 0)
            bw = int(r.get(f"{b}_win") or 0)
            if bp > 0:
                brackets[str(b)] = [bp, min(bw, bp)]
        if brackets:
            bracket_rows += 1
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

    total = sum(v["pickrate"] for v in out.values()) or 1
    for v in out.values():
        v["pickrate_pct"] = round(v["pickrate"] / total, 5)

    patch, patch_date = latest_patch()
    print(f"英雄数: {len(out)}（本地库 {len(known)}）")
    print(f"带分档位数据的英雄: {bracket_rows}")
    print(f"版本号: {patch}  ({patch_date[:10]})")
    if len(out) < 100:
        print("! 英雄数偏少，缓存可能不完整")

    if args.dry_run:
        print("--dry-run：未写入")
        return 0

    payload = {
        "schema": 2,
        "source": "opendota/heroStats",
        "fetched_at": int(time.time()),
        "patch": patch,
        "patch_date": patch_date,
        "bracket_note": "brackets 键为天梯档位 1..8 = Herald/Guardian/Crusader/Archon/Legend/Ancient/Divine/Immortal",
        "heroes": out,
    }
    (DATA / "meta.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {DATA / 'meta.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
