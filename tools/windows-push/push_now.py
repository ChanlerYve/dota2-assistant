# -*- coding: utf-8 -*-
"""一键推送脚本（本机环境专用）。

本机 github.com:443 不可达，OpenSSH 又被沙箱限制，因此使用
d2a-push-tool/ 里的 paramiko remote helper 走 ssh.github.com:443。

用法::

    set D2A_TOKEN=ghp_xxx        # 你的新 fine-grained token（仅本会话）
    python push_now.py [branch]

它会自动：生成临时密钥 → 用 API 注册到账号 → push → 立刻撤销密钥。
token 只在环境变量中，不落盘。
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

def find_workspace() -> pathlib.Path:
    """稳健地定位工作区根目录。

    不要依赖相对路径解析：本机 python.exe 启动器在某些 shell 下会继承调用者的
    CWD，导致 ``Path("d2a-push-tool").resolve()`` 解析成嵌套路径。
    这里优先用环境变量，其次从脚本自身位置向上找到含 ``dota2-assistant`` 的目录。
    """
    env = os.environ.get("D2A_WORKSPACE")
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env).resolve()
    here = pathlib.Path(__file__).resolve().parent
    for cand in [here, *here.parents]:
        if (cand / "dota2-assistant" / ".git").is_dir():
            return cand
    return here.parent


WS = find_workspace()
TOOL = WS / "d2a-push-tool"
REPO = WS / "dota2-assistant"
OWNER = "ChanlerYve"
NAME = "dota2-assistant"
def _pick_branch(argv: list[str]) -> str:
    """从参数里取分支名，跳过 --token / --xxx 这类选项。"""
    skip_next = False
    for a in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if a == "--token":
            skip_next = True
            continue
        if a.startswith("-"):
            continue
        return a
    return "main"


BRANCH = _pick_branch(sys.argv)
TOKEN = ""  # 由 acquire_token() 解析（环境变量 / --token / 交互输入）

if os.environ.get("D2A_DEBUG"):
    print(f"[diag] cwd={os.getcwd()}")
    print(f"[diag] __file__={__file__}")
    print(f"[diag] WS={WS}  TOOL={TOOL}  REPO={REPO}")

if not (REPO / ".git").is_dir():
    print(f"定位仓库失败：{REPO} 不是 git 仓库")
    print("可设置 D2A_WORKSPACE 环境变量指向工作区根目录")
    sys.exit(2)

TOKEN_PAGE = "https://github.com/settings/tokens/new?description=dota2-assistant&scopes="


def notify_token_needed(reason: str) -> None:
    """明确告诉用户：现在需要 token，以及怎么给。"""
    line = "=" * 66
    print(line)
    print("  ⚠  需要 GitHub Token 才能继续推送")
    print(line)
    print(f"  {reason}")
    print()
    print(f"  仓库    : https://github.com/{OWNER}/{NAME}")
    print(f"  新建页面: https://github.com/settings/tokens")
    print()
    print("  请创建 fine-grained token，权限只要两项：")
    print("    · Contents        : Read and write   （推送代码）")
    print("    · Git SSH keys    : Read and write   （临时密钥注册/撤销）")
    print(f"    仅授权仓库: {OWNER}/{NAME}")
    print()
    print("  三种给 token 的方式（任选其一）：")
    print("    1) 直接在本窗口输入     —— 隐藏回显，不写 shell 历史")
    print('    2) PowerShell: $env:D2A_TOKEN="ghp_xxx"')
    print("    3) 命令行参数: python push_now.py main --token ghp_xxx  (会进历史，不推荐)")
    print(line)
    print()


def _is_console_stdin() -> bool:
    """stdin 是否连着真正的控制台（决定能否用 msvcrt 读键盘）。"""
    if os.name != "nt":
        return False
    try:
        import ctypes

        h = ctypes.windll.kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        if h in (0, -1):
            return False
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode)))
    except Exception:
        return False


def _read_via_thread(timeout: float) -> str:
    """兜底：守护线程里阻塞读一行，主线程到点就返回空串（绝不挂死）。"""
    import threading

    result: dict[str, str] = {}
    done = threading.Event()

    def reader() -> None:
        try:
            result["line"] = sys.stdin.readline() or ""
        except Exception:
            result["line"] = ""
        finally:
            done.set()

    threading.Thread(target=reader, daemon=True).start()
    return result.get("line", "").strip() if done.wait(timeout) else ""


def _read_secret(prompt: str, timeout: float = 120.0) -> str:
    """读一行密钥：不回显、不挂死、兼容「控制台」与「管道」两种 stdin。

    为什么不用 getpass：本机 ``stdin.isatty()`` 在无人值守场景下也返回 True，
    getpass 会永久等待；而 msvcrt 的键盘读取在管道 stdin 下读不到任何字符。
    所以按 stdin 类型分三条路径：

    * Windows 真实控制台 → ``msvcrt.getwch()`` 逐字符读，天然无回显；
    * 有 select 的平台   → select 带超时后再 readline；
    * 其它（Windows 管道）→ 守护线程读 + 超时丢弃。
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        if _is_console_stdin():
            import msvcrt
            import time as _time

            buf: list[str] = []
            deadline = _time.time() + timeout
            while _time.time() < deadline:
                if not msvcrt.kbhit():
                    _time.sleep(0.04)
                    continue
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    break
                if ch == "\003":  # Ctrl+C
                    raise KeyboardInterrupt
                if ch == "\b":
                    if buf:
                        buf.pop()
                    continue
                buf.append(ch)
            return "".join(buf).strip()

        try:
            import select

            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                return ""
            return (sys.stdin.readline() or "").strip()
        except (ImportError, OSError, ValueError):
            # Windows 上 select 只接受 socket，管道会抛错 → 走线程兜底
            return _read_via_thread(timeout)
    except KeyboardInterrupt:
        print("\n  已取消。")
        sys.exit(130)
    finally:
        sys.stdout.write("\n")
        sys.stdout.flush()


