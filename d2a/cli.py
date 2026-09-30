# -*- coding: utf-8 -*-
"""命令行界面：不依赖任何第三方库，终端里就能完成 BP 询问。

设计取舍：CLI 是最快能验证评分引擎是否靠谱的入口，
悬浮窗和 Web 面板都复用同一套 :class:`d2a.engine.Assistant`。

用法::

    python -m d2a.cli                  # 进入交互式点选
    python -m d2a.cli --demo           # 跑一个仿真对局，先看效果
    python -m d2a.cli --once --enemy "Juggernaut,Luna" --lane 4
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import unicodedata
from typing import Dict, List, Optional, Tuple

from .config import Config
from .data_loader import HeroNotFound, PlayerHeroStat
from .engine import Assistant, Candidate


# --------------------------------------------------------------------------- 排版
def dwidth(s: str) -> int:
    """显示宽度：中文算 2 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s: str, width: int, align: str = "left") -> str:
    space = max(0, width - dwidth(s))
    if align == "right":
        return " " * space + s
    if align == "center":
        left = space // 2
        return " " * left + s + " " * (space - left)
    return s + " " * space


def stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


BAR = "─"


def hr(title: str = "", width: int = 74) -> str:
    if not title:
        return BAR * width
    inner = f" {title} "
    left = (width - dwidth(inner)) // 2
    right = width - dwidth(inner) - left
    return BAR * max(0, left) + inner + BAR * max(0, right)


# --------------------------------------------------------------------------- 展示
def render_pool(assistant: Assistant, limit: int = 12) -> str:
    pool = sorted(assistant.engine.pool.values(), key=lambda s: -s.games)[:limit]
    if not pool:
        return "  （英雄池为空，用 pool 命令添加）"
    lines = []
    for s in pool:
        note = "数据不全(按50%)" if s.games and abs(s.winrate - 0.5) < 1e-9 and s.games > 0 else f"{s.winrate:.0%}"
        lines.append(f"  {pad(s.hero, 20)} {pad(str(s.games), 5, 'right')} 局  胜率 {note}")
    return "\n".join(lines)


def render_candidate(c: Candidate, idx: int, verbose: bool = True, span: int = 72) -> str:
    out = [
        f"{pad(str(idx) + '.', 4)}{pad(c.hero.name, 20)} {stars(c.stars)}  "
        f"评分 {pad(f'{c.score:.1f}', 5, 'right')}  位置 {c.position or '-'}"
    ]
    if verbose:
        b = c.breakdown
        out.append(
            f"      熟练 {b['proficiency']:.2f} | 对位 {b['matchup']:.2f} | "
            f"阵容 {b['team_need']:.2f} | 版本 {b['meta']:.2f} | 配合 {b['synergy']:.2f}"
        )
        for r in c.reasons:
            out.append("      · " + r)
        for r in c.risks:
            out.append("      ! " + r)
    return "\n".join(out)


def render_result(assistant: Assistant, cands: List[Candidate], title: str, verbose: bool = True) -> str:
    lines = [hr(title), ""]
    if not cands:
        lines.append("  没有可推荐的英雄（英雄池为空？先用 pool 命令添加，或试 all 命令看全英雄）。")
    for i, c in enumerate(cands, 1):
        lines.append(render_candidate(c, i, verbose=verbose))
        lines.append("")
    return "\n".join(lines)


def render_draft(assistant: Assistant) -> str:
    d = assistant.draft
    lines = [hr("当前 BP"), ""]
    ally = []
    for h in d.ally_heroes():
        lane = d.ally_lanes.get(h)
        ally.append(f"{h}[{lane}]" if lane else h)
    lines.append("  我方: " + ("、".join(ally) if ally else "（空）"))
    lines.append("  敌方: " + ("、".join(d.enemy_heroes()) if d.enemy_heroes() else "（空）"))
    if d.bans:
        lines.append("  已 ban: " + "、".join(d.bans))
    needs = assistant.engine.team_needs(d)
    gaps = [k for k, v in needs.items() if v > 0.25]
    label = {"init": "先手", "control": "硬控", "frontline": "前排", "save": "救人", "push": "清线"}
    lines.append("  阵容缺口: " + ("、".join(label.get(k, k) for k in gaps) if gaps else "暂无"))
    lanes = assistant.engine.role_suggestion(d)
    if lanes:
        lines.append("  待补位置: " + "；".join(f"{l} {t}" for l, t in lanes[:3]))
    lines.append("")
    return "\n".join(lines)


