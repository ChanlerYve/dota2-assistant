# -*- coding: utf-8 -*-
"""Web 面板：浏览器里点选 BP，得到推荐。

为什么还要有 Web 版：悬浮窗在部分机器上会被游戏独占全屏挡住；
浏览器窗口（或第二块屏/手机）是稳的兜底，而且和悬浮窗共享同一套引擎。

零依赖：只用标准库 http.server + 内嵌的单页 HTML/JS。
"""

from __future__ import annotations

import hmac
import json
import os
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from ..engine import Assistant, Weights
from ..data_loader import BRACKETS
from ..voice import VoiceConfig, VoiceListener, voice_supported

STATIC_DIR = pathlib.Path(__file__).resolve().parents[1] / "webui" / "static"


class ApiState:
    """所有可变状态集中在这里，供 HTTP handler 读写（带锁）。"""

    def __init__(self, assistant: Assistant, config_path: "pathlib.Path | None" = None) -> None:
        self.a = assistant
        self.lock = threading.RLock()
        # 语音监听器（服务端跑 Windows 自带识别，见 d2a/voice.py）
        self.voice: "VoiceListener | None" = None
        # 持久化目标：容器里指到挂载卷，这样重建容器不丢英雄池与权重
        self.config_path = pathlib.Path(config_path) if config_path else None
        self._dirty = False

    # ---------------------------------------------------------------- 持久化
    def mark_dirty(self) -> None:
        self._dirty = True

    def persist(self) -> None:
        """把当前英雄池/权重/切片写回配置文件。没有配置路径就什么都不做。"""
        if not self.config_path or not self._dirty:
            return
        from ..config import Config

        try:
            cfg = Config.load(self.config_path) if self.config_path.exists() else Config()
        except Exception:
            cfg = Config()
        cfg.pool = {}
        cfg.pool_records = {name: st.to_dict() for name, st in self.a.engine.pool.items()}
        cfg.weights = {
            "proficiency": self.a.weights.proficiency,
            "matchup": self.a.weights.matchup,
            "team_need": self.a.weights.team_need,
            "meta": self.a.weights.meta,
            "synergy": self.a.weights.synergy,
        }
        if self.a.engine.bracket:
            cfg.bracket = str(self.a.engine.bracket)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.save(self.config_path)
        self._dirty = False

    # ---------------------------------------------------------------- 序列化
    def draft_payload(self) -> dict:
        d = self.a.draft
        return {
            "allies": [{"hero": h, "lane": d.ally_lanes.get(h)} for h in d.ally_heroes()],
            "enemies": [{"hero": h, "lane": d.guess_enemy_lanes(self.a.book).get(h)} for h in d.enemy_heroes()],
            "bans": list(d.bans),
            "my_lane": d.my_lane,
            "open_lanes": d.open_lanes(),
            "needs": self.a.engine.team_needs(d),
            "role_suggestion": self.a.engine.role_suggestion(d),
        }

    def pool_payload(self) -> list:
        out = []
        for s in sorted(self.a.engine.pool.values(), key=lambda x: -x.games):
            out.append(
                {
                    "hero": s.hero,
                    "games": s.games,
                    "wins": s.wins,
                    "winrate": round(s.winrate, 4) if s.games else 0.0,
                    # 分位置战绩（有才给，UI 按需展示）
                    "by_lane": {
                        str(k): {
                            "games": int(v[0]),
                            "wins": int(v[1]) if len(v) > 1 else 0,
                            "winrate": round(v[1] / v[0], 4) if v and v[0] else 0.0,
                        }
                        for k, v in sorted((s.by_lane or {}).items())
                    },
                }
            )
        return out

    def data_payload(self) -> dict:
        b = self.a.book
        return {
            "heroes": len(b.heroes),
            "matchup_edges": sum(len(v) for v in b.matchups.values()),
            "matchup_source": b.live_source,
            "matchup_fetched_at": b.live_fetched_at,
            "meta_source": b.meta_source,
            "meta_fetched_at": b.meta_fetched_at,
            "meta_patch": b.meta_patch,
            "meta_patch_date": b.meta_patch_date,
            "meta_heroes": len(b.meta),
            "alias_source": b.alias_source,
            "problems": b.validate(),
            "bracket": self.a.engine.bracket,
            "bracket_label": b.bracket_label(self.a.engine.bracket),
            "brackets": [{"id": k, "label": v} for k, v in BRACKETS.items()],
            "weights": {
                "proficiency": self.a.weights.proficiency,
                "matchup": self.a.weights.matchup,
                "team_need": self.a.weights.team_need,
                "meta": self.a.weights.meta,
                "synergy": self.a.weights.synergy,
            },
        }

    # ---------------------------------------------------------------- 语音
    def voice_payload(self) -> dict:
        """语音状态。识别在服务端跑（复用 d2a.voice），前端只负责开关与展示。"""
        ok, why = voice_supported()
        return {
            "supported": ok,
            "reason": why,
            "listening": bool(self.voice is not None and self.voice.running),
            "events": [
                {"kind": e.kind, "text": e.text, "confidence": e.confidence, "detail": e.detail}
                for e in (self.voice.events[-25:] if self.voice else [])
            ],
        }

    def voice_toggle(self, mode: str = "hero", seconds: int = 0) -> dict:
        if self.voice is not None and self.voice.running:
            self.voice.stop()
            self.voice = None
            return self.voice_payload()
        ok, why = voice_supported()
        if not ok:
            raise RuntimeError(why)
        self.voice = VoiceListener(VoiceConfig(seconds=seconds, mode=mode))
        if not self.voice.start():
            self.voice = None
            raise RuntimeError("语音进程启动失败（检查麦克风权限与默认录音设备）")
        return self.voice_payload()


