# -*- coding: utf-8 -*-
"""push_now.py 的读输入逻辑自检：三种 stdin 类型都不能挂死。

运行::

    python d2a-push-tool/selftest_prompt.py
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
PUSH = HERE / "push_now.py"

# 通过 importlib 直接加载，避免执行它顶层的 token 交互
spec = importlib.util.spec_from_file_location("push_now_mod", PUSH)
mod = importlib.util.module_from_spec(spec)
sys.modules["push_now_mod"] = mod
spec.loader.exec_module(mod)  # 顶层 TOKEN=""，acquire_token 此时未被调用

FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK] " if cond else "  [!!] ") + label + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


print("== 控制台判定 ==")
print(f"  _is_console_stdin() = {mod._is_console_stdin()}")
check("函数可调用", callable(mod._is_console_stdin))

print("\n== 管道 stdin + 有数据（应读到内容）==")
old_stdin = sys.stdin
try:
    sys.stdin = io.StringIO("ghp_test_value_123\n")
    t0 = time.time()
    got = mod._read_secret("  prompt: ", timeout=5)
    dt = time.time() - t0
    check("读到内容", got == "ghp_test_value_123", f"got={got!r}")
    check("未超时等待", dt < 4, f"{dt:.2f}s")
finally:
    sys.stdin = old_stdin

print("\n== 管道 stdin + 空数据（必须快速返回，不能挂死）==")
try:
    sys.stdin = io.StringIO("")
    t0 = time.time()
    got = mod._read_secret("  prompt: ", timeout=5)
    dt = time.time() - t0
    check("返回空串", got == "", f"got={got!r}")
    check("未挂死", dt < 6, f"{dt:.2f}s")
finally:
    sys.stdin = old_stdin

print("\n== 超时语义（无数据且不支持 kbhit 时应尊重 timeout）==")
try:
    sys.stdin = io.StringIO("")
    t0 = time.time()
    mod._read_secret("  prompt: ", timeout=1)
    dt = time.time() - t0
    check("在 timeout 附近返回", dt < 3, f"{dt:.2f}s")
finally:
    sys.stdin = old_stdin

print("\n== 分支完备性 ==")
src = PUSH.read_text(encoding="utf-8")
for branch in ("_is_console_stdin", "_read_via_thread", "msvcrt.kbhit", "select.select"):
    check(f"包含分支 {branch}", branch in src)

print("\n" + "=" * 46)
if FAILS:
    print(f"失败 {len(FAILS)} 项: " + "; ".join(FAILS))
    sys.exit(1)
print("读输入逻辑自检通过 ✅")
