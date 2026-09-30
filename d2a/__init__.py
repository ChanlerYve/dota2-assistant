# -*- coding: utf-8 -*-
"""dota2-assistant：对局 BP（选人）阶段的选人助手。

只用公开可查的数据给出建议，不读取游戏进程、不注入、不修改游戏文件。

典型用法::

    from d2a import Assistant

    a = Assistant.create()
    a.set_pool_simple({"Axe": (40, 24), "Magnus": (30, 19), "Crystal Maiden": (60, 33)})
    a.draft.add("Juggernaut", "enemy")
    a.draft.add("Luna", "enemy")
    for c in a.engine.recommend(a.draft, top_n=5):
        print(c.hero.name, round(c.score, 1), c.reasons)
"""

from .config import Config
from .data_loader import Hero, HeroBook, HeroNotFound, PlayerHeroStat
from .draft import Draft, Pick
from .engine import Assistant, Candidate, RecommendationEngine, Tuning, Weights

__version__ = "0.1.0"

__all__ = [
    "Assistant",
    "Candidate",
    "Config",
    "Draft",
    "Hero",
    "HeroBook",
    "HeroNotFound",
    "Pick",
    "PlayerHeroStat",
    "RecommendationEngine",
    "Tuning",
    "Weights",
    "__version__",
]
