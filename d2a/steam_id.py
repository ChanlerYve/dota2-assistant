# -*- coding: utf-8 -*-
"""Steam / Dota2 账号标识解析（纯字符串计算，无网络）。

Dota2 的公开统计接口用的是 **account_id（32 位）**，而玩家平时拿到的是
64 位 SteamID 或个人主页链接。这里负责统一换算：

* ``76561198012345678``        -> 64 位 SteamID
* ``12345678``                 -> 32 位 account_id
* ``STEAM_0:1:1234567``        -> 旧式
* ``https://steamcommunity.com/id/xxx`` / ``/profiles/7656...``
"""

from __future__ import annotations

import re

STEAM64_BASE = 76561197960265728


class SteamIdError(ValueError):
    pass


def steam64_to_account_id(steam64: int) -> int:
    if steam64 <= STEAM64_BASE:
        raise SteamIdError(f"{steam64} 不是合法的 64 位 SteamID（应大于 {STEAM64_BASE}）")
    return steam64 - STEAM64_BASE


def account_id_to_steam64(account_id: int) -> int:
    return account_id + STEAM64_BASE


def parse_steam_input(text: str) -> int:
    """把各种写法解析成 32 位 account_id。"""
    if text is None:
        raise SteamIdError("Steam 输入为空")
    s = str(text).strip()
    if not s:
        raise SteamIdError("Steam 输入为空")

    # 个人主页链接
    m = re.search(r"steamcommunity\.com/profiles/(\d+)", s, re.I)
    if m:
        return steam64_to_account_id(int(m.group(1)))
    if re.search(r"steamcommunity\.com/id/", s, re.I):
        raise SteamIdError("自定义主页链接需要在 OpenDota 上查询后改用数字 ID（本工具不做网页爬取）")

    # STEAM_x:y:z
    m = re.match(r"^STEAM_[0-5]:([01]):(\d+)$", s, re.I)
    if m:
        y, z = int(m.group(1)), int(m.group(2))
        return z * 2 + y

    # 纯数字
    m = re.match(r"^\[?U:1:(\d+)\]?$", s, re.I)
    if m:
        return int(m.group(1))
    if re.match(r"^\d+$", s):
        v = int(s)
        if v > STEAM64_BASE:
            return steam64_to_account_id(v)
        if v <= 0:
            raise SteamIdError("account_id 必须为正整数")
        return v

    raise SteamIdError(f"无法识别的 Steam 标识: {text!r}")