HELP = """可用命令
  a <英雄> [位置]      我方选人（位置 1-5，可省略）
  e <英雄> [位置]      敌方选人
  b <英雄>             记录一个 ban
  rm <英雄>            撤销某个英雄
  lane <位置>          切换我这一手要打的位置
  pool                 查看我的英雄池
  pool add <英雄> <局数> [胜场]
                       添加/更新英雄池条目（支持中文名，如"斧王"）
  pool rm <英雄>       从英雄池删除
  pool keep            只保留当前英雄池之外没有的英雄？(打印统计)
  import <steam id>    从公开 API 导入英雄池（需联网）
  rec [n]              推荐（默认只看你的英雄池）
  all [n]              在全部英雄里推荐（可能发现版本答案/克制位）
  why <英雄>           解释某个英雄为什么被推荐/不推荐
  counter [英雄]       找克制位
  w <键> <值>          调整权重，键: prof/match/team/meta/syn
  data                 查看数据来源与版本信息
  refresh [--matchups] 在线刷新版本强度（可选真实对位）
  reset                清空当前 BP
  demo                 跑一遍仿真对局
  clear                清屏
  q / quit             退出
"""


# --------------------------------------------------------------------------- 交互
def _parse_int(tok: str) -> Optional[int]:
    try:
        v = int(tok)
        return v if 1 <= v <= 5 else None
    except ValueError:
        return None


