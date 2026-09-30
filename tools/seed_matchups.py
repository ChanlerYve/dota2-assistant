# -*- coding: utf-8 -*-
"""克制 / 协同矩阵的**人工种子数据**。

这是离线可用性的关键：没有网络时，引擎靠这份人工矩阵给出建议。
在线刷新（tools/refresh_data.py）会用 OpenDota 的真实对位胜率覆盖同名字段，
覆盖时以真实数据为准，本文件只作为兜底与冷启动。

数值约定
--------
``counter_pairs``: ``{英雄 A: [(英雄 B, 优势值), ...]}``
    「A 打 B 的优势值」，单位 = 胜率差（百分点 / 100）。
    +0.05 表示 A 对 B 大约有 5 个百分点的胜率优势，属于明显克制；
    +0.01~0.02 属于轻微优势，不写也不影响大局。
    查询时引擎会同时看 A→B 与 B→A（反向取负），所以同一条关系只写一次。

``synergy_pairs``: ``{英雄 A: [(英雄 B, 协同值), ...]}``
    「A 与 B 同队时的加成」，单位同上，通常是控制链 / 组合技。

标注用途
--------
* 数值用于给玩家解释「为什么推荐」，不是精确预言，UI 里以星级呈现。
* 想调平衡只改这里，然后运行 ``python tools/gen_matchup_data.py``。
"""