def sanitize_token(raw: str) -> str:
    """清理 token：去掉 BOM / 零宽字符 / 空白。

    这是必需的，不是洁癖：Windows 上 PowerShell 的管道有时会在输入最前面加
    UTF-8 BOM（``\\ufeff``），而 HTTP 头只能用 latin-1 编码，会直接抛
    ``UnicodeEncodeError``。之前在别的调试脚本里已经踩过同一个坑，
    所以统一在入口做一次清洗。
    """
    if not raw:
        return ""
    # 去掉所有空白（含 BOM/零宽/全角空格），再剔除非 ASCII 字符
    cleaned = "".join(c for c in raw if not c.isspace())
    return "".join(c for c in cleaned if c.isascii())


def acquire_token() -> str:
    """按「环境变量 → 命令行参数 → 交互输入」的顺序拿 token。"""
    tok = sanitize_token(os.environ.get("D2A_TOKEN", ""))
    if tok:
        return tok

    for i, a in enumerate(sys.argv):
        if a == "--token" and i + 1 < len(sys.argv):
            return sanitize_token(sys.argv[i + 1])
        if a.startswith("--token="):
            return sanitize_token(a.split("=", 1)[1])

    notify_token_needed("未检测到 D2A_TOKEN 环境变量，也没有 --token 参数。")

    # 提示用户：自动打开 token 新建页（URL 里没有密钥，安全）
    # 必须在后台线程里做——某些环境下 webbrowser.open 会阻塞直到浏览器退出。
    try:
        import threading
        import webbrowser

        threading.Thread(
            target=lambda: webbrowser.open("https://github.com/settings/tokens"),
            daemon=True,
        ).start()
    except Exception:
        pass

    if not sys.stdin.isatty() and not os.environ.get("D2A_FORCE_PROMPT"):
        print("  当前不是交互式终端，无法读取输入。")
        print("  请在真实终端里运行，或用方式 2/3 提供 token。")
        sys.exit(2)

    tok = _read_secret("  请粘贴 token（输入不回显，120 秒超时）: ")
    tok = sanitize_token(tok)

    if not tok:
        print("  未收到输入（超时或取消），已退出。")
        sys.exit(2)
    if tok in ("test", "xxx", "xxxxx", "none"):
        print("  这看起来不是有效的 token，已取消。")
        sys.exit(2)
    if not tok.isascii():
        print("  输入包含非 ASCII 字符（可能是复制时带入了不可见字符），已取消。")
        print(f"  长度 {len(tok)}，首个异常字符位置 {next(i for i, c in enumerate(tok) if not c.isascii())}")
        sys.exit(2)
    print()
    return tok


TOKEN = ""  # 由 main() 在需要时通过 acquire_token() 填充（导入本模块不应触发交互）


