# -*- coding: utf-8 -*-
"""自检脚本：一条命令验证数据、引擎、Web API 与悬浮窗依赖是否都正常。

用途：改完代码 / 刷新完数据后，快速确认「整个助手还能跑」。

用法::

    python tools/selftest.py            # 数据 + 引擎 + Web API
    python tools/selftest.py --overlay  # 额外打开悬浮窗看一眼（需人工点关闭）
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from d2a.data_loader import HeroBook, PlayerHeroStat  # noqa: E402
from d2a.draft import Draft  # noqa: E402
from d2a.engine import Assistant  # noqa: E402
from d2a.webui import ApiState, make_handler  # noqa: E402

OK = "  [OK] "
BAD = "  [!!] "
failures = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print((OK if cond else BAD) + label + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(label)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--overlay", action="store_true", help="额外启动悬浮窗")
    ap.add_argument("--port", type=int, default=8791)
    args = ap.parse_args()

    print("== 数据层 ==")
    book = HeroBook.load()
    check("英雄库加载", len(book.heroes) >= 120, f"{len(book.heroes)} 个英雄")
    problems = book.validate()
    check("数据集自检", not problems, "通过" if not problems else str(problems[:3]))
    edges = sum(len(v) for v in book.matchups.values())
    check("克制矩阵", edges > 300, f"{edges} 条，来源 {book.live_source}")
    check("版本胜率", bool(book.meta), f"{len(book.meta)} 个英雄，来源 {book.meta_source}")
    check("中文名解析", book.resolve("斧王").name == "Axe", "斧王 -> Axe")
    check("缩写解析", book.resolve("am").name == "Anti-Mage", "am -> Anti-Mage")
    check("别名表加载", book.alias_source.startswith("aliases.json"), book.alias_source)
    check("危险别名不出错", book.resolve("ls").name == "Lifestealer", "ls -> Lifestealer（曾错成 Vengeful Spirit）")
    zero = [h for h in book.names() if not book.matchups.get(h)]
    check("无零对位英雄", not zero, "全部 127 个英雄都有对位数据" if not zero else str(zero[:5]))
    check("版本号真实", book.meta_patch not in ("", "current", "unknown"), f"patch={book.meta_patch}")
    with_br = [h for h in book.names() if (book.meta.get(h) or {}).get("brackets")]
    check("分档位数据", len(with_br) > 100, f"{len(with_br)} 个英雄带 1~8 档位样本")
    bw = book.bracket_winrate("Axe", 7)
    check("高分段切片可读", bw is not None and 0.3 < bw < 0.7, f"Axe@Divine={bw:.4f}" if bw else "无")

    print("\n== 引擎 ==")
    a = Assistant.create(book=book)
    a.set_pool_simple({"Axe": (60, 37), "Magnus": (44, 27), "Crystal Maiden": (88, 50),
                       "Lion": (52, 30), "Spirit Breaker": (47, 27), "Sand King": (40, 24)})
    d = Draft()
    d.add("Juggernaut", "enemy", 1)
    d.add("Zeus", "enemy", 2)
    d.add("Crystal Maiden", "ally", 5)
    d.my_lane = 4
    cands = a.engine.recommend(d, top_n=5, pool_only=True)
    check("英雄池推荐", len(cands) == 5, "、".join(f"{c.hero.name}({c.score:.1f})" for c in cands[:3]))
    check("推荐只在英雄池内", all(c.games > 0 for c in cands))
    check("分数有区分度", len({round(c.score, 1) for c in cands}) > 1)
    check("全英雄推荐", len(a.engine.recommend(d, top_n=5, pool_only=False)) == 5)
    exp = a.engine.explain("Axe", d, lane=4)
    check("可解释性", exp is not None and len(exp.reasons) >= 2, "；".join(exp.reasons[:2]) if exp else "无")
    check("阵容缺口识别", a.engine.team_needs(d)["init"] > 0, f"先手缺口 {a.engine.team_needs(d)['init']:.0%}")
    check("加权贡献可对账",
          exp is not None and abs(sum(exp.contribution.values()) - exp.breakdown["weighted_base"] * 100) < 0.01,
          "各因子贡献之和 = 加权总分")

    # 分位置熟练度
    st = PlayerHeroStat("Magnus", 80, 50)
    for _ in range(20):
        st.add_lane_result(3, True)
    for _ in range(20):
        st.add_lane_result(4, False)
    a2 = Assistant.create(book=book)
    a2.engine.set_pool([st])
    s3 = a2.engine.proficiency("Magnus", 3)[0]
    s4 = a2.engine.proficiency("Magnus", 4)[0]
    check("分位置熟练度", s3 - s4 > 0.5, f"3号位 {s3:.2f} vs 4号位 {s4:.2f}")

    # 天梯档位切片
    a3 = Assistant.create(book=book)
    a3.engine.set_bracket(None)
    base_meta = a3.engine.meta_score("Axe")
    a3.engine.set_bracket(7)
    div_meta = a3.engine.meta_score("Axe")
    check("档位切片生效", abs(base_meta - div_meta) > 1e-9 and a3.engine.bracket == 7,
          f"全体 {base_meta:.3f} vs Divine {div_meta:.3f}")

    print("\n== Web API ==")
    from http.server import ThreadingHTTPServer

    state = ApiState(a)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state))
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    time.sleep(0.4)
    base = f"http://127.0.0.1:{args.port}"

    def get(path: str) -> dict:
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            base + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        st = get("/api/state")
        check("GET /api/state", st.get("ok") and st["data"]["heroes"] >= 120)
        check("首页 HTML", "<!DOCTYPE html>" in urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8"))
        rec = get("/api/recommend?top=3&pool_only=1")
        check("GET /api/recommend", rec.get("ok") and len(rec["candidates"]) == 3)
        check("推荐卡片字段完整",
              all(k in rec["candidates"][0] for k in ("hero", "score", "stars", "breakdown", "reasons")))
        add = post("/api/add", {"hero": "美杜莎", "side": "enemy", "lane": "1"})
        check("POST /api/add 中文名", any(e["hero"] == "Medusa" for e in add["draft"]["enemies"]))
        cnt = get("/api/counter?hero=Medusa")
        check("GET /api/counter", cnt.get("ok") and len(cnt["candidates"]) > 0)
        pool = post("/api/pool", {"op": "set", "hero": "斧王", "games": 20, "wins": 12})
        check("POST /api/pool 中文名", any(p["hero"] == "Axe" for p in pool["pool"]))
        rm = post("/api/remove", {"hero": "Medusa"})
        check("POST /api/remove", not any(e["hero"] == "Medusa" for e in rm["draft"]["enemies"]))
        srch = get("/api/search?q=ls&limit=5")
        check("GET /api/search 别名检索", srch.get("ok") and srch["candidates"]
              and srch["candidates"][0]["name"] == "Lifestealer",
              f"ls -> {srch['candidates'][0]['name'] if srch.get('candidates') else '无'}")
        srch2 = get("/api/search?q=" + urllib.parse.quote("剑圣"))
        check("GET /api/search 中文", srch2.get("ok") and srch2["candidates"]
              and srch2["candidates"][0]["name"] == "Juggernaut")
        vc = get("/api/voice")
        check("GET /api/voice 状态", vc.get("ok") and "supported" in vc)
        brk = post("/api/bracket", {"bracket": "7"})
        check("POST /api/bracket", brk.get("ok") and brk.get("bracket") == 7,
              f"切片 -> {brk.get('bracket_label')}")
        post("/api/bracket", {"bracket": ""})
        try:
            get("/api/not-exist")
            check("未知接口返回错误", False)
        except urllib.error.HTTPError as e:
            check("未知接口返回错误", e.code == 404, f"HTTP {e.code}")
    finally:
        httpd.shutdown()
        httpd.server_close()

    print("\n== 语音输入 ==")
    try:
        from d2a.voice import VoiceConfig, VoiceListener, parse_line, voice_supported

        ok_v, why_v = voice_supported()
        check("语音环境", True, why_v)
        ev = parse_line("FLAG 斧王 0.88")
        check("语音协议解析", ev is not None and ev.kind == "flag" and abs(ev.confidence - 0.88) < 1e-6)
        import subprocess

        fake = [
            sys.executable, "-c",
            "import sys;print('READY zh-CN hero',flush=True);print('HEARD 剑圣',flush=True)",
        ]
        got = []
        lst = VoiceListener(VoiceConfig(seconds=1), on_event=got.append, command=fake)
        lst.start()
        if lst._thread:
            lst._thread.join(timeout=10)
        lst.stop()
        check("语音监听管线", any(e.kind == "heard" and e.text == "剑圣" for e in got),
              "假进程 -> READY/HEARD 解析通过")
    except Exception as e:
        check("语音环境", False, str(e))

    print("\n== 悬浮窗依赖 ==")
    try:
        import tkinter as tk

        r = tk.Tk()
        r.withdraw()
        r.destroy()
        check("tkinter 可用", True)
        try:
            import ctypes

            check("Win32 置顶/穿透 API", hasattr(ctypes.windll.user32, "SetWindowLongW"))
        except Exception as e:
            check("Win32 置顶/穿透 API", False, str(e))
    except Exception as e:
        check("tkinter 可用", False, str(e))

    if args.overlay:
        print("\n启动悬浮窗（请手动关闭窗口结束）…")
        a.draft = d
        from d2a.overlay import run_overlay

        run_overlay(a)

    print("\n" + ("=" * 46))
    if failures:
        print(f"自检失败 {len(failures)} 项: " + "; ".join(failures))
        return 1
    print("自检全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
