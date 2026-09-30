# -*- coding: utf-8 -*-
"""别名解析与语音录入的单元测试。

运行::

    python -m unittest tests.test_voice -v
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.data_loader import HeroBook, normalize  # noqa: E402
from d2a.voice import VoiceConfig, VoiceEvent, VoiceListener, parse_line  # noqa: E402

BOOK: HeroBook


def setUpModule() -> None:
    global BOOK
    BOOK = HeroBook.load()


# --------------------------------------------------------------------------- 别名解析
class TestAliasResolution(unittest.TestCase):
    def test_ls_is_lifestealer_not_vengeful(self) -> None:
        """回归：曾因「末词短名」索引被后写入的英雄覆盖，ls 错解析成 Vengeful Spirit。"""
        self.assertEqual(BOOK.resolve("ls").name, "Lifestealer")
        self.assertEqual(BOOK.resolve("LS").name, "Lifestealer")

    def test_jugg_and_chinese(self) -> None:
        """用户明确要求：剑圣 与 jugg 都指向 Juggernaut。"""
        self.assertEqual(BOOK.resolve("剑圣").name, "Juggernaut")
        self.assertEqual(BOOK.resolve("jugg").name, "Juggernaut")
        self.assertEqual(BOOK.resolve("juggernaut").name, "Juggernaut")
        self.assertEqual(BOOK.resolve("主宰").name, "Juggernaut")

    def test_common_colloquial_names(self) -> None:
        cases = {
            "wisp": "Io",
            "abba": "Abaddon",
            "dusa": "Medusa",
            "qop": "Queen of Pain",
            "potm": "Mirana",
            "火猫": "Ember Spirit",
            "蓝猫": "Storm Spirit",
            "紫猫": "Void Spirit",
            "土猫": "Earth Spirit",
            "小鱼人": "Slark",
            "敌法": "Anti-Mage",
            "一姐": "Medusa",
            "幽鬼": "Spectre",
            "nec": "Necrophos",
            "shaker": "Earthshaker",
            "furion": "Nature's Prophet",
            "kotl": "Keeper of the Light",
        }
        for raw, expect in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(BOOK.resolve(raw).name, expect)

    def test_every_alias_in_file_resolves_to_its_target(self) -> None:
        """别名表里每一条都必须解析成它自己声明的英雄（防止错配）。"""
        p = ROOT / "data" / "aliases.json"
        self.assertTrue(p.exists(), "应当提供 data/aliases.json")
        aliases = json.loads(p.read_text(encoding="utf-8"))
        checked = 0
        for alias, expect in aliases.items():
            if alias.startswith("_"):
                continue
            checked += 1
            with self.subTest(alias=alias):
                hero = BOOK.try_resolve(alias)
                self.assertIsNotNone(hero, f"别名 {alias} 无法解析")
                self.assertEqual(hero.name, expect, f"别名 {alias} 应指向 {expect}")
        self.assertGreater(checked, 400, "别名表太小，可能是被截断或没读到")

    def test_alias_file_has_no_normalized_duplicates(self) -> None:
        """归一化后重复的别名会让后写入的覆盖前者，属于隐蔽错误。"""
        aliases = json.loads((ROOT / "data" / "aliases.json").read_text(encoding="utf-8"))
        seen: dict = {}
        dups = []
        for alias in aliases:
            if alias.startswith("_"):
                continue
            k = normalize(alias)
            if k in seen:
                dups.append((alias, seen[k]))
            seen[k] = alias
        self.assertEqual(dups, [], f"归一化后重复的别名: {dups}")

    def test_alias_targets_all_exist(self) -> None:
        aliases = json.loads((ROOT / "data" / "aliases.json").read_text(encoding="utf-8"))
        bad = [(a, t) for a, t in aliases.items() if not a.startswith("_") and t not in BOOK.heroes]
        self.assertEqual(bad, [], f"别名指向了不存在的英雄: {bad}")

    def test_candidates_for_autocomplete(self) -> None:
        """UI 补全：单字符也要给候选，且按出场率排序。"""
        cands = BOOK.candidates("s", limit=5)
        self.assertTrue(cands, "单字符应能给出候选")
        self.assertLessEqual(len(cands), 5)
        # 出场率高的应该排在前面
        rates = [BOOK.pickrate(c.name) for c in cands]
        self.assertEqual(rates, sorted(rates, reverse=True), f"候选应按出场率降序: {rates}")

    def test_unknown_still_raises(self) -> None:
        from d2a.data_loader import HeroNotFound

        with self.assertRaises(HeroNotFound):
            BOOK.resolve("这不是英雄zzz")

    def test_empty_input_raises(self) -> None:
        from d2a.data_loader import HeroNotFound

        with self.assertRaises(HeroNotFound):
            BOOK.resolve("   ")


# --------------------------------------------------------------------------- 语音协议
class TestVoiceProtocol(unittest.TestCase):
    def test_parse_ready(self) -> None:
        ev = parse_line("READY zh-CN hero")
        self.assertEqual(ev.kind, "ready")
        self.assertEqual(ev.detail, "zh-CN hero")

    def test_parse_heard_chinese(self) -> None:
        ev = parse_line("HEARD 斧王")
        self.assertEqual(ev.kind, "heard")
        self.assertEqual(ev.text, "斧王")

    def test_parse_flag_with_confidence(self) -> None:
        ev = parse_line("FLAG 剑圣 0.87")
        self.assertEqual(ev.kind, "flag")
        self.assertEqual(ev.text, "剑圣")
        self.assertAlmostEqual(ev.confidence, 0.87)

    def test_parse_reject(self) -> None:
        ev = parse_line("REJECT 嗯 0.21")
        self.assertEqual(ev.kind, "reject")
        self.assertAlmostEqual(ev.confidence, 0.21)

    def test_parse_error_and_stopped(self) -> None:
        self.assertEqual(parse_line("ERROR 无法打开麦克风").detail, "无法打开麦克风")
        self.assertEqual(parse_line("STOPPED microphone").kind, "stopped")

    def test_parse_ignores_junk(self) -> None:
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("   "))
        self.assertIsNone(parse_line("没有任何标签的一行"))

    def test_heard_text_is_not_lowercased_or_stripped_of_chinese(self) -> None:
        """识别结果必须原样保留，不能因为我们解析协议而改动它。"""
        ev = parse_line("HEARD Crystal Maiden")
        self.assertEqual(ev.text, "Crystal Maiden")

    def test_voice_event_defaults(self) -> None:
        ev = VoiceEvent("heard", text="斧王")
        self.assertEqual(ev.confidence, 0.0)
        self.assertEqual(ev.detail, "")


# --------------------------------------------------------------------------- 语音监听器
class TestVoiceListenerWithFakeProcess(unittest.TestCase):
    """用假子进程验证监听器本身，无需麦克风。

    真实识别依赖 Windows 语音组件与麦克风，在 CI/沙箱里不可能端到端跑；
    这里验证的是「起进程 → 读协议 → 派发事件 → 收尾」这条我们自己写的链路。
    """

    def _fake(self, lines) -> list:
        import sys as _sys

        code = "import sys,time;"
        for l in lines:
            code += f"print({l!r}, flush=True);"
        code += "time.sleep(0.05)"
        return [_sys.executable, "-c", code]

    def test_events_are_dispatched_in_order(self) -> None:
        got: list = []
        cmd = self._fake(["READY zh-CN hero", "FLAG 斧王 0.91", "HEARD 剑圣", "STOPPED timeout"])
        lst = VoiceListener(VoiceConfig(seconds=1), on_event=got.append, command=cmd)
        self.assertTrue(lst.start(), "假进程应当能启动")
        if lst._thread:
            lst._thread.join(timeout=10)
        lst.stop()
        kinds = [e.kind for e in got]
        self.assertIn("ready", kinds)
        self.assertIn("flag", kinds)
        self.assertIn("heard", kinds)
        self.assertIn("stopped", kinds)
        # 顺序必须与输出一致
        self.assertLess(kinds.index("ready"), kinds.index("flag"))
        self.assertLess(kinds.index("flag"), kinds.index("heard"))

    def test_heard_text_survives_pipe(self) -> None:
        """中文经子进程管道后不能乱码（曾因编码问题踩过坑）。"""
        got: list = []
        cmd = self._fake(["HEARD 斧王", "HEARD Crystal Maiden"])
        lst = VoiceListener(VoiceConfig(), on_event=got.append, command=cmd)
        lst.start()
        if lst._thread:
            lst._thread.join(timeout=10)
        lst.stop()
        texts = [e.text for e in got if e.kind == "heard"]
        self.assertEqual(texts, ["斧王", "Crystal Maiden"])

    def test_missing_executable_reports_error_not_crash(self) -> None:
        got: list = []
        lst = VoiceListener(VoiceConfig(), on_event=got.append, command=["definitely-not-a-real-exe-xyz"])
        ok = lst.start()
        self.assertFalse(ok, "不存在的可执行文件应当启动失败而不是抛异常")
        self.assertTrue(any(e.kind == "error" for e in got), "应当派发 error 事件")

    def test_build_command_contains_config(self) -> None:
        lst = VoiceListener(VoiceConfig(seconds=9, culture="en-US", mode="dictation", min_confidence=0.7))
        cmd = lst.build_command()
        self.assertIn("-Seconds", cmd)
        self.assertIn("9", cmd)
        self.assertIn("-Culture", cmd)
        self.assertIn("en-US", cmd)
        self.assertIn("dictation", cmd)
        self.assertIn("0.70", cmd)

    def test_stop_is_idempotent(self) -> None:
        lst = VoiceListener(VoiceConfig())
        lst.stop()  # 从未启动
        lst.stop()  # 再停一次
        self.assertFalse(lst.running)

    def test_event_history_is_bounded(self) -> None:
        lst = VoiceListener(VoiceConfig())
        for i in range(400):
            lst._emit(VoiceEvent("heard", text=f"h{i}"))
        self.assertLessEqual(len(lst.events), 200, "事件历史必须有上限，避免长会话内存增长")


if __name__ == "__main__":
    unittest.main(verbosity=2)
