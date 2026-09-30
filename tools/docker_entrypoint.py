# -*- coding: utf-8 -*-
"""容器入口：启动前自检 → 启动 Web 面板；也可当健康检查用。

为什么把入口写成 Python 而不是 shell 脚本
------------------------------------------
1. 镜像里没有 curl/wget，Dockerfile 的 HEALTHCHECK 需要一个能发 HTTP 请求的东西，
   标准库 urllib 就够，不用为此装包；
2. 启动前的自检（数据文件齐不齐、JSON 能不能解析、127 个英雄在不在）
   用 Python 写比 shell 可靠得多，而且报错信息能带上具体缺什么。

用法
----
    python tools/docker_entrypoint.py                # 启动服务（读环境变量）
    python tools/docker_entrypoint.py --healthcheck   # 探活，退出码 0/1（给 HEALTHCHECK 用）
    python tools/docker_entrypoint.py --selfcheck      # 只做启动前自检，不启动服务
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REQUIRED_DATA = [
    ("heroes.json", "英雄库（属性/分路/能力标签）"),
    ("aliases.json", "英雄别名表"),
    ("matchups.json", "人工克制种子"),
    ("synergies.json", "人工协同种子"),
]


def _fail(msg: str, code: int = 1) -> int:
    print(f"[entrypoint][FATAL] {msg}", file=sys.stderr, flush=True)
    return code


def baseline_dir() -> pathlib.Path:
    """镜像内的原始数据副本（见 Dockerfile 的 /opt/d2a-baseline）。"""
    return pathlib.Path(os.environ.get("D2A_BASELINE_DIR") or "/opt/d2a-baseline/data")


def seed_data_dir(data_dir: pathlib.Path) -> int:
    """把基线数据播种进数据目录（只补缺失的文件，绝不覆盖已有的）。

    为什么需要这一步：``docker-compose.yml`` 把 ``./data-docker`` 挂到 ``/app/data``，
    **挂载会把镜像里烤好的 /app/data 整个遮住**——新卷首次启动时目录是空的，
    服务会因为找不到 heroes.json 直接失败。所以镜像在 /opt 另存一份基线，
    启动时发现缺什么就补什么。

    只补不覆盖：这样你手动改过 heroes.json、或刷新过 meta.json，都不会被镜像里的旧版冲掉。
    """
    src = baseline_dir()
    if not src.exists():
        # 非容器环境（本地直接跑）没有基线目录，属正常情况
        return 0
    seeded = []
    for item in sorted(src.iterdir()):
        if item.name == "cache":
            continue
        dst = data_dir / item.name
        if dst.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
            seeded.append(item.name)
        except OSError as e:
            print(f"[entrypoint][warn] 播种 {item.name} 失败: {e}", file=sys.stderr, flush=True)
    if seeded:
        print(f"[entrypoint] 已从基线播种 {len(seeded)} 个数据文件: {', '.join(seeded)}", flush=True)
    return len(seeded)


def selfcheck(strict: bool = False) -> int:
    """启动前自检：只检查「服务能不能正常工作」，不联网。

    ``strict=True`` 时把缺失的运行时数据（meta.json / matchups_live.json）
    也当成错误；默认只警告，因为这两个文件是刷新产物、缺失时仍可运行。
    返回 0 表示可以启动。
    """
    print("[entrypoint] 启动前自检…", flush=True)
    problems = []

    # 1) 必需的数据文件
    data_dir = pathlib.Path(os.environ.get("D2A_DATA_DIR") or (ROOT / "data"))
    for name, desc in REQUIRED_DATA:
        p = data_dir / name
        if not p.exists():
            problems.append(f"缺少 {p}（{desc}）")
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            problems.append(f"{p} 无法解析: {e}")
            continue
        if name == "heroes.json":
            n = len(payload.get("heroes") or [])
            print(f"[entrypoint]   heroes.json: {n} 个英雄", flush=True)
            if n < 100:
                problems.append(f"heroes.json 只有 {n} 个英雄，数据不完整")
        elif name == "aliases.json":
            n = len([k for k in payload if not str(k).startswith("_")])
            print(f"[entrypoint]   aliases.json: {n} 条别名", flush=True)
        else:
            n = sum(len(v) for v in (payload.get("pairs") or {}).values())
            print(f"[entrypoint]   {name}: {n} 条", flush=True)

    # 2) 可选但影响推荐质量的运行时数据
    for name in ("meta.json", "matchups_live.json"):
        p = data_dir / name
        if not p.exists():
            msg = f"{p} 不存在（版本强度/真实对位缺失，推荐质量下降）"
            if strict:
                problems.append(msg)
            else:
                print(f"[entrypoint]   [warn] {msg}", flush=True)
        else:
            print(f"[entrypoint]   {name}: 存在", flush=True)

    # 3) 可写性：配置与缓存要能写
    for sub in ("", "cache"):
        d = data_dir / sub if sub else data_dir
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            problems.append(f"{d} 不可写（挂载卷权限）: {e}")

    # 4) 真正 import 一次引擎，确认代码与数据能协同工作
    try:
        from d2a.data_loader import HeroBook
        from d2a.engine import Assistant
        from d2a.draft import Draft

        book = HeroBook.load(data_dir)
        a = Assistant.create(book=book)
        cands = a.engine.recommend(Draft(), top_n=3, pool_only=False)
        print(f"[entrypoint]   引擎自检通过（示例推荐 {len(cands)} 条）", flush=True)
    except Exception as e:
        problems.append(f"引擎自检失败: {type(e).__name__}: {e}")

    if problems:
        print("[entrypoint] 自检未通过：", file=sys.stderr, flush=True)
        for x in problems:
            print(f"[entrypoint]   - {x}", file=sys.stderr, flush=True)
        return 1
    print("[entrypoint] 自检通过 ✅", flush=True)
    return 0


def healthcheck() -> int:
    """探活：请求本机 /api/health。"""
    port = os.environ.get("D2A_PORT", "8787")
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        if resp.status == 200 and body.get("ok"):
            print(f"healthy: {body.get('heroes')} heroes, patch {body.get('patch')}")
            return 0
        print(f"unhealthy: HTTP {resp.status} {body}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as e:
        print(f"unhealthy: HTTP {e.code}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"unhealthy: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="dota2-assistant 容器入口")
    ap.add_argument("--healthcheck", action="store_true", help="探活后退出（给 Docker HEALTHCHECK 用）")
    ap.add_argument(
        "--selfcheck", "--check", "--self-check",
        action="store_true", dest="selfcheck",
        help="只做启动前自检，不启动服务",
    )
    ap.add_argument("--strict", action="store_true", help="自检时把缺失的运行时数据也当错误")
    ap.add_argument("--no-seed", action="store_true", help="跳过从基线播种数据")
    args = ap.parse_args()

    if args.healthcheck:
        return healthcheck()

    data_dir = pathlib.Path(os.environ.get("D2A_DATA_DIR") or (ROOT / "data"))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _fail(f"数据目录 {data_dir} 无法创建/访问: {e}")
    if not args.no_seed:
        # 必须在自检之前：新挂的卷是空的，先播种再检查
        seed_data_dir(data_dir)

    rc = selfcheck(strict=args.strict)
    if rc != 0:
        return rc
    if args.selfcheck:
        return 0

    # 配置：优先环境变量指定的路径，确保落在挂载卷上
    cfg = os.environ.get("D2A_CONFIG")
    if cfg:
        os.environ.setdefault("D2A_CONFIG", cfg)
        print(f"[entrypoint] 配置文件: {cfg}", flush=True)

    if not os.environ.get("D2A_API_TOKEN"):
        print(
            "[entrypoint] [warn] 未设置 D2A_API_TOKEN。容器绑定的是 0.0.0.0，"
            "任何能访问该端口的人都能改你的英雄池与权重。",
            flush=True,
        )

    print(
        f"[entrypoint] 启动服务 {os.environ.get('D2A_HOST', '0.0.0.0')}:{os.environ.get('D2A_PORT', '8787')}",
        flush=True,
    )
    # 清掉我们自己的参数，避免被下游 argparse 当成未知参数
    sys.argv = [sys.argv[0]]
    from d2a import __main__ as d2a_main

    return d2a_main.main(["--web", "--no-browser"])


if __name__ == "__main__":
    sys.exit(main())