def make_handler(state: ApiState, api_token: str = "") -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = "d2a/0.2"

        # ------------------------------------------------------------ 工具
        def log_message(self, fmt: str, *args: Any) -> None:  # 静音默认日志
            return

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, code: int = 200) -> None:
            self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _error(self, msg: str, code: int = 400) -> None:
            self._json({"ok": False, "error": msg}, code)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        # ------------------------------------------------------------ 鉴权
        def _authorized(self, path: str, query: Dict[str, list]) -> bool:
            """设置了 ``D2A_API_TOKEN`` 时校验调用方。

            容器里服务会绑到 0.0.0.0（否则端口映射进不来），
            而 POST 接口能改状态，所以一旦设了 token 就必须带上：
              - ``Authorization: Bearer <token>``，或
              - ``?token=<token>``（给浏览器直接打开用）
            ``/api/health`` 永远放行，否则容器健康检查会失败。
            """
            if not api_token:
                return True
            if path == "/api/health":
                return True
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), api_token):
                return True
            given = (query.get("token", [""])[0] or "")
            return bool(given) and hmac.compare_digest(given, api_token)

        # ------------------------------------------------------------ 路由
        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._authorized(u.path, q):
                return self._error("未授权：请带上 Authorization: Bearer <D2A_API_TOKEN>", 401)
            if u.path in ("/", "/index.html"):
                f = STATIC_DIR / "index.html"
                if not f.exists():
                    return self._error("缺少前端文件 index.html", 500)
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
            try:
                return self._get(u.path, q)
            except Exception as e:
                return self._error(f"{type(e).__name__}: {e}", 500)

        def do_POST(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if not self._authorized(u.path, parse_qs(u.query)):
                return self._error("未授权：请带上 Authorization: Bearer <D2A_API_TOKEN>", 401)
            try:
                return self._post(u.path, self._body())
            except Exception as e:
                return self._error(f"{type(e).__name__}: {e}", 500)

        # ------------------------------------------------------------ GET
        def _get(self, path: str, q: Dict[str, list]) -> None:
            a = state.a
            with state.lock:
                if path == "/api/health":
                    # 给容器 healthcheck / 编排系统用的轻量探针：不做重活
                    b = a.book
                    ok = bool(b.heroes) and not b.validate()
                    return self._json(
                        {
                            "ok": ok,
                            "status": "healthy" if ok else "degraded",
                            "heroes": len(b.heroes),
                            "matchup_edges": sum(len(v) for v in b.matchups.values()),
                            "patch": b.meta_patch,
                            "auth_required": bool(api_token),
                        },
                        code=200 if ok else 503,
                    )
                if path == "/api/state":
                    return self._json(
                        {
                            "ok": True,
                            "draft": state.draft_payload(),
                            "pool": state.pool_payload(),
                            "data": state.data_payload(),
                            "heroes": [
                                {"name": h.name, "lanes": list(h.lanes), "attr": h.attr, "tags": list(h.tags)}
                                for h in a.book.heroes.values()
                            ],
                        }
                    )
                if path == "/api/recommend":
                    top = int(q.get("top", ["6"])[0])
                    pool_only = q.get("pool_only", ["1"])[0] not in ("0", "false")
                    strict_lane = q.get("strict_lane", ["0"])[0] in ("1", "true")
                    lane = q.get("lane", [""])[0]
                    lane_v = int(lane) if lane.isdigit() else None
                    cands = a.engine.recommend(
                        a.draft, top_n=top, lane=lane_v, pool_only=pool_only, strict_lane=strict_lane
                    )
                    return self._json(
                        {
                            "ok": True,
                            "mode": "pool" if pool_only else "all",
                            "position": lane_v if lane_v is not None else a.engine.preferred_lane(),
                            "candidates": [c.to_dict() for c in cands],
                            "draft": state.draft_payload(),
                        }
                    )
                if path == "/api/voice":
                    return self._json({"ok": True, **state.voice_payload()})
                if path == "/api/counter":
                    target = (q.get("hero", [""])[0] or "").strip()
                    name = a.book.resolve(target).name if target else None
                    cands = a.engine.counter_picks(a.draft, against=name, top_n=6)
                    return self._json({"ok": True, "against": name, "candidates": [c.to_dict() for c in cands]})
                if path == "/api/explain":
                    raw = (q.get("hero", [""])[0] or "").strip()
                    if not raw:
                        return self._error("缺少 hero 参数")
                    hero = a.book.resolve(raw)
                    c = a.engine.explain(hero.name, a.draft)
                    if c is None:
                        return self._error(f"{hero.name} 已被选走或 ban 掉")
                    return self._json({"ok": True, "candidate": c.to_dict(), "hero": hero.to_dict()})
                if path == "/api/heroes":
                    pat = (q.get("q", [""])[0] or "").strip()
                    out = []
                    for h in a.book.names():
                        if pat and pat.lower() not in h.lower():
                            continue
                        out.append(h)
                        if len(out) >= 40:
                            break
                    return self._json({"ok": True, "heroes": out})
                if path == "/api/search":
                    # 别名感知的补全：走 HeroBook.candidates（内含 527 条别名 + 出场率消歧）
                    raw = (q.get("q", [""])[0] or "").strip()
                    limit = int(q.get("limit", ["8"])[0])
                    if not raw:
                        return self._json({"ok": True, "candidates": []})
                    hits = a.book.candidates(raw, limit=max(1, min(limit, 30)))
                    return self._json(
                        {
                            "ok": True,
                            "candidates": [
                                {
                                    "name": h.name,
                                    "lanes": list(h.lanes),
                                    "attr": h.attr,
                                    "tags": list(h.tags),
                                    "pickrate": a.book.pickrate(h.name),
                                }
                                for h in hits
                            ],
                        }
                    )
                if path == "/api/pool":
                    return self._json({"ok": True, "pool": state.pool_payload()})
            return self._error("未知接口: " + path, 404)

        # ------------------------------------------------------------ POST
        def _post(self, path: str, body: dict) -> None:
            """变更类请求：先执行、再落盘、最后才回应。

            顺序很重要：如果先回应再落盘，客户端可能在写盘完成前就拿到 200，
            于是「改完立刻读配置」会读到旧内容（容器编排脚本很容易踩这个坑）。
            做法是把响应内容暂存下来，落盘后再真正写出 socket。
            """
            captured: list = []
            real_json = self._json

            def capture(payload, code=200):
                captured.append((payload, code))

            self._json = capture  # type: ignore[assignment]
            try:
                with state.lock:
                    self._post_locked(path, body)
                    if state._dirty:
                        try:
                            state.persist()
                        except Exception as e:
                            print(f"  持久化配置失败: {e}", file=sys.stderr)
            finally:
                self._json = real_json  # type: ignore[assignment]

            if captured:
                payload, code = captured[-1]
                return real_json(payload, code)
            return real_json({"ok": False, "error": "内部错误：未产生响应"}, 500)

        def _post_locked(self, path: str, body: dict) -> None:
            a = state.a
            if path == "/api/add":
                side = body.get("side", "ally")
                raw = (body.get("hero") or "").strip()
                if not raw:
                    return self._error("缺少英雄名")
                if side == "ban":
                    hero = a.book.resolve(raw)
                    if hero.name not in a.draft.bans:
                        a.draft.bans.append(hero.name)
                    return self._json({"ok": True, "draft": state.draft_payload()})
                hero = a.book.resolve(raw)
                lane = body.get("lane")
                a.draft.add(hero.name, side, int(lane) if lane else None)
                return self._json({"ok": True, "draft": state.draft_payload()})
            if path == "/api/remove":
                raw = (body.get("hero") or "").strip()
                hero = a.book.resolve(raw)
                if not a.draft.remove(hero.name) and hero.name in a.draft.bans:
                    a.draft.bans.remove(hero.name)
                return self._json({"ok": True, "draft": state.draft_payload()})
            if path == "/api/lane":
                lane = body.get("lane")
                if body.get("hero"):
                    hero = a.book.resolve(str(body["hero"]))
                    a.draft.set_lane(hero.name, int(lane))
                else:
                    a.draft.my_lane = int(lane) if lane else None
                return self._json({"ok": True, "draft": state.draft_payload()})
            if path == "/api/reset":
                a.draft.__init__()
                return self._json({"ok": True, "draft": state.draft_payload()})
            if path == "/api/voice":
                mode = str(body.get("mode") or "hero")
                seconds = int(body.get("seconds") or 0)
                return self._json({"ok": True, **state.voice_toggle(mode=mode, seconds=seconds)})
            if path == "/api/bracket":
                raw = body.get("bracket")
                a.engine.set_bracket(None if raw in ("", None, "all", "0") else raw)
                state.mark_dirty()   # 切片偏好要持久化
                return self._json(
                    {
                        "ok": True,
                        "bracket": a.engine.bracket,
                        "bracket_label": a.book.bracket_label(a.engine.bracket),
                        "data": state.data_payload(),
                    }
                )
            if path == "/api/pool":
                op = body.get("op")
                if op == "set":
                    raw = (body.get("hero") or "").strip()
                    hero = a.book.resolve(raw)
                    games = int(body.get("games") or 0)
                    wins = int(body.get("wins") or round(games / 2))
                    from ..data_loader import PlayerHeroStat

                    a.engine.pool[hero.name] = PlayerHeroStat(hero=hero.name, games=games, wins=min(wins, games))
                elif op == "remove":
                    hero = a.book.resolve(str(body.get("hero") or ""))
                    a.engine.pool.pop(hero.name, None)
                elif op == "clear":
                    a.engine.set_pool([])
                elif op == "import":
                    from ..steam_api import PublicDataClient, import_pool_from_opendota
                    from ..steam_id import parse_steam_input

                    account_id = parse_steam_input(str(body.get("steam") or ""))
                    cache = pathlib.Path(__file__).resolve().parents[2] / "data" / "cache"
                    recs = import_pool_from_opendota(
                        PublicDataClient(cache), account_id, a.book, min_games=int(body.get("min_games") or 3)
                    )
                    a.pool_from_records(recs)
                else:
                    return self._error("未知操作: " + str(op))
                state.mark_dirty()   # 英雄池要持久化（容器里指向挂载卷）
                return self._json({"ok": True, "pool": state.pool_payload()})
            if path == "/api/weights":
                a.set_weights(**{k: float(v) for k, v in body.items() if k in Weights().__dict__})
                state.mark_dirty()   # 权重偏好要持久化
                return self._json({"ok": True, "weights": state.data_payload()["weights"]})
            return self._error("未知接口: " + path, 404)

    return Handler


def serve(
    assistant: Assistant,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
    config_path: "pathlib.Path | None" = None,
    api_token: str = "",
) -> None:
    """启动面板。

    默认绑 ``127.0.0.1``（本机使用，不对外暴露）。容器里必须绑
    ``0.0.0.0`` 否则端口映射进不来——此时**强烈建议同时设置**
    ``api_token``，因为后端有一批能改状态的 POST 接口。
    """
    state = ApiState(assistant, config_path=config_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(state, api_token=api_token))
    url = f"http://{host}:{port}/"
    print(f"Dota2 选人助手面板已启动: {url}")
    if host in ("127.0.0.1", "localhost"):
        print("  （仅监听本机回环地址，不对外暴露；Ctrl+C 退出）")
    else:
        print(f"  （监听 {host}；容器内请访问映射后的宿主机端口）")
        if not api_token:
            print("  ⚠ 未设置 D2A_API_TOKEN：任何能访问该端口的人都能修改你的英雄池与权重")
        else:
            print("  🔒 已启用 Bearer token 鉴权（/api/health 除外）")
    if config_path:
        print(f"  配置：{config_path}")
    if open_browser:
        try:
            import webbrowser

            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        # 退出前把内存里的英雄池/权重落盘，容器重启后不丢
        try:
            state.persist()
        except Exception as e:
            print(f"  保存配置失败: {e}", file=sys.stderr)
        httpd.server_close()


def serve_from_env(assistant: Assistant) -> None:
    """按环境变量启动（容器入口用）。

    支持：
      D2A_HOST        默认 127.0.0.1（容器里通常设为 0.0.0.0）
      D2A_PORT        默认 8787
      D2A_API_TOKEN   设置后启用 Bearer 鉴权
      D2A_CONFIG      配置文件路径（同时作为持久化目标）
      D2A_OPEN_BROWSER  "1" 时尝试打开浏览器（容器里无意义，默认关）
    """
    host = os.environ.get("D2A_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("D2A_PORT", "8787"))
    except ValueError:
        port = 8787
    token = (os.environ.get("D2A_API_TOKEN") or "").strip()
    cfg = (os.environ.get("D2A_CONFIG") or "").strip()
    serve(
        assistant,
        host=host,
        port=port,
        open_browser=os.environ.get("D2A_OPEN_BROWSER") == "1",
        config_path=pathlib.Path(cfg) if cfg else None,
        api_token=token,
    )