def api(method: str, path: str, payload: dict | None = None):
    if not TOKEN:
        raise RuntimeError("调用 api() 前必须先 acquire_token()")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "d2a-push",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8")
            return r.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            raw = json.loads(raw)
        except Exception:
            pass
        if e.code == 401:
            print("  token 被 GitHub 拒绝（401）：可能已过期、已撤销，或复制时缺字符。")
        elif e.code == 403:
            print("  权限不足（403）：fine-grained token 需要 Contents 与 Git SSH keys 的读写权限。")
        return e.code, raw
    except UnicodeEncodeError as e:
        # 非 ASCII 混进 HTTP 头时会走到这里；给出可操作的提示而不是堆栈
        print(f"  token 含非法字符（HTTP 头只允许 ASCII）: {e}")
        print("  请重新复制 token，注意不要带上换行、空格或 BOM。")
        return 0, {"message": "invalid characters in token"}


def generate_key() -> pathlib.Path:
    """用 cryptography 生成 ed25519 密钥对（本机 ssh-keygen 写 .pub 会失败）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    # 注意：不要用 id_ed25519 这个名字——本机曾对该文件做过 ACL 加固，
    # 残留的受限 ACL 会让同名文件重建失败（WinError 5 / FileNotFoundError）。
    priv = TOOL / "push_key"
    pub = TOOL / "push_key.pub"
    for p in (priv, pub):
        if p.exists():
            p.chmod(0o600)
            p.unlink()

    key = ed25519.Ed25519PrivateKey.generate()
    priv.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub.write_text(
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode()
        + " d2a-temp-push\n",
        encoding="utf-8",
        newline="\n",
    )
    priv.chmod(0o600)
    return priv


def resolve_token_interactive(first: str) -> str:
    """校验 token；失效时**重新提示**而不是直接退出。

    典型场景：上一次用过的 token 已经被撤销（这正是我们一直建议做的事），
    此时应该让用户换一个新的，而不是甩一个 401 让他自己猜。
    """
    tok = first
    for attempt in (1, 2, 3):
        global TOKEN
        TOKEN = tok
        status, me = api("GET", "/user")
        if status == 200:
            print(f"账号: {me.get('login')}")
            return tok
        if status not in (401, 403):
            print(f"token 校验失败 HTTP {status}: {me}")
            sys.exit(2)

        print()
        print(f"  这个 token 用不了（HTTP {status}）。")
        if os.environ.get("D2A_TOKEN") and attempt == 1:
            print("  注意：环境变量 D2A_TOKEN 里的值优先于手动输入，")
            print("        如果你刚撤销了它，请先清除该变量：")
            print("        Remove-Item Env:D2A_TOKEN")
        if attempt == 3:
            print("  连续 3 次失败，已退出。")
            sys.exit(2)

        notify_token_needed(f"需要换一个有效的 token（第 {attempt + 1} 次尝试）。")
        if not sys.stdin.isatty() and not os.environ.get("D2A_FORCE_PROMPT"):
            print("  非交互终端，无法继续询问。请用 D2A_TOKEN 提供有效 token。")
            sys.exit(2)
        tok = sanitize_token(_read_secret("  请粘贴新的 token（输入不回显）: "))
        if not tok:
            print("  未收到输入，已退出。")
            sys.exit(2)
    return tok


def main() -> int:
    key_id = None
    priv = None
    try:
        resolve_token_interactive(acquire_token())

        priv = generate_key()
        pub_text = (TOOL / "push_key.pub").read_text(encoding="utf-8").strip()
        status, added = api("POST", "/user/keys", {"title": "d2a-temp-push", "key": pub_text, "read_only": False})
        if status != 201:
            print(f"注册密钥失败 HTTP {status}: {added}")
            print("提示: token 需要「Git SSH keys: Read and write」权限")
            return 1
        key_id = added.get("id")
        print(f"临时密钥已注册 id={key_id}")

        env = dict(os.environ)
        env["PATH"] = f"{TOOL}{os.pathsep}{env.get('PATH', '')}"
        env["D2A_SSH_KEY"] = str(priv)
        r = subprocess.run(
            ["git", "push", "-u", "origin", BRANCH],
            cwd=str(REPO),
            env=env,
        )
        print(f"git push 返回码: {r.returncode}")
        return r.returncode
    finally:
        if key_id is not None:
            st, _ = api("DELETE", f"/user/keys/{key_id}")
            print(f"临时密钥已撤销 (HTTP {st})")
        if priv is not None and priv.exists():
            priv.chmod(0o600)
            try:
                priv.unlink()
                (TOOL / "push_key.pub").unlink(missing_ok=True)
                print("本地临时私钥已删除")
            except OSError as e:
                print(f"! 本地私钥删除失败，请手动删除 {priv}: {e}")


if __name__ == "__main__":
    sys.exit(main())