# fmt: off
counter_pairs = {
    "Anti-Mage": [("Axe", 0.055), ("Bloodseeker", 0.04), ("Legion Commander", 0.035), ("Faceless Void", 0.04), ("Slardar", 0.03), ("Bane", 0.03), ("Nyx Assassin", 0.025)],
    "Axe": [("Ursa", 0.04), ("Juggernaut", 0.035), ("Anti-Mage", 0.045), ("Faceless Void", 0.03), ("Troll Warlord", 0.03), ("Legion Commander", 0.03), ("Slark", 0.03)],
    "Spectre": [("Slardar", 0.05), ("Bounty Hunter", 0.045), ("Anti-Mage", 0.04), ("Doom", 0.035), ("Nyx Assassin", 0.03), ("Legion Commander", 0.03)],
    "Medusa": [("Anti-Mage", 0.065), ("Nyx Assassin", 0.05), ("Invoker", 0.045), ("Silencer", 0.04), ("Keeper of the Light", 0.03), ("Lion", 0.03), ("Shadow Demon", 0.03)],
    "Terrorblade": [("Leshrac", 0.045), ("Tiny", 0.04), ("Kunkka", 0.035), ("Sand King", 0.035), ("Timbersaw", 0.03)],
    "Luna": [("Axe", 0.04), ("Legion Commander", 0.04), ("Enigma", 0.035), ("Magnus", 0.04), ("Winter Wyvern", 0.03), ("Tidehunter", 0.035)],
    "Gyrocopter": [("Axe", 0.045), ("Legion Commander", 0.04), ("Enigma", 0.04), ("Batrider", 0.035), ("Winter Wyvern", 0.03)],
    "Juggernaut": [("Winter Wyvern", 0.035), ("Axe", 0.045), ("Bane", 0.03), ("Shadow Demon", 0.03), ("Legion Commander", 0.03), ("Venomancer", 0.03)],
    "Morphling": [("Ancient Apparition", 0.055), ("Nyx Assassin", 0.05), ("Lion", 0.045), ("Shadow Shaman", 0.035), ("Bane", 0.03), ("Silencer", 0.03)],
    "Templar Assassin": [("Batrider", 0.045), ("Tinker", 0.045), ("Pudge", 0.04), ("Sand King", 0.035), ("Earthshaker", 0.03), ("Venomancer", 0.03)],
    "Storm Spirit": [("Ancient Apparition", 0.045), ("Doom", 0.045), ("Lion", 0.04), ("Shadow Shaman", 0.035), ("Nyx Assassin", 0.03)],
    "Queen of Pain": [("Silencer", 0.035), ("Nyx Assassin", 0.03), ("Bloodseeker", 0.03), ("Clockwerk", 0.03), ("Anti-Mage", 0.03)],
    "Necrophos": [("Ancient Apparition", 0.055), ("Anti-Mage", 0.04), ("Nyx Assassin", 0.035), ("Doom", 0.03)],
    "Timbersaw": [("Necrophos", 0.045), ("Ancient Apparition", 0.035), ("Silencer", 0.03), ("Nyx Assassin", 0.03), ("Outworld Destroyer", 0.03)],
    "Bristleback": [("Ancient Apparition", 0.04), ("Necrophos", 0.035), ("Viper", 0.035), ("Timbersaw", 0.03), ("Silencer", 0.03)],
    "Wraith King": [("Anti-Mage", 0.04), ("Necrophos", 0.035), ("Timbersaw", 0.03), ("Invoker", 0.03)],
    "Chaos Knight": [("Earthshaker", 0.055), ("Sand King", 0.045), ("Magnus", 0.04), ("Enigma", 0.04), ("Tidehunter", 0.035)],
    "Phantom Lancer": [("Earthshaker", 0.05), ("Sand King", 0.045), ("Leshrac", 0.04), ("Kunkka", 0.04), ("Tinker", 0.035)],
    "Naga Siren": [("Earthshaker", 0.045), ("Sand King", 0.04), ("Leshrac", 0.04), ("Kunkka", 0.035)],
    "Meepo": [("Earthshaker", 0.065), ("Sand King", 0.055), ("Leshrac", 0.05), ("Kunkka", 0.045), ("Tinker", 0.045), ("Anti-Mage", 0.04)],
    "Broodmother": [("Sand King", 0.05), ("Kunkka", 0.045), ("Timbersaw", 0.04), ("Bristleback", 0.04), ("Earthshaker", 0.035), ("Bounty Hunter", 0.03)],
    "Nature's Prophet": [("Bounty Hunter", 0.04), ("Timbersaw", 0.035), ("Storm Spirit", 0.03)],
    "Lycan": [("Bristleback", 0.035), ("Sand King", 0.03), ("Timbersaw", 0.035), ("Axe", 0.03)],
    "Sniper": [("Spirit Breaker", 0.05), ("Storm Spirit", 0.05), ("Tusk", 0.045), ("Pudge", 0.04), ("Clockwerk", 0.045), ("Night Stalker", 0.04), ("Slark", 0.04), ("Batrider", 0.04)],
    "Drow Ranger": [("Spirit Breaker", 0.055), ("Storm Spirit", 0.05), ("Clockwerk", 0.045), ("Tusk", 0.045), ("Night Stalker", 0.045), ("Batrider", 0.04), ("Riki", 0.035)],
    "Lifestealer": [("Clockwerk", 0.04), ("Kunkka", 0.035), ("Batrider", 0.035), ("Enigma", 0.03), ("Winter Wyvern", 0.03)],
    "Legion Commander": [("Winter Wyvern", 0.04), ("Weaver", 0.04), ("Storm Spirit", 0.045), ("Ember Spirit", 0.04), ("Bane", 0.03), ("Clockwerk", 0.03)],
    "Undying": [("Ancient Apparition", 0.04), ("Lifestealer", 0.035), ("Necrophos", 0.03), ("Templar Assassin", 0.03)],
    "Pudge": [("Ancient Apparition", 0.035), ("Anti-Mage", 0.03), ("Storm Spirit", 0.03), ("Weaver", 0.035), ("Morphling", 0.03)],
    "Warlock": [("Spirit Breaker", 0.035), ("Storm Spirit", 0.03), ("Nyx Assassin", 0.03), ("Riki", 0.03)],
    "Winter Wyvern": [("Silencer", 0.04), ("Nyx Assassin", 0.035), ("Doom", 0.03)],
    "Techies": [("Nyx Assassin", 0.04), ("Zeus", 0.035), ("Bounty Hunter", 0.03)],
    "Ember Spirit": [("Doom", 0.04), ("Nyx Assassin", 0.035), ("Bloodseeker", 0.03), ("Silencer", 0.03)],
    "Slark": [("Bloodseeker", 0.045), ("Bounty Hunter", 0.04), ("Nyx Assassin", 0.035), ("Doom", 0.035), ("Winter Wyvern", 0.03)],
    "Faceless Void": [("Winter Wyvern", 0.05), ("Nyx Assassin", 0.04), ("Silencer", 0.04), ("Outworld Destroyer", 0.035), ("Bane", 0.03), ("Ancient Apparition", 0.035)],
    "Troll Warlord": [("Winter Wyvern", 0.045), ("Axe", 0.045), ("Nyx Assassin", 0.035), ("Bane", 0.035), ("Kunkka", 0.03)],
    "Ursa": [("Axe", 0.045), ("Bane", 0.04), ("Winter Wyvern", 0.04), ("Clockwerk", 0.035), ("Leshrac", 0.03)],
    "Slardar": [("Weaver", 0.03), ("Storm Spirit", 0.03), ("Ember Spirit", 0.03)],
    "Night Stalker": [("Bristleback", 0.035), ("Ursa", 0.03), ("Legion Commander", 0.03)],
    "Enigma": [("Silencer", 0.06), ("Rubick", 0.045), ("Nyx Assassin", 0.045), ("Earthshaker", 0.04), ("Sand King", 0.04), ("Bane", 0.035), ("Vengeful Spirit", 0.035)],
    "Magnus": [("Silencer", 0.05), ("Rubick", 0.045), ("Nyx Assassin", 0.04), ("Vengeful Spirit", 0.035)],
    "Tidehunter": [("Silencer", 0.04), ("Rubick", 0.04), ("Nyx Assassin", 0.03)],
    "Batrider": [("Silencer", 0.04), ("Rubick", 0.035), ("Nyx Assassin", 0.035), ("Vengeful Spirit", 0.03)],
    "Sand King": [("Silencer", 0.035), ("Rubick", 0.035), ("Nyx Assassin", 0.03)],
    "Shadow Fiend": [("Ember Spirit", 0.04), ("Void Spirit", 0.045), ("Storm Spirit", 0.04), ("Clockwerk", 0.04), ("Batrider", 0.035), ("Pudge", 0.035)],
    "Death Prophet": [("Doom", 0.035), ("Nyx Assassin", 0.03), ("Anti-Mage", 0.03)],
    "Outworld Destroyer": [("Anti-Mage", 0.04), ("Nyx Assassin", 0.035), ("Silencer", 0.03)],
    "Invoker": [("Nyx Assassin", 0.045), ("Anti-Mage", 0.04), ("Storm Spirit", 0.04), ("Clockwerk", 0.035), ("Silencer", 0.03)],
    "Tinker": [("Storm Spirit", 0.045), ("Clockwerk", 0.04), ("Nyx Assassin", 0.04), ("Anti-Mage", 0.035), ("Spirit Breaker", 0.035)],
    "Zeus": [("Anti-Mage", 0.045), ("Nyx Assassin", 0.04), ("Clockwerk", 0.035), ("Storm Spirit", 0.035)],
    "Lina": [("Nyx Assassin", 0.035), ("Clockwerk", 0.035), ("Anti-Mage", 0.03)],
    "Puck": [("Silencer", 0.04), ("Doom", 0.035), ("Nyx Assassin", 0.035), ("Bloodseeker", 0.03)],
    "Kunkka": [("Weaver", 0.035), ("Slark", 0.035), ("Anti-Mage", 0.03)],
    "Monkey King": [("Sniper", 0.035), ("Drow Ranger", 0.035), ("Bristleback", 0.04), ("Timbersaw", 0.035)],
    "Viper": [("Anti-Mage", 0.03), ("Medusa", 0.03), ("Spectre", 0.03)],
    "Dragon Knight": [("Timbersaw", 0.03), ("Viper", 0.03), ("Necrophos", 0.03)],
    "Bounty Hunter": [("Slardar", 0.03), ("Bristleback", 0.03)],
    "Spirit Breaker": [("Bristleback", 0.035), ("Timbersaw", 0.03), ("Ursa", 0.03)],
    "Earthshaker": [("Silencer", 0.035), ("Rubick", 0.04), ("Nyx Assassin", 0.03)],
    "Crystal Maiden": [("Spirit Breaker", 0.04), ("Storm Spirit", 0.04), ("Riki", 0.04), ("Nyx Assassin", 0.035), ("Clockwerk", 0.035)],
    "Witch Doctor": [("Spirit Breaker", 0.035), ("Storm Spirit", 0.035), ("Riki", 0.035)],
    "Lion": [("Silencer", 0.03), ("Nyx Assassin", 0.03), ("Anti-Mage", 0.03)],
    "Shadow Shaman": [("Silencer", 0.03), ("Nyx Assassin", 0.03), ("Spirit Breaker", 0.035)],
    "Dazzle": [("Ancient Apparition", 0.045), ("Nyx Assassin", 0.035), ("Doom", 0.03)],
    "Oracle": [("Ancient Apparition", 0.04), ("Nyx Assassin", 0.035), ("Doom", 0.03)],
    "Omniknight": [("Ancient Apparition", 0.04), ("Doom", 0.035), ("Nyx Assassin", 0.035), ("Silencer", 0.03)],
    "Io": [("Clockwerk", 0.04), ("Spirit Breaker", 0.035), ("Nyx Assassin", 0.03)],
    "Rubick": [("Spirit Breaker", 0.035), ("Nyx Assassin", 0.035), ("Storm Spirit", 0.03)],
    "Ancient Apparition": [("Spirit Breaker", 0.04), ("Storm Spirit", 0.04), ("Riki", 0.035)],
    "Disruptor": [("Spirit Breaker", 0.035), ("Storm Spirit", 0.035), ("Riki", 0.035)],
    "Keeper of the Light": [("Spirit Breaker", 0.04), ("Storm Spirit", 0.04), ("Riki", 0.04)],
    "Jakiro": [("Spirit Breaker", 0.035), ("Storm Spirit", 0.035)],
    "Treant Protector": [("Timbersaw", 0.03), ("Bristleback", 0.03)],
    "Dark Seer": [("Doom", 0.03), ("Nyx Assassin", 0.03)],
    "Brewmaster": [("Doom", 0.035), ("Silencer", 0.03), ("Nyx Assassin", 0.03)],
    "Phoenix": [("Doom", 0.03), ("Silencer", 0.03)],
    "Abaddon": [("Ancient Apparition", 0.045), ("Doom", 0.03), ("Nyx Assassin", 0.03)],
    "Alchemist": [("Ancient Apparition", 0.04), ("Nyx Assassin", 0.035), ("Doom", 0.03)],
    "Huskar": [("Ancient Apparition", 0.05), ("Necrophos", 0.04), ("Doom", 0.035)],
    "Arc Warden": [("Earthshaker", 0.03), ("Leshrac", 0.03), ("Tinker", 0.03)],
    "Beastmaster": [("Slark", 0.03), ("Riki", 0.03)],
    "Chen": [("Sand King", 0.035), ("Earthshaker", 0.035), ("Leshrac", 0.03)],
    "Enchantress": [("Sand King", 0.03), ("Leshrac", 0.03)],
    "Venomancer": [("Anti-Mage", 0.03), ("Morphling", 0.03)],
    "Weaver": [("Slardar", 0.03), ("Bounty Hunter", 0.035), ("Bloodseeker", 0.03)],
    "Riki": [("Slardar", 0.035), ("Bounty Hunter", 0.035), ("Bloodseeker", 0.03)],
    "Doom": [("Anti-Mage", 0.03), ("Weaver", 0.03), ("Storm Spirit", 0.03)],
    "Bloodseeker": [("Anti-Mage", 0.03), ("Weaver", 0.03)],
    "Silencer": [("Anti-Mage", 0.03)],
}

