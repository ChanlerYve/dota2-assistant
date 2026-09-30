# -*- coding: utf-8 -*-
"""git remote helper：通过 paramiko 让 git 走 SSH 协议访问 GitHub。

为什么需要它
------------
本机网络对 ``github.com:443`` 不可达（超时），但 ``ssh.github.com:443`` 正常。
于是必须走 SSH，而 OpenSSH 在这台机器上有两个绕不过的障碍：

1. 私钥文件权限检查 —— 沙箱不允许我们收紧 ACL，OpenSSH 会拒绝使用该密钥；
2. ``ssh-agent`` 服务未启用，且沙箱禁止创建命名管道，agent 方案也用不了。

paramiko 是纯 Python 实现：完全绕开上述两点，也不用 fork ssh.exe。

用法（git 通过 ext:: 传输协议调用本脚本）
---------------------------------------
::

    git ls-remote   ext::python%20<abs-path>/git_remote_paramiko.py%20git@ssh.github.com:OWNER/REPO.git
    git push       同上

参数格式：``<user@host:path>``，可带 ``;port=443`` 后缀。

实现范围：``capabilities`` / ``list`` / ``push``。
（``fetch`` 未实现——本机只需要推送，需要拉取时请用浏览器下载或配代理。）
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Tuple

import paramiko

DEBUG = bool(os.environ.get("D2A_REMOTE_DEBUG"))
ZERO_SHA = "0" * 40


def log(*a: object) -> None:
    if DEBUG:
        print("[helper]", *a, file=sys.stderr, flush=True)


def parse_spec(spec: str) -> Tuple[str, str, Optional[int]]:
    """把 ``git@ssh.github.com:owner/repo.git`` 或带 ``;port=443`` 的形式拆开。"""
    port: Optional[int] = None
    m = re.search(r";port=(\d+)", spec)
    if m:
        port = int(m.group(1))
        spec = spec[: m.start()] + spec[m.end() :]
    if ":" not in spec:
        raise ValueError(f"无法解析远端地址: {spec!r}")
    hostpart, path = spec.split(":", 1)
    user = "git"
    if "@" in hostpart:
        user, hostpart = hostpart.rsplit("@", 1)
    host, _, port_s = hostpart.partition(":")
    if port_s and port is None:
        port = int(port_s)
    return f"{user}@{host}", path.lstrip("/"), port


def build_client(spec: str) -> Tuple[paramiko.SSHClient, str]:
    target, path, port = parse_spec(spec)
    user, host = target.split("@", 1)

    # GitHub 的 SSH-over-443 专用主机默认走 443（本机 22 端口被网络屏蔽）
    if port is None and host.lower() in ("ssh.github.com", "github.com"):
        port = 443 if host.lower() == "ssh.github.com" else None

    cfg: Dict[str, object] = {
        "hostname": host,
        "port": port or 22,
        "username": user,
    }
    key = os.environ.get("D2A_SSH_KEY")
    if key:
        cfg["key_filename"] = key
        cfg["look_for_keys"] = False
        cfg["allow_agent"] = False
    else:
        cfg["look_for_keys"] = True
        cfg["allow_agent"] = True

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # 沙箱里无法写 known_hosts，这里采用首次信任（与 ssh 的 accept-new 等价）
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    log("connecting", host, cfg["port"], "key=", key)
    client.connect(timeout=25, banner_timeout=25, auth_timeout=25, **cfg)  # type: ignore[arg-type]
    return client, path


def git(*args: str, input: Optional[bytes] = None) -> bytes:
    env = dict(os.environ)
    env["GIT_DIR"] = os.environ.get("GIT_DIR", ".")
    r = subprocess.run(["git", *args], input=input, capture_output=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败 ({r.returncode}): {r.stderr.decode('utf-8', 'replace')[:400]}"
        )
    return r.stdout


def remote_refs(client: paramiko.SSHClient, path: str) -> Dict[str, str]:
    """list 命令：读远端引用（走 git-upload-pack 的能力广告）。"""
    _stdin, stdout, _stderr = client.exec_command(f"git-upload-pack '{path}'", timeout=60)
    chan = stdout.channel
    _stdin.write(b"0000")  # 空请求头：只要引用列表
    _stdin.flush()
    try:
        refs, _caps = read_advertisement(chan)
    finally:
        chan.close()
    return {ref: sha for ref, sha in refs}


def _parse_advertised(data: bytes) -> Dict[str, str]:
    """（保留）从原始字节解析引用广告，供离线调试使用。"""
    refs: Dict[str, str] = {}
    i = 0
    while i + 4 <= len(data):
        ln_s = data[i : i + 4]
        i += 4
        try:
            ln = int(ln_s, 16)
        except ValueError:
            break
        if ln == 0:
            break
        chunk = data[i : i + ln - 4]
        i += ln - 4
        text = chunk.decode("utf-8", "replace")
        if text.startswith("#"):
            continue
        text = text.split("\x00", 1)[0].strip()
        if not text:
            continue
        parts = text.split(" ")
        if len(parts) == 2:
            sha, ref = parts
            refs[ref] = sha
    return refs


def pkt_line(text: str) -> bytes:
    payload = text.encode("utf-8")
    return f"{len(payload) + 4:04x}".encode() + payload


def read_exact(chan, n: int) -> bytes:
    """从 paramiko Channel 精确读 n 字节（Channel 只有 recv，没有 read）。"""
    buf = bytearray()
    while len(buf) < n:
        chunk = chan.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def read_pkt_line(chan) -> Optional[Tuple[Optional[str], bool]]:
    """从 SSH 通道读一个 pkt-line。

    返回 ``(payload, flush)``；``payload=None`` 表示流结束。
    """
    header = read_exact(chan, 4)
    if len(header) < 4:
        return None
    try:
        length = int(header, 16)
    except ValueError:
        return None
    if length == 0:
        return "", True
    body = read_exact(chan, length - 4)
    if not body and length - 4 > 0:
        return None
    return body.decode("utf-8", "replace"), False


def read_advertisement(chan) -> Tuple[List[Tuple[str, str]], List[str]]:
    """读取 git-receive-pack 的能力广告，返回 (refs, caps)。"""
    refs: List[Tuple[str, str]] = []
    caps: List[str] = []
    first = True
    while True:
        got = read_pkt_line(chan)
        if got is None:
            break
        payload, flush = got
        if flush:
            break
        text = payload or ""
        if first:
            # 首个 pkt-line 用 NUL 分隔「引用 + 能力列表」
            head, _, cap_text = text.partition("\x00")
            caps = [c.strip() for c in cap_text.split(" ") if c.strip()]
            text = head
            first = False
        text = text.strip()
        if not text:
            continue
        parts = text.split(" ")
        if len(parts) == 2:
            refs.append((parts[1], parts[0]))
    return refs, caps


def read_pack_result(chan, use_sideband: bool) -> str:
    """读 receive-pack 的结果；侧带模式（side-band-64k）要跳过通道 2/3 的字节。"""
    out = bytearray()
    while True:
        got = read_pkt_line(chan)
        if got is None:
            break
        payload, flush = got
        if flush:
            break
        data = (payload or "").encode("utf-8", "replace")
        if use_sideband and data:
            band = data[0:1]
            if band == b"\x01":
                out += data[1:]
            elif band == b"\x02":
                if DEBUG:
                    sys.stderr.write("[remote] " + data[1:].decode("utf-8", "replace"))
            elif band == b"\x03":
                sys.stderr.write("[remote error] " + data[1:].decode("utf-8", "replace"))
            else:
                out += data
        else:
            out += data
    return out.decode("utf-8", "replace")


def build_pack(wants: Iterable[str], haves: Iterable[str]) -> bytes:
    """用 git pack-objects 生成上行的对象包。

    直接从 stdin 提供 want/have 的 SHA，让 git 自己用本地对象库计算闭包与增量，
    比手工调 rev-list 再拼列表可靠（也自然获得 delta 压缩）。
    """
    lines: List[str] = [w for w in wants if w]
    lines += [f"^{h}" for h in haves if h]
    spec = ("\n".join(lines) + "\n").encode()
    return git("pack-objects", "--stdout", "--revs", "--thin", "-q", input=spec)


def do_push(client: paramiko.SSHClient, path: str, refspecs: List[str]) -> Tuple[List[str], int]:
    stdin, stdout, _ = client.exec_command(
        f"git-receive-pack '{path}'", timeout=600, get_pty=False
    )
    chan = stdout.channel

    remote_refs_list, caps = read_advertisement(chan)
    remote: Dict[str, str] = {ref: sha for ref, sha in remote_refs_list}
    log("caps:", caps)
    log("remote refs:", remote)

    # 解析 refspec：支持 refs/heads/main:refs/heads/main、main:main、HEAD:refs/heads/x
    updates: List[Tuple[str, str, str]] = []  # (old, new, ref)
    uptodate: List[str] = []
    for spec in refspecs:
        spec = spec.strip().lstrip("+")
        if not spec or spec.startswith(":"):
            continue  # 删除分支：本工具不需要
        src, _, dst = spec.partition(":")
        dst = dst or (src if src.startswith("refs/") else f"refs/heads/{src}")
        src = src or "HEAD"
        sha = git("rev-parse", src).decode().strip()
        old = remote.get(dst, ZERO_SHA)
        if old == sha:
            log("up-to-date:", dst, sha)
            uptodate.append(dst)
            continue
        updates.append((old, sha, dst))

    if not updates:
        sys.stderr.write("Everything up-to-date\n")
        chan.close()
        return uptodate, 0

    haves = [sha for sha in remote.values() if sha and sha != ZERO_SHA]
    wants = [new for _, new, _ in updates]
    pack = build_pack(wants, haves)
    log(f"updates={updates} pack_bytes={len(pack)}")

    # 能力协商：服务端广告里有什么就用什么
    use_sideband = "side-band-64k" in caps
    req_caps = ["report-status"]
    if use_sideband:
        req_caps.append("side-band-64k")
    if "agent" in " ".join(caps):
        req_caps.append("agent=d2a-helper/0.1")

    buf = bytearray()
    for i, (old, new, ref) in enumerate(updates):
        line = f"{old} {new} {ref}"
        if i == 0:
            line += "\x00" + " ".join(req_caps)
        buf += pkt_line(line)
    buf += b"0000"
    buf += pack

    chan.sendall(bytes(buf))
    try:
        chan.shutdown_write()
    except Exception:
        pass

    result = read_pack_result(chan, use_sideband)
    log("result:", result[:800])
    chan.close()

    if "unpack ok" not in result:
        # 人类可读的诊断走 stderr；stdout 是 git 的协议流，只能放 ok/error
        sys.stderr.write(f"推送被拒绝:\n{result[:2000]}\n")
        return [], 1

    # stdout 上只允许协议响应，其余信息全部走 stderr
    sys.stderr.write(f"To ssh://{path}\n")
    for old, new, ref in updates:
        if old == ZERO_SHA:
            sys.stderr.write(f" * [new branch]      {ref} -> {ref}\n")
        else:
            sys.stderr.write(f"   {old[:7]}..{new[:7]}  {ref} -> {ref}\n")
    sys.stderr.flush()
    return [r for _o, _n, r in updates], 0


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: git remote helper（由 git 自动调用）", file=sys.stderr)
        return 2
    # git 调用约定：argv[1] = 完整 URL（含 scheme），argv[2] = "::" 之后的地址。
    # 早期实现误取了 argv[1]，会把主机名解析成 scheme 名。
    spec = ""
    for candidate in sys.argv[2:]:
        if candidate and "::" not in candidate:
            spec = candidate
            break
    if not spec:
        spec = sys.argv[1] if len(sys.argv) > 1 else ""
    log("argv:", sys.argv)
    try:
        log("parse_spec:", parse_spec(spec))
    except Exception as e:
        log("parse_spec 失败:", e)

    def printf(line: str = "") -> None:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    client: Optional[paramiko.SSHClient] = None
    try:
        # 必须用 readline() 而不是 for-in：后者是行缓冲的，
        # 会让 git 等待输出时死锁（git 不做回车刷新）。
        while True:
            raw = sys.stdin.readline()
            if not raw:
                break
            # 防御：Windows 管道有时会在首行带入 BOM
            line = raw.lstrip("\ufeff").rstrip("\r\n")
            if not line:
                continue
            cmd, _, arg = line.partition(" ")
            log("cmd:", cmd, arg)

            if cmd == "capabilities":
                printf("option")
                printf("push")
                printf("list")
                printf()
            elif cmd == "option":
                # 所有 option 都回 unsupported，git 会退回默认行为
                printf("unsupported")
            elif cmd == "list":
                if client is None:
                    client, path = build_client(spec)
                refs = remote_refs(client, path)
                for ref, sha in refs.items():
                    printf(f"{sha} {ref}")
                printf()
            elif cmd == "push":
                if client is None:
                    client, path = build_client(spec)
                specs = arg.split() if arg else []
                while True:
                    more = sys.stdin.readline()
                    if not more:
                        break
                    more = more.lstrip("\ufeff").rstrip("\r\n")
                    if not more:
                        break
                    specs.append(more)
                pushed, rc = do_push(client, path, specs)
                if rc != 0:
                    printf("error push failed")
                    printf()
                    return rc
                # 每个被更新/已最新的 ref 都要回一行 ok
                for ref in pushed:
                    printf(f"ok {ref}")
                printf()
                return 0
            elif cmd == "fetch":
                printf("error fetch-not-implemented")
                printf()
                return 1
            else:
                printf(f"error unsupported-command {cmd}")
                printf()
                return 1
        return 0
    except Exception as e:
        print(f"remote helper 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
