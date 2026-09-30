# -*- coding: utf-8 -*-
"""生成 dota2-assistant 的离线英雄库快照 data/heroes.json。

设计说明
--------
本文件是**唯一的人工数据源**：英雄的属性、可用分路、伤害类型与能力标签
(traits) 都在这里声明，由脚本编译成 JSON，供评分引擎读取。

这样做的原因：能力标签（先手/团控/保人/推进...）没有稳定的公开 API 字段，
无法从 OpenDota 直接取，因此作为「内置离线数据」手工维护；
而版本胜率、克制矩阵这类大量且会变的数字，交给在线刷新脚本覆盖。

用法::

    python tools/gen_hero_data.py            # 重新生成 data/heroes.json
    python tools/gen_hero_data.py --diff     # 只统计与现有文件的差异
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Dict, List, Sequence, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "heroes.json"

# 能力标签键：取值均为 0-3（0 = 无 / 3 = 顶级）
#   lane      对线强度（压制 / 抗压能力）
#   init      先手开团能力
#   counter   反手 / 后排切入能力
#   teamfight 团战总输出与站位价值
#   control   硬控链（沉默、眩晕、缠绕、位移限制）
#   save      救人 / 保护核心能力
#   frontline 承担前排与吸收伤害能力
#   push      推塔 / 清线与兵线压力
#   sustain   续航（回复、吸血、回蓝）
#   scaling   后期成长上限
#   escape    逃生 / 生存机动性（辅助位保命能力也记在这里）
TRAIT_KEYS = ("lane", "init", "counter", "teamfight", "control", "save", "frontline", "push", "sustain", "scaling", "escape")

# 字段: name, attr, lanes, damage, tags, traits
#   attr   : str / agi / int / all
#   lanes  : 可分路 1=优势路核心 2=中路 3=劣势路 4=游走/辅助 5=纯辅助
#   damage : physical / magic / pure / mixed
#   tags   : carry, mid, offlane, support, roamer, initiator, nuker, durable, escape, pusher, disable
#   traits : dict，只需写非 0 的键；未写的键默认 0
Hero = Tuple[str, str, Sequence[int], str, Sequence[str], Dict[str, int]]

_HEROES: List[Hero] = [
    # ---------------- 力量 ----------------
    ("Alchemist", "str", [1, 2], "physical", ["carry", "mid", "durable"], {"lane": 2, "frontline": 2, "sustain": 3, "scaling": 3, "push": 1}),
    ("Axe", "str", [3], "pure", ["offlane", "initiator", "durable", "disable"], {"lane": 2, "init": 3, "counter": 2, "teamfight": 3, "control": 3, "frontline": 3, "push": 1}),
    ("Bristleback", "str", [3], "physical", ["offlane", "durable"], {"lane": 3, "teamfight": 2, "frontline": 3, "sustain": 2, "scaling": 2}),
    ("Centaur Warrunner", "str", [3], "magic", ["offlane", "initiator", "durable", "disable"], {"lane": 2, "init": 3, "teamfight": 2, "control": 3, "frontline": 3}),
    ("Chaos Knight", "str", [1], "physical", ["carry", "durable", "pusher", "disable"], {"lane": 2, "init": 2, "teamfight": 3, "control": 2, "frontline": 2, "push": 2, "scaling": 2}),
    ("Dawnbreaker", "str", [3, 4], "magic", ["offlane", "support", "durable", "initiator"], {"lane": 3, "init": 2, "counter": 2, "teamfight": 3, "control": 1, "save": 2, "frontline": 3, "sustain": 2}),
    ("Doom", "str", [3], "pure", ["offlane", "initiator", "durable", "disable"], {"lane": 2, "init": 2, "control": 3, "frontline": 3, "sustain": 2}),
    ("Dragon Knight", "str", [2, 3], "mixed", ["mid", "offlane", "durable", "pusher", "disable"], {"lane": 3, "init": 2, "teamfight": 2, "control": 2, "frontline": 2, "push": 2}),
    ("Earth Spirit", "str", [4], "magic", ["roamer", "support", "initiator", "disable", "escape"], {"lane": 2, "init": 3, "counter": 2, "teamfight": 2, "control": 3, "save": 1}),
    ("Earthshaker", "str", [4, 5], "magic", ["support", "initiator", "disable", "nuker"], {"lane": 1, "init": 3, "counter": 3, "teamfight": 3, "control": 3, "frontline": 1}),
    ("Elder Titan", "str", [4, 5], "mixed", ["support", "initiator", "disable", "nuker"], {"lane": 1, "init": 2, "counter": 3, "teamfight": 3, "control": 3}),
    ("Huskar", "str", [2, 3], "mixed", ["mid", "offlane", "durable", "nuker"], {"lane": 2, "teamfight": 2, "frontline": 2, "sustain": 3, "scaling": 1}),
    ("Kunkka", "str", [2, 3, 4], "mixed", ["mid", "offlane", "initiator", "nuker", "disable"], {"lane": 2, "init": 3, "teamfight": 3, "control": 2, "frontline": 2, "push": 1}),
    ("Kez", "agi", [1, 2], "physical", ["carry", "mid", "escape", "nuker", "disable"], {"lane": 3, "init": 2, "counter": 2, "teamfight": 2, "control": 2, "scaling": 2}),
    ("Largo", "str", [4, 5], "magic", ["support", "roamer", "initiator", "disable", "durable", "nuker"], {"lane": 3, "init": 3, "counter": 2, "teamfight": 3, "control": 3, "save": 2, "frontline": 2, "sustain": 2}),
    ("Legion Commander", "str", [3], "physical", ["offlane", "initiator", "durable", "disable"], {"lane": 2, "init": 2, "control": 3, "frontline": 3, "sustain": 2}),
    ("Lifestealer", "str", [1], "physical", ["carry", "durable", "escape"], {"lane": 2, "teamfight": 2, "frontline": 2, "sustain": 3, "scaling": 2}),
    ("Lone Druid", "agi", [1, 3], "physical", ["carry", "offlane", "pusher", "durable"], {"lane": 2, "teamfight": 2, "frontline": 2, "push": 3, "sustain": 2, "scaling": 2}),
    ("Mars", "str", [3], "mixed", ["offlane", "initiator", "durable", "disable"], {"lane": 3, "init": 3, "counter": 2, "teamfight": 3, "control": 3, "frontline": 3}),
    ("Night Stalker", "str", [3], "mixed", ["offlane", "roamer", "initiator", "durable"], {"lane": 2, "init": 2, "counter": 3, "control": 1, "frontline": 3}),
    ("Ogre Magi", "str", [4, 5], "magic", ["support", "nuker", "durable", "disable"], {"lane": 3, "teamfight": 2, "control": 2, "frontline": 2, "sustain": 1}),
    ("Omniknight", "str", [4, 5], "pure", ["support", "durable", "disable"], {"lane": 2, "teamfight": 2, "save": 3, "frontline": 2, "sustain": 2}),
    ("Primal Beast", "str", [3, 4], "magic", ["offlane", "initiator", "durable", "disable"], {"lane": 2, "init": 3, "counter": 2, "teamfight": 3, "control": 3, "frontline": 3}),
    ("Pudge", "str", [4, 5], "pure", ["roamer", "support", "initiator", "durable", "disable"], {"lane": 2, "init": 3, "control": 3, "frontline": 2, "sustain": 1}),
    ("Slardar", "str", [3], "physical", ["offlane", "initiator", "durable", "disable", "escape"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 2, "control": 2, "frontline": 2}),
    ("Spirit Breaker", "str", [4], "physical", ["roamer", "support", "initiator", "durable", "disable"], {"lane": 1, "init": 3, "counter": 3, "teamfight": 2, "control": 2, "frontline": 2}),
    ("Sven", "str", [1], "physical", ["carry", "durable", "disable", "pusher"], {"lane": 2, "init": 2, "teamfight": 3, "control": 1, "frontline": 2, "push": 1, "scaling": 2}),
    ("Tidehunter", "str", [3], "magic", ["offlane", "initiator", "durable", "disable"], {"lane": 2, "init": 3, "counter": 3, "teamfight": 3, "control": 3, "frontline": 3}),
    ("Timbersaw", "str", [3], "pure", ["offlane", "durable", "escape", "nuker"], {"lane": 1, "teamfight": 2, "frontline": 2, "sustain": 2, "scaling": 2}),
    ("Tiny", "str", [2, 4], "mixed", ["mid", "support", "initiator", "nuker", "disable"], {"lane": 2, "init": 3, "counter": 2, "teamfight": 2, "control": 2, "push": 2}),
    ("Treant Protector", "str", [4, 5], "magic", ["support", "disable", "durable"], {"lane": 3, "init": 2, "counter": 2, "teamfight": 3, "control": 3, "save": 2, "frontline": 1, "sustain": 1}),
    ("Tusk", "str", [4], "mixed", ["roamer", "support", "initiator", "disable"], {"lane": 2, "init": 3, "counter": 2, "control": 2, "save": 2, "frontline": 1}),
    ("Underlord", "str", [3], "magic", ["offlane", "durable", "pusher", "disable"], {"lane": 3, "teamfight": 2, "control": 2, "frontline": 3, "push": 3, "sustain": 1}),
    ("Undying", "str", [4, 5], "magic", ["support", "durable", "initiator"], {"lane": 3, "teamfight": 3, "frontline": 2, "sustain": 2}),
    ("Wraith King", "str", [1], "physical", ["carry", "durable", "initiator", "disable"], {"lane": 2, "init": 2, "teamfight": 2, "control": 1, "frontline": 3, "sustain": 2, "scaling": 2}),

    # ---------------- 敏捷 ----------------
    ("Anti-Mage", "agi", [1], "physical", ["carry", "escape"], {"lane": 2, "counter": 1, "scaling": 3}),
    ("Arc Warden", "agi", [1, 2], "magic", ["carry", "mid", "escape", "pusher"], {"lane": 1, "teamfight": 2, "push": 3, "scaling": 3}),
    ("Bloodseeker", "agi", [1, 3], "physical", ["carry", "offlane", "nuker", "disable"], {"lane": 2, "init": 2, "teamfight": 2, "control": 1, "sustain": 2, "scaling": 2}),
    ("Bounty Hunter", "agi", [4], "physical", ["roamer", "support", "escape", "nuker"], {"lane": 2, "counter": 2, "control": 1}),
    ("Clinkz", "agi", [1, 2], "physical", ["carry", "mid", "escape", "pusher"], {"lane": 2, "counter": 2, "push": 3, "scaling": 2}),
    ("Drow Ranger", "agi", [1], "physical", ["carry", "disable", "pusher"], {"lane": 3, "teamfight": 3, "control": 1, "push": 2, "scaling": 3}),
    ("Ember Spirit", "agi", [2], "mixed", ["mid", "carry", "escape", "nuker", "initiator"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 3, "control": 1, "scaling": 3}),
    ("Faceless Void", "agi", [1], "physical", ["carry", "initiator", "disable", "escape"], {"lane": 2, "init": 3, "counter": 2, "teamfight": 3, "control": 3, "scaling": 3}),
    ("Gyrocopter", "agi", [1], "mixed", ["carry", "nuker", "pusher"], {"lane": 3, "teamfight": 3, "push": 1, "scaling": 2}),
    ("Hoodwink", "agi", [4, 5], "magic", ["support", "nuker", "escape", "disable"], {"lane": 3, "init": 2, "counter": 2, "teamfight": 2, "control": 2}),
    ("Juggernaut", "agi", [1], "physical", ["carry", "escape", "pusher"], {"lane": 3, "teamfight": 2, "sustain": 2, "scaling": 3}),
    ("Luna", "agi", [1], "mixed", ["carry", "nuker", "pusher"], {"lane": 3, "teamfight": 3, "push": 2, "scaling": 3}),
    ("Medusa", "agi", [1], "physical", ["carry", "durable", "disable"], {"lane": 2, "teamfight": 3, "control": 2, "frontline": 2, "push": 1, "scaling": 3}),
    ("Meepo", "agi", [2], "physical", ["mid", "carry", "escape", "pusher", "disable"], {"lane": 1, "init": 2, "teamfight": 2, "control": 2, "push": 3, "scaling": 3}),
    ("Monkey King", "agi", [1, 2], "physical", ["carry", "mid", "escape", "nuker", "disable"], {"lane": 3, "init": 2, "counter": 2, "teamfight": 3, "control": 1, "scaling": 2}),
    ("Morphling", "agi", [1, 2], "magic", ["carry", "mid", "escape", "nuker", "durable"], {"lane": 2, "escape": 3, "teamfight": 2, "save": 1, "scaling": 3}),
    ("Naga Siren", "agi", [1], "physical", ["carry", "disable", "pusher", "escape"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 2, "control": 3, "push": 2, "scaling": 3}),
    ("Phantom Assassin", "agi", [1], "physical", ["carry", "escape"], {"lane": 2, "counter": 2, "scaling": 3}),
    ("Phantom Lancer", "agi", [1], "physical", ["carry", "escape", "pusher"], {"lane": 2, "teamfight": 2, "push": 2, "scaling": 3}),
    ("Razor", "agi", [1, 2, 3], "mixed", ["carry", "mid", "offlane"], {"lane": 3, "teamfight": 2, "sustain": 1, "scaling": 2}),
    ("Riki", "agi", [1, 4], "physical", ["carry", "roamer", "escape", "disable"], {"lane": 1, "counter": 2, "control": 2, "scaling": 2}),
    ("Ringmaster", "int", [4, 5], "magic", ["support", "nuker", "disable", "initiator"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 2, "control": 3, "save": 1}),
    ("Slark", "agi", [1], "physical", ["carry", "escape"], {"lane": 2, "counter": 2, "teamfight": 2, "sustain": 3, "scaling": 3}),
    ("Sniper", "agi", [1, 2], "physical", ["carry", "mid", "nuker", "pusher"], {"lane": 2, "teamfight": 2, "push": 2, "scaling": 3}),
    ("Spectre", "agi", [1], "physical", ["carry", "escape", "durable"], {"lane": 1, "init": 2, "counter": 2, "teamfight": 3, "frontline": 1, "scaling": 3}),
    ("Templar Assassin", "agi", [2], "physical", ["mid", "carry", "escape", "nuker"], {"lane": 3, "teamfight": 2, "push": 2, "scaling": 2}),
    ("Terrorblade", "agi", [1], "physical", ["carry", "pusher", "nuker"], {"lane": 2, "teamfight": 2, "save": 1, "push": 3, "scaling": 3}),
    ("Troll Warlord", "agi", [1], "physical", ["carry", "disable", "pusher", "durable"], {"lane": 2, "teamfight": 2, "control": 1, "push": 2, "sustain": 2, "scaling": 3}),
    ("Ursa", "agi", [1], "physical", ["carry", "durable", "nuker"], {"lane": 3, "teamfight": 2, "frontline": 2, "scaling": 2}),
    ("Viper", "agi", [1, 2, 3], "mixed", ["carry", "mid", "offlane", "durable"], {"lane": 3, "teamfight": 2, "control": 1, "sustain": 2, "scaling": 2}),
    ("Weaver", "agi", [1, 4], "physical", ["carry", "support", "escape", "nuker"], {"lane": 2, "counter": 2, "teamfight": 1, "save": 2, "scaling": 2}),

    # ---------------- 智力 ----------------
    ("Ancient Apparition", "int", [4, 5], "magic", ["support", "nuker", "disable"], {"lane": 2, "init": 2, "teamfight": 3, "control": 2}),
    ("Chen", "int", [4, 5], "magic", ["support", "pusher", "nuker"], {"lane": 2, "teamfight": 2, "save": 2, "push": 3, "sustain": 3}),
    ("Crystal Maiden", "int", [5], "magic", ["support", "nuker", "disable"], {"lane": 1, "init": 1, "counter": 2, "teamfight": 3, "control": 2, "sustain": 1}),
    ("Dark Seer", "int", [3], "magic", ["offlane", "initiator", "escape", "pusher"], {"lane": 2, "init": 2, "counter": 3, "teamfight": 2, "control": 1, "push": 2}),
    ("Dark Willow", "int", [4, 5], "magic", ["support", "nuker", "disable", "escape"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 2, "control": 3}),
    ("Dazzle", "int", [4, 5], "mixed", ["support", "nuker", "disable"], {"lane": 3, "counter": 2, "teamfight": 2, "control": 2, "save": 3, "sustain": 3}),
    ("Disruptor", "int", [4, 5], "magic", ["support", "nuker", "disable"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 2, "control": 3, "save": 1}),
    ("Enchantress", "int", [4, 5], "pure", ["support", "pusher", "nuker", "durable"], {"lane": 3, "teamfight": 2, "save": 1, "push": 2, "sustain": 2}),
    ("Grimstroke", "int", [4, 5], "magic", ["support", "nuker", "disable"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 2, "control": 2, "save": 2}),
    ("Invoker", "int", [2], "mixed", ["mid", "nuker", "disable", "pusher", "escape"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 3, "control": 2, "push": 2, "scaling": 3}),
    ("Jakiro", "int", [4, 5], "magic", ["support", "nuker", "pusher", "disable"], {"lane": 3, "teamfight": 3, "control": 2, "push": 3}),
    ("Keeper of the Light", "int", [4, 5], "magic", ["support", "nuker", "pusher"], {"lane": 3, "counter": 2, "teamfight": 2, "save": 1, "push": 3, "sustain": 2}),
    ("Leshrac", "int", [2, 3], "magic", ["mid", "offlane", "nuker", "pusher", "disable"], {"lane": 2, "teamfight": 3, "control": 2, "frontline": 1, "push": 3}),
    ("Lich", "int", [5], "magic", ["support", "nuker", "disable"], {"lane": 3, "teamfight": 3, "control": 1}),
    ("Lina", "int", [2, 4], "magic", ["mid", "support", "nuker", "disable", "pusher"], {"lane": 3, "teamfight": 3, "control": 1, "push": 2, "scaling": 2}),
    ("Lion", "int", [4, 5], "magic", ["support", "nuker", "disable", "initiator"], {"lane": 2, "init": 3, "counter": 2, "teamfight": 2, "control": 3}),
    ("Muerta", "int", [1, 2], "mixed", ["carry", "mid", "nuker", "disable"], {"lane": 2, "teamfight": 2, "control": 1, "scaling": 2}),
    ("Necrophos", "int", [2, 3], "magic", ["mid", "offlane", "durable", "nuker"], {"lane": 3, "teamfight": 3, "sustain": 3, "frontline": 2, "scaling": 2}),
    ("Oracle", "int", [4, 5], "magic", ["support", "nuker", "disable"], {"lane": 2, "counter": 2, "teamfight": 2, "control": 2, "save": 3}),
    ("Outworld Destroyer", "int", [2], "pure", ["mid", "nuker", "disable"], {"lane": 3, "teamfight": 2, "control": 2, "scaling": 2}),
    ("Puck", "int", [2], "magic", ["mid", "initiator", "nuker", "escape", "disable"], {"lane": 3, "init": 3, "counter": 2, "teamfight": 3, "control": 3}),
    ("Pugna", "int", [2, 4], "magic", ["mid", "support", "nuker", "pusher"], {"lane": 2, "counter": 2, "teamfight": 2, "save": 2, "push": 3}),
    ("Queen of Pain", "int", [2], "magic", ["mid", "nuker", "escape"], {"lane": 3, "counter": 2, "teamfight": 2, "scaling": 2}),
    ("Shadow Fiend", "agi", [2], "magic", ["mid", "nuker", "pusher"], {"lane": 3, "teamfight": 3, "push": 2, "scaling": 3}),
    ("Death Prophet", "int", [2, 3], "magic", ["mid", "offlane", "pusher", "nuker", "durable"], {"lane": 3, "teamfight": 3, "control": 1, "frontline": 2, "push": 3, "sustain": 2, "scaling": 2}),
    ("Rubick", "int", [4, 5], "magic", ["support", "nuker", "disable"], {"lane": 2, "init": 2, "counter": 3, "teamfight": 2, "control": 2, "save": 1}),
    ("Shadow Demon", "int", [4, 5], "magic", ["support", "nuker", "disable", "initiator"], {"lane": 2, "init": 2, "counter": 3, "teamfight": 2, "control": 2, "save": 1}),
    ("Shadow Shaman", "int", [4, 5], "magic", ["support", "nuker", "disable", "pusher", "initiator"], {"lane": 2, "init": 2, "control": 3, "push": 3}),
    ("Silencer", "int", [4, 5], "magic", ["support", "nuker", "disable"], {"lane": 2, "counter": 3, "teamfight": 3, "control": 3}),
    ("Skywrath Mage", "int", [4, 5], "magic", ["support", "nuker", "disable"], {"lane": 3, "counter": 2, "teamfight": 1, "control": 2}),
    ("Storm Spirit", "int", [2], "magic", ["mid", "nuker", "escape", "initiator", "disable"], {"lane": 2, "init": 3, "counter": 3, "teamfight": 2, "control": 1, "scaling": 3}),
    ("Techies", "int", [3, 4], "magic", ["offlane", "support", "nuker", "pusher"], {"lane": 1, "counter": 2, "teamfight": 2, "control": 1, "push": 2}),
    ("Tinker", "int", [2], "magic", ["mid", "nuker", "pusher"], {"lane": 2, "teamfight": 3, "push": 3, "scaling": 2}),
    ("Warlock", "int", [4, 5], "magic", ["support", "initiator", "nuker", "disable"], {"lane": 3, "init": 3, "counter": 2, "teamfight": 3, "control": 2, "push": 1}),
    ("Winter Wyvern", "int", [4, 5], "magic", ["support", "nuker", "disable"], {"lane": 3, "counter": 3, "teamfight": 3, "control": 3, "save": 2}),
    ("Witch Doctor", "int", [5], "magic", ["support", "nuker", "disable"], {"lane": 2, "counter": 2, "teamfight": 3, "control": 2}),
    ("Zeus", "int", [2, 4], "magic", ["mid", "support", "nuker"], {"lane": 2, "counter": 2, "teamfight": 2, "scaling": 2}),

    # ---------------- 全属性 ----------------
    ("Abaddon", "all", [3, 4], "magic", ["offlane", "support", "durable", "escape"], {"lane": 2, "counter": 2, "teamfight": 2, "save": 3, "frontline": 2, "sustain": 2}),
    ("Bane", "all", [4, 5], "pure", ["support", "disable", "nuker"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 1, "control": 3, "save": 1}),
    ("Batrider", "all", [2, 3], "magic", ["mid", "offlane", "initiator", "escape", "disable"], {"lane": 2, "init": 3, "counter": 2, "teamfight": 2, "control": 3}),
    ("Beastmaster", "all", [3], "mixed", ["offlane", "initiator", "durable", "pusher", "disable"], {"lane": 3, "init": 3, "counter": 2, "teamfight": 2, "control": 2, "frontline": 2, "push": 3}),
    ("Brewmaster", "all", [3], "mixed", ["offlane", "initiator", "durable", "disable"], {"lane": 3, "init": 3, "counter": 2, "teamfight": 3, "control": 3, "frontline": 3}),
    ("Broodmother", "all", [2, 3], "physical", ["mid", "offlane", "pusher", "escape", "nuker"], {"lane": 3, "push": 3, "sustain": 3, "scaling": 2}),
    ("Clockwerk", "all", [3, 4], "magic", ["offlane", "roamer", "initiator", "durable", "disable"], {"lane": 2, "init": 3, "counter": 2, "teamfight": 2, "control": 2, "frontline": 2}),
    ("Enigma", "all", [3], "pure", ["offlane", "initiator", "pusher", "disable"], {"lane": 1, "init": 3, "counter": 3, "teamfight": 3, "control": 3, "push": 3}),
    ("Io", "all", [4, 5], "magic", ["support", "escape", "nuker"], {"lane": 2, "teamfight": 2, "save": 3, "sustain": 3}),
    ("Lycan", "all", [1, 3], "physical", ["carry", "offlane", "pusher", "durable"], {"lane": 2, "teamfight": 2, "frontline": 2, "push": 3, "sustain": 1, "scaling": 2}),
    ("Magnus", "all", [3, 4], "magic", ["offlane", "support", "initiator", "disable"], {"lane": 2, "init": 3, "counter": 3, "teamfight": 3, "control": 3, "save": 1}),
    ("Marci", "all", [4], "physical", ["roamer", "support", "initiator", "durable", "disable"], {"lane": 3, "init": 3, "counter": 2, "teamfight": 2, "control": 2, "save": 2, "frontline": 2}),
    ("Mirana", "all", [4, 5], "magic", ["support", "roamer", "nuker", "escape", "disable"], {"lane": 2, "init": 2, "counter": 1, "teamfight": 2, "control": 2, "save": 1}),
    ("Nature's Prophet", "all", [1, 4], "mixed", ["carry", "support", "pusher", "escape", "nuker"], {"lane": 1, "push": 3, "scaling": 2}),
    ("Nyx Assassin", "all", [4], "magic", ["roamer", "support", "nuker", "disable", "escape"], {"lane": 1, "init": 2, "counter": 3, "control": 3}),
    ("Pangolier", "all", [2, 3], "mixed", ["mid", "offlane", "initiator", "escape", "disable"], {"lane": 2, "init": 3, "counter": 2, "teamfight": 3, "control": 3}),
    ("Phoenix", "all", [3, 4], "magic", ["offlane", "support", "initiator", "nuker", "durable"], {"lane": 2, "init": 3, "counter": 3, "teamfight": 3, "control": 1, "save": 2, "sustain": 2}),
    ("Sand King", "all", [3, 4], "magic", ["offlane", "support", "initiator", "nuker", "escape", "disable"], {"lane": 2, "init": 3, "counter": 3, "teamfight": 3, "control": 2}),
    ("Snapfire", "all", [4, 5], "magic", ["support", "nuker", "disable"], {"lane": 2, "init": 2, "counter": 2, "teamfight": 2, "control": 2, "save": 1}),
    ("Terrorblade", "all", [1], "physical", ["carry"], {"scaling": 3}),
    ("Vengeful Spirit", "all", [4, 5], "magic", ["support", "initiator", "nuker", "disable"], {"lane": 2, "init": 2, "counter": 3, "teamfight": 2, "control": 2, "save": 1}),
    ("Venomancer", "all", [3, 4], "magic", ["offlane", "support", "pusher", "nuker", "disable"], {"lane": 3, "teamfight": 2, "control": 2, "push": 3}),
    ("Visage", "all", [2, 4], "mixed", ["mid", "support", "pusher", "nuker", "durable"], {"lane": 2, "teamfight": 2, "push": 3, "scaling": 2}),
    ("Void Spirit", "all", [2], "magic", ["mid", "escape", "nuker", "initiator", "disable"], {"lane": 3, "init": 2, "counter": 3, "teamfight": 2, "control": 1}),
    ("Windranger", "all", [2, 4], "mixed", ["mid", "support", "nuker", "disable", "escape"], {"lane": 3, "init": 1, "counter": 2, "control": 2, "push": 1}),
]


def build() -> dict:
    heroes = []
    seen: Dict[str, int] = {}
    for name, attr, lanes, damage, tags, traits in _HEROES:
        if name in seen:
            # 允许同一英雄出现在多段列表中（便于按属性分组维护）：合并而非重复
            continue
        seen[name] = len(heroes)
        clean = {k: int(v) for k, v in traits.items() if k in TRAIT_KEYS and int(v) > 0}
        heroes.append(
            {
                "name": name,
                "attr": attr,
                "lanes": sorted(set(int(x) for x in lanes)),
                "damage": damage,
                "tags": sorted(set(tags)),
                "traits": {k: clean.get(k, 0) for k in TRAIT_KEYS},
            }
        )
    heroes.sort(key=lambda h: h["name"])
    return {
        "schema": 1,
        "source": "hand-curated + open data merge",
        "note": "traits 为人工维护的能力标签(0-3)，版本强度/克制矩阵由 tools/refresh_data.py 从公开 API 覆盖",
        "trait_keys": list(TRAIT_KEYS),
        "lanes_note": {"1": "优势路核心", "2": "中路", "3": "劣势路", "4": "游走/4号位辅助", "5": "5号位辅助"},
        "heroes": heroes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", action="store_true", help="只对比现有文件，不写入")
    args = ap.parse_args()

    data = build()
    names = [h["name"] for h in data["heroes"]]
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)

    if args.diff and OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8"))
        old_names = {h["name"] for h in old.get("heroes", [])}
        print(f"现有 {len(old_names)} 个英雄，新生成 {len(names)} 个")
        print("新增:", sorted(set(names) - old_names))
        print("移除:", sorted(old_names - set(names)))
        return 0

    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"已写入 {OUT}（{len(names)} 个英雄，{OUT.stat().st_size / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
