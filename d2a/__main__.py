# -*- coding: utf-8 -*-
"""统一入口。

::

    python -m d2a                 # 默认启动 Windows 悬浮窗
    python -m d2a --web           # 启动浏览器面板（推荐兜底）
    python -m d2a --cli           # 命令行交互
    python -m d2a --demo          # 仿真对局，快速看效果
    python -m d2a --steam 12345678  # 先用公开战绩导入英雄池
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import List, Optional

from .cli import build_assistant, main as cli_main
from .config import Config
from .overlay import OverlayOptions, run_overlay
from .webui import serve


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="d2a", description="Dota2 选人阶段助手（仅使用公开数据）")
    ap.add_argument("--web", action="store_true", help="启动浏览器面板")
    ap.add_argument("--cli", action="store_true", help="启动命令行交互模式")
    ap.add_argument("--demo", action="store_true", help="跑一个仿真对局")
    ap.add_argument("--config", type=str, default=None, help="配置文件路径")
    ap.add_argument("--port", type=int, default=8787, help="Web 面板端口")
    ap.add_argument("--host", type=str, default="127.0.0.1", help="Web 面板绑定地址（默认仅本机）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--steam", type=str, default="", help="用公开 API 导入英雄池的 Steam ID")
    args, rest = ap.parse_known_args(argv)

    cfg_path = pathlib.Path(args.config) if args.config else None
    if args.cli or args.demo or rest:
        # 交给 CLI 处理（它自己有 --demo/--once/--steam 等参数）
        fwd = argv[:]
        if args.demo and "--demo" not in fwd:
            fwd.append("--demo")
        return cli_main(fwd)

    cfg = Config.load(cfg_path)
    a = build_assistant(cfg, cfg_path)

    if args.steam:
        from .steam_api import PublicDataClient, import_pool_from_opendota
        from .steam_id import parse_steam_input

        try:
            account_id = parse_steam_input(args.steam)
            cache = pathlib.Path(__file__).resolve().parents[1] / "data" / "cache"
            recs = import_pool_from_opendota(
                PublicDataClient(cache), account_id, a.book, min_games=cfg.import_min_games
            )
            n = a.pool_from_records(recs)
            cfg.steam_id = args.steam
            cfg.pool = {h: [s.games, s.wins] for h, s in a.engine.pool.items()}
            print(f"已导入 {n} 个英雄的公开战绩 → {cfg.save(cfg_path)}")
        except Exception as e:
            print(f"导入失败: {e}", file=sys.stderr)
            return 2

    if args.web:
        serve(a, host=args.host, port=args.port, open_browser=not args.no_browser)
        return 0

    if not a.engine.pool:
        print("英雄池为空：悬浮窗/面板里手动添加，或用 --steam <id> 从公开战绩导入。\n")

    return run_overlay(a, OverlayOptions.from_config(cfg.overlay_options()))


if __name__ == "__main__":
    sys.exit(main())