class Cli:
    def __init__(self, assistant: Assistant, config: Config, config_path: Optional[pathlib.Path] = None) -> None:
        self.a = assistant
        self.cfg = config
        self.config_path = config_path
        self.verbose = True
        self.last: List[Candidate] = []

    # ------------------------------------------------------------ 命令分发
    def dispatch(self, line: str) -> bool:
        """返回 False 表示要退出。"""
        line = line.strip()
        if not line:
            return True
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]

        try:
            if cmd in ("q", "quit", "exit"):
                return False
            if cmd in ("h", "help", "?"):
                print(HELP)
            elif cmd == "a":
                self._add("ally", args)
            elif cmd == "e":
                self._add("enemy", args)
            elif cmd == "b":
                self._add_ban(args)
            elif cmd == "rm":
                self._remove(args)
            elif cmd == "lane":
                self._set_lane(args)
            elif cmd == "pool":
                self._pool(args)
            elif cmd == "import":
                self._import(args)
            elif cmd == "rec":
                self._recommend(args, pool_only=True)
            elif cmd == "all":
                self._recommend(args, pool_only=False)
            elif cmd == "why":
                self._why(args)
            elif cmd == "counter":
                self._counter(args)
            elif cmd == "w":
                self._weights(args)
            elif cmd == "data":
                self.print_data_info()
            elif cmd == "refresh":
                self._refresh(args)
            elif cmd == "reset":
                self.a.draft = type(self.a.draft)()
                print("已清空 BP")
            elif cmd == "demo":
                run_demo(self.a)
            elif cmd in ("clear", "cls"):
                print("\033[2J\033[H", end="")
            elif cmd == "v":
                self.verbose = not self.verbose
                print(f"详细输出: {'开' if self.verbose else '关'}")
            else:
                print(f"未知命令: {cmd}（输入 help 查看全部命令）")
        except HeroNotFound as e:
            print(f"认不出这个英雄: {e}（支持中文名/缩写，如 斧王、am、magina）")
        except Exception as e:  # 交互式工具：不因为一个命令崩掉整个会话
            print(f"命令执行出错: {type(e).__name__}: {e}")
        return True

    # ------------------------------------------------------------ 子命令
    def _add(self, side: str, args: List[str]) -> None:
        if not args:
            print(f"用法: {'a' if side == 'ally' else 'e'} <英雄> [位置]")
            return
        lane = None
        if len(args) >= 2:
            lane = _parse_int(args[-1])
            if lane is not None:
                args = args[:-1]
        hero = self.a.book.resolve(" ".join(args))
        p = self.a.draft.add(hero.name, side, lane)
        tag = "我方" if side == "ally" else "敌方"
        print(f"已记录 {tag}: {hero.name}" + (f"（{lane}号位）" if lane else ""))
        if side == "ally" and lane:
            pass
        elif side == "enemy" and not lane:
            guess = self.a.draft.guess_enemy_lanes(self.a.book).get(hero.name)
            if guess:
                print(f"  推断位置: {guess}（不准的话用 lane 命令改）")

    def _add_ban(self, args: List[str]) -> None:
        if not args:
            print("用法: b <英雄>")
            return
        hero = self.a.book.resolve(" ".join(args))
        if hero.name not in self.a.draft.bans:
            self.a.draft.bans.append(hero.name)
        print(f"已记录 ban: {hero.name}")

    def _remove(self, args: List[str]) -> None:
        if not args:
            print("用法: rm <英雄>")
            return
        hero = self.a.book.resolve(" ".join(args))
        if self.a.draft.remove(hero.name):
            print(f"已撤销: {hero.name}")
        elif hero.name in self.a.draft.bans:
            self.a.draft.bans.remove(hero.name)
            print(f"已移除 ban: {hero.name}")
        else:
            print(f"{hero.name} 不在当前 BP 里")

    def _set_lane(self, args: List[str]) -> None:
        if not args:
            print("用法: lane <1-5>")
            return
        lane = _parse_int(args[0])
        if lane is None:
            print("位置必须是 1-5")
            return
        self.a.draft.my_lane = lane
        print(f"我这一手按 {lane} 号位推荐")

    def _pool(self, args: List[str]) -> None:
        if not args:
            print(hr("我的英雄池"))
            print(render_pool(self.a, limit=30))
            print()
            return
        sub = args[0].lower()
        if sub == "add":
            if len(args) < 3:
                print("用法: pool add <英雄> <局数> [胜场]")
                return
            hero = self.a.book.resolve(args[1])
            games = int(args[2])
            wins = int(args[3]) if len(args) >= 4 else round(games * 0.5)
            self.a.engine.pool[hero.name] = PlayerHeroStat(hero=hero.name, games=games, wins=min(wins, games))
            self._persist_pool()
            print(f"已更新 {hero.name}: {games} 局 / {wins} 胜（{wins / games:.0%}）")
        elif sub == "rm":
            if len(args) < 2:
                print("用法: pool rm <英雄>")
                return
            hero = self.a.book.resolve(args[1])
            if self.a.engine.pool.pop(hero.name, None):
                self._persist_pool()
                print(f"已从英雄池删除 {hero.name}")
            else:
                print(f"{hero.name} 不在英雄池里")
        elif sub == "keep":
            n = len(self.a.engine.pool)
            total = sum(s.games for s in self.a.engine.pool.values())
            print(f"英雄池: {n} 个英雄，累计 {total} 局")
        else:
            print("用法: pool / pool add / pool rm / pool keep")

    def _persist_pool(self) -> None:
        self.cfg.pool = {h: [s.games, s.wins] for h, s in self.a.engine.pool.items()}
        try:
            p = self.cfg.save(self.config_path)
            print(f"  （已保存到 {p}）")
        except Exception as e:
            print(f"  （保存失败: {e}）")

    def _import(self, args: List[str]) -> None:
        if not args:
            print("用法: import <steam id / 64位id / 个人主页链接>")
            return
        from .steam_api import PublicDataClient, import_pool_from_opendota
        from .steam_id import parse_steam_input

        try:
            account_id = parse_steam_input(args[0])
        except Exception as e:
            print(f"Steam ID 解析失败: {e}")
            return
        cache = pathlib.Path(__file__).resolve().parents[1] / "data" / "cache"
        client = PublicDataClient(cache)
        try:
            records = import_pool_from_opendota(client, account_id, self.a.book, min_games=self.cfg.import_min_games)
        except Exception as e:
            print(f"导入失败（需要联网）: {e}")
            return
        n = self.a.pool_from_records(records)
        self.cfg.steam_id = args[0]
        self._persist_pool()
        print(f"已从公开 API 导入 {n} 个英雄（account_id={account_id}，≥{self.cfg.import_min_games} 局）")
        print(render_pool(self.a, limit=10))

    def _recommend(self, args: List[str], pool_only: bool) -> None:
        n = 5
        if args:
            try:
                n = max(1, int(args[0]))
            except ValueError:
                pass
        if pool_only and not self.a.engine.pool:
            print("英雄池是空的：先用 pool add 添加，或直接看 all 命令的全英雄推荐。")
            return
        cands = self.a.engine.recommend(self.a.draft, top_n=n, pool_only=pool_only)
        self.last = cands
        title = "推荐（你的英雄池）" if pool_only else "推荐（全英雄 · 含版本答案与克制位）"
        print(render_result(self.a, cands, title, verbose=self.verbose))

    def _why(self, args: List[str]) -> None:
        if not args:
            print("用法: why <英雄>")
            return
        hero = self.a.book.resolve(" ".join(args))
        c = self.a.engine.explain(hero.name, self.a.draft)
        if c is None:
            print(f"{hero.name} 已被选走或 ban 掉")
            return
        print(hr(f"为什么是 {hero.name}"))
        print(render_candidate(c, 1, verbose=True))
        print()
        pool_note = "" if c.games else "（这个英雄不在你的英雄池里，熟练度按 0 计）"
        print(f"  胜率数据: {self.a.book.winrate(hero.name):.1%} {pool_note}")
        print(f"  可用位置: {list(hero.lanes)}  主属性: {hero.attr}  伤害: {hero.damage}")
        print(f"  标签: {'、'.join(hero.tags) or '-'}")
        print()

    def _counter(self, args: List[str]) -> None:
        target = " ".join(args) if args else None
        if target:
            target = self.a.book.resolve(target).name
        cands = self.a.engine.counter_picks(self.a.draft, against=target, top_n=5)
        print(render_result(self.a, cands, f"克制推荐（针对 {target or '敌方已选全部英雄'}）", verbose=self.verbose))

    def _weights(self, args: List[str]) -> None:
        keymap = {
            "prof": "proficiency",
            "proficiency": "proficiency",
            "match": "matchup",
            "matchup": "matchup",
            "team": "team_need",
            "team_need": "team_need",
            "meta": "meta",
            "syn": "synergy",
            "synergy": "synergy",
        }
        if len(args) < 2:
            w = self.a.weights.normalized()
            print(
                f"当前权重: 熟练 {w.proficiency:.2f} | 对位 {w.matchup:.2f} | "
                f"阵容 {w.team_need:.2f} | 版本 {w.meta:.2f} | 配合 {w.synergy:.2f}"
            )
            return
        key = keymap.get(args[0].lower())
        if not key:
            print(f"未知权重键: {args[0]}（可用 prof/match/team/meta/syn）")
            return
        self.a.set_weights(**{key: float(args[1])})
        self.cfg.weights = {
            "proficiency": self.a.weights.proficiency,
            "matchup": self.a.weights.matchup,
            "team_need": self.a.weights.team_need,
            "meta": self.a.weights.meta,
            "synergy": self.a.weights.synergy,
        }
        self._persist_pool()
        self._weights([])

    def _refresh(self, args: List[str]) -> None:
        script = pathlib.Path(__file__).resolve().parents[1] / "tools" / "refresh_data.py"
        cmd = [sys.executable, str(script), "--meta"]
        if "--matchups" in args:
            cmd.append("--matchups")
        print(f"执行: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=False)
            print("刷新完成，重新加载数据…")
            self.a.book = type(self.a.book).load()
            self.a.engine.book = self.a.book
            self.print_data_info()
        except Exception as e:
            print(f"刷新失败: {e}")

    def print_data_info(self) -> None:
        b = self.a.book
        import time as _t

        def ts(x: int) -> str:
            return _t.strftime("%Y-%m-%d %H:%M", _t.localtime(x)) if x else "从未"

        print(hr("数据来源"))
        print(f"  英雄库: {len(b.heroes)} 个英雄（内置人工维护）")
        print(f"  克制关系: {sum(len(v) for v in b.matchups.values())} 条，来源 {b.live_source}")
        print(f"    真实对位数据抓取时间: {ts(b.live_fetched_at)}")
        print(f"  版本胜率: 来源 {b.meta_source}，抓取时间 {ts(b.meta_fetched_at)}，覆盖 {len(b.meta)} 个英雄")
        problems = self.a.book.validate()
        print(f"  自检: {'通过' if not problems else str(len(problems)) + ' 个问题'}")
        for p in problems[:5]:
            print("    ! " + p)
        print()


# --------------------------------------------------------------------------- 仿真
def run_demo(assistant: Assistant) -> None:
    """跑一个典型的随机匹配 BP 场景，让玩家直接看到效果。"""
    from .data_loader import PlayerHeroStat
    from .draft import Draft

    demo_pool = {
        "Axe": (60, 37), "Magnus": (44, 27), "Crystal Maiden": (88, 50),
        "Lion": (52, 30), "Sand King": (40, 24), "Tidehunter": (33, 20),
        "Warlock": (28, 16), "Tusk": (35, 19), "Spirit Breaker": (47, 27),
        "Juggernaut": (30, 15), "Luna": (26, 14), "Sniper": (22, 13),
    }
    assistant.engine.set_pool(PlayerHeroStat(hero=k, games=v[0], wins=v[1]) for k, v in demo_pool.items())

    d = Draft()
    d.add("Juggernaut", "enemy")
    d.add("Luna", "enemy")
    d.add("Axe", "ally", 3)
    d.add("Crystal Maiden", "ally", 5)
    d.my_lane = 4
    assistant.draft = d

    print(render_draft(assistant))
    cands = assistant.engine.recommend(d, top_n=5, pool_only=True)
    print(render_result(assistant, cands, "仿真：敌方已选 剑圣/露娜，我打4号位", verbose=True))
    alls = assistant.engine.recommend(d, top_n=3, pool_only=False)
    print(render_result(assistant, alls, "仿真：全英雄视角（看看有没有更优解）", verbose=False))


# --------------------------------------------------------------------------- 入口
def build_assistant(config: Config, config_path: Optional[pathlib.Path] = None) -> Assistant:
    a = Assistant.create(weights=None)
    if config.weights:
        a.set_weights(**{k: float(v) for k, v in config.weights.items()})
    if config.pool:
        a.set_pool_simple(config.pool)  # type: ignore[arg-type]
    return a


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="d2a.cli", description="Dota2 选人阶段助手（公开数据）")
    ap.add_argument("--config", type=str, default=None, help="配置文件路径")
    ap.add_argument("--demo", action="store_true", help="跑一个仿真对局")
    ap.add_argument("--once", action="store_true", help="执行一次推荐后退出")
    ap.add_argument("--ally", type=str, default="", help="我方已选英雄，逗号分隔")
    ap.add_argument("--enemy", type=str, default="", help="敌方已选英雄，逗号分隔")
    ap.add_argument("--lane", type=int, default=None, help="我这一手的位置 1-5")
    ap.add_argument("--top", type=int, default=6, help="推荐数量")
    ap.add_argument("--all", action="store_true", help="在全部英雄里推荐")
    ap.add_argument("--steam", type=str, default="", help="用公开 API 导入英雄池的 Steam ID")
    args = ap.parse_args(argv)

    cfg_path = pathlib.Path(args.config) if args.config else None
    cfg = Config.load(cfg_path)
    a = build_assistant(cfg, cfg_path)

    if args.steam:
        from .steam_api import PublicDataClient, import_pool_from_opendota
        from .steam_id import parse_steam_input

        try:
            account_id = parse_steam_input(args.steam)
            cache = pathlib.Path(__file__).resolve().parents[1] / "data" / "cache"
            recs = import_pool_from_opendota(PublicDataClient(cache), account_id, a.book, min_games=cfg.import_min_games)
            n = a.pool_from_records(recs)
            cfg.steam_id = args.steam
            cfg.pool = {h: [s.games, s.wins] for h, s in a.engine.pool.items()}
            print(f"已导入 {n} 个英雄的公开战绩（account_id={account_id}）")
            print(f"配置已保存到 {cfg.save(cfg_path)}")
        except Exception as e:
            print(f"导入失败: {e}", file=sys.stderr)
            return 2

    for h in filter(None, (x.strip() for x in args.ally.split(","))):
        a.draft.add(a.book.resolve(h).name, "ally")
    for h in filter(None, (x.strip() for x in args.enemy.split(","))):
        a.draft.add(a.book.resolve(h).name, "enemy")
    if args.lane:
        a.draft.my_lane = args.lane

    cli = Cli(a, cfg, cfg_path)

    if args.demo:
        run_demo(a)
        return 0

    if args.once:
        if not a.draft.is_empty():
            print(render_draft(a))
        cli._recommend([str(args.top)], pool_only=not args.all)
        return 0

    cli.print_data_info()
    if a.draft.is_empty():
        print(HELP)
    if not a.engine.pool:
        print("提示：英雄池为空。先在 config.json 里填 pool，或用 pool add 命令添加几个常用英雄。\n")
    print("输入 help 查看命令，q 退出。\n")

    while True:
        try:
            prompt = f"[{len(a.draft.allies)}v{len(a.draft.enemies)}] > "
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not cli.dispatch(line):
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
