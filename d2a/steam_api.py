# -*- coding: utf-8 -*-
"""玩家资料与英雄池：只使用公开可查的数据。

两种录入方式
------------
1. **手工录入**（默认）：``config.json`` 里直接写你的英雄池，完全离线。
2. **公开 API 导入**（可选）：给一个 Steam32 位 ID，从 OpenDota 的公开接口
   读取该账号的历史对局英雄统计。这属于「查询公开数据」，不接触游戏进程。
   需要在本机设置环境变量 ``OPENDOTA_API_KEY``（可选，仅用于提高限流额度）。

安全边界
--------
本模块只做 HTTP GET 与本地文件读写；不注入游戏、不读取内存、不修改任何游戏文件。
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

USER_AGENT = "dota2-assistant/0.1 (personal draft helper; public data only)"
DEFAULT_TIMEOUT = 20
CACHE_TTL = 6 * 3600  # 6 小时


class ApiError(RuntimeError):
    pass


@dataclass
class Cache:
    path: pathlib.Path
    ttl: int = CACHE_TTL

    def get(self, key: str) -> Optional[Any]:
        f = self.path / (key + ".json")
        if not f.exists() or time.time() - f.stat().st_mtime > self.ttl:
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None

    def put(self, key: str, value: Any) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        f = self.path / (key + ".json")
        f.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class PublicDataClient:
    """极简 HTTP 客户端：带磁盘缓存、限流保护和明确错误信息。"""

    def __init__(
        self,
        cache_dir: pathlib.Path,
        ttl: int = CACHE_TTL,
        opendota_key: Optional[str] = None,
        valve_key: Optional[str] = None,
    ) -> None:
        self.cache = Cache(cache_dir, ttl=ttl)
        self.opendota_key = opendota_key or os.environ.get("OPENDOTA_API_KEY")
        self.valve_key = valve_key or os.environ.get("STEAM_API_KEY")
        self._last_call = 0.0

    # ------------------------------------------------------------------ HTTP
    def _throttle(self, min_interval: float = 1.1) -> None:
        wait = min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def get_json(
        self,
        url: str,
        cache_key: Optional[str] = None,
        use_cache: bool = True,
        min_interval: float = 1.1,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Any:
        if use_cache and cache_key:
            hit = self.cache.get(cache_key)
            if hit is not None:
                return hit
        self._throttle(min_interval)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise ApiError(f"HTTP {e.code} 于 {url}") from e
        except urllib.error.URLError as e:
            raise ApiError(f"网络不可达: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise ApiError(f"返回内容不是 JSON: {url}") from e
        if use_cache and cache_key:
            self.cache.put(cache_key, data)
        return data

    # ------------------------------------------------------- OpenDota 公开接口
    def opendota_heroes(self) -> List[dict]:
        return self.get_json("https://api.opendota.com/api/heroes", cache_key="opendota_heroes", min_interval=0.2)

    def opendota_hero_stats(self) -> List[dict]:
        return self.get_json(
            "https://api.opendota.com/api/heroStats", cache_key="opendota_hero_stats", min_interval=0.2
        )

    def opendota_matchups(self, hero_id: int) -> List[dict]:
        return self.get_json(
            f"https://api.opendota.com/api/heroes/{hero_id}/matchups",
            cache_key=f"opendota_matchups_{hero_id}",
            min_interval=1.2,
        )

    def opendota_player_heroes(self, account_id: int) -> List[dict]:
        key = f"?api_key={self.opendota_key}" if self.opendota_key else ""
        return self.get_json(
            f"https://api.opendota.com/api/players/{account_id}/heroes{key}",
            cache_key=f"opendota_player_heroes_{account_id}",
            min_interval=1.2,
        )

    def opendota_player(self, account_id: int) -> dict:
        return self.get_json(
            f"https://api.opendota.com/api/players/{account_id}",
            cache_key=f"opendota_player_{account_id}",
            min_interval=1.2,
        )

    # ------------------------------------------------------- Valve 官方 WebAPI
    def valve_player_heroes(self, account_id: int) -> List[dict]:
        """Steam WebAPI 的 hero 统计（需要 STEAM_API_KEY）。"""
        if not self.valve_key:
            raise ApiError("未设置 STEAM_API_KEY，无法调用 Valve WebAPI（可用 OpenDota 替代）")
        q = urllib.parse.urlencode(
            {"key": self.valve_key, "account_id": account_id, "hero_id": 0, "leagueid": 0, "game_mode": 0}
        )
        url = f"https://api.steampowered.com/IDOTA2Match_570/GetMatchHistory/v1/?{q}"
        data = self.get_json(url, cache_key=f"valve_history_{account_id}", min_interval=1.2)
        return data.get("result", {}).get("matches", [])

    # ------------------------------------------------------- Steam ID 解析
    def resolve_account_id(self, steam_id_or_url: str) -> int:
        """支持 32 位 ID、64 位 ID、STEAM_x:y:z 与个人主页链接。"""
        from .steam_id import parse_steam_input

        return parse_steam_input(steam_id_or_url)


def import_pool_from_opendota(
    client: PublicDataClient,
    account_id: int,
    book,
    min_games: int = 1,
    top_n: Optional[int] = None,
) -> List[dict]:
    """把 OpenDota 的玩家英雄统计转成本项目的英雄池记录。"""
    rows = client.opendota_player_heroes(account_id)
    heroes = {int(h["id"]): h["localized_name"] for h in client.opendota_heroes()}
    out: List[dict] = []
    for r in sorted(rows, key=lambda x: -int(x.get("games", 0))):
        hid = int(r.get("hero_id", 0))
        name = heroes.get(hid)
        if not name:
            continue
        g = int(r.get("games", 0))
        w = int(r.get("win", 0))
        if g < min_games:
            continue
        out.append({"hero": name, "games": g, "wins": w, "last_played": r.get("last_played")})
    if top_n:
        out = out[:top_n]
    return out
