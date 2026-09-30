# -*- coding: utf-8 -*-
"""把 tools/seed_matchups.py 的人工种子编译成 data/matchups.json 与 data/synergies.json。

同时做一致性校验：任何指向不存在英雄的名字都会直接报错，
避免手工维护的数据悄悄写错英雄名（这是这类数据集最常见的隐性 bug）。
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.seed_matchups import counter_pairs, synergy_pairs  # noqa: E402


def load_names() -> set:
    raw = json.loads((ROOT / "data" / "heroes.json").read_text(encoding="utf-8"))
    return {h["name"] for h in raw["heroes"]}


def build(pairs, names: set, kind: str) -> dict:
    problems = []
    out: dict = {}
    for a, rows in pairs.items():
        if a not in names:
            problems.append(f"{kind}: 主键英雄不存在 -> {a}")
            continue
        clean = {}
        for b, v in rows:
            if b not in names:
                problems.append(f"{kind}: {a} 指向不存在的英雄 -> {b}")
                continue
            if b == a:
                problems.append(f"{kind}: {a} 不能与自己配对")
                continue
            if not (0 < abs(v) <= 0.15):
                problems.append(f"{kind}: {a}-{b} 数值 {v} 超出合理范围 (0, 0.15]")
                continue
            clean[b] = round(float(v), 4)
        if clean:
            out[a] = dict(sorted(clean.items()))
    if problems:
        print("\n".join("  ! " + p for p in problems), file=sys.stderr)
        raise SystemExit(f"{kind}: 校验失败，共 {len(problems)} 个问题")
    return out


def main() -> int:
    names = load_names()
    m = build(counter_pairs, names, "counter")
    s = build(synergy_pairs, names, "synergy")

    (ROOT / "data" / "matchups.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "source": "hand-curated seed",
                "unit": "winrate delta vs 50% (0.05 = +5pp)",
                "pairs": m,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (ROOT / "data" / "synergies.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "source": "hand-curated seed",
                "unit": "winrate delta vs 50%",
                "pairs": s,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    edges = sum(len(v) for v in m.values())
    sedges = sum(len(v) for v in s.values())
    print(f"克制关系: {edges} 条，覆盖 {len(m)} 个英雄")
    print(f"协 同 关系: {sedges} 条，覆盖 {len(s)} 个英雄")
    return 0


if __name__ == "__main__":
    sys.exit(main())