synergy_pairs = {
    "Magnus": [("Faceless Void", 0.05), ("Sven", 0.045), ("Troll Warlord", 0.04), ("Luna", 0.04), ("Juggernaut", 0.04)],
    "Enigma": [("Faceless Void", 0.045), ("Tidehunter", 0.04), ("Luna", 0.04), ("Death Prophet", 0.04)],
    "Tidehunter": [("Luna", 0.045), ("Gyrocopter", 0.045), ("Death Prophet", 0.04), ("Faceless Void", 0.04)],
    "Dark Seer": [("Faceless Void", 0.045), ("Tidehunter", 0.04), ("Magnus", 0.04), ("Sven", 0.04)],
    "Faceless Void": [("Magnus", 0.05), ("Dark Seer", 0.045), ("Luna", 0.04), ("Death Prophet", 0.04), ("Warlock", 0.045), ("Ancient Apparition", 0.04)],
    "Warlock": [("Faceless Void", 0.045), ("Luna", 0.04), ("Gyrocopter", 0.04)],
    "Ancient Apparition": [("Faceless Void", 0.04), ("Magnus", 0.04), ("Luna", 0.035)],
    "Dazzle": [("Spectre", 0.04), ("Medusa", 0.04), ("Wraith King", 0.035)],
    "Oracle": [("Medusa", 0.04), ("Spectre", 0.035)],
    "Io": [("Spectre", 0.04), ("Tiny", 0.045), ("Gyrocopter", 0.035)],
    "Tiny": [("Io", 0.045), ("Centaur Warrunner", 0.035)],
    "Vengeful Spirit": [("Luna", 0.035), ("Medusa", 0.035)],
    "Crystal Maiden": [("Juggernaut", 0.035), ("Ursa", 0.035)],
    "Kunkka": [("Faceless Void", 0.04), ("Shadow Fiend", 0.04)],
    "Shadow Demon": [("Luna", 0.04), ("Gyrocopter", 0.035), ("Kunkka", 0.04)],
    "Bane": [("Mirana", 0.04), ("Pudge", 0.035)],
    "Mirana": [("Bane", 0.04), ("Sand King", 0.04), ("Faceless Void", 0.04)],
    "Sand King": [("Mirana", 0.04), ("Leshrac", 0.035), ("Death Prophet", 0.035)],
    "Naga Siren": [("Leshrac", 0.04), ("Death Prophet", 0.035)],
    "Treant Protector": [("Faceless Void", 0.035)],
    "Beastmaster": [("Death Prophet", 0.035)],
    "Chen": [("Death Prophet", 0.035)],
    "Undying": [("Death Prophet", 0.035)],
    "Phoenix": [("Faceless Void", 0.04), ("Magnus", 0.04)],
    "Winter Wyvern": [("Faceless Void", 0.035), ("Magnus", 0.035)],
    "Rubick": [("Sand King", 0.035), ("Enigma", 0.035)],
    "Batrider": [("Luna", 0.035), ("Gyrocopter", 0.035)],
    "Clockwerk": [("Death Prophet", 0.03)],
    "Slardar": [("Sniper", 0.03), ("Drow Ranger", 0.035)],
    "Spirit Breaker": [("Sniper", 0.03)],
    "Omniknight": [("Medusa", 0.04), ("Spectre", 0.035)],
    "Keeper of the Light": [("Medusa", 0.035)],
    "Nyx Assassin": [("Ancient Apparition", 0.03)],
    "Shadow Shaman": [("Leshrac", 0.03)],
    "Leshrac": [("Naga Siren", 0.04), ("Sand King", 0.035)],
}
# fmt: on
