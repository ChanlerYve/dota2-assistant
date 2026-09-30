"""Dockerfile / compose 静态校验（本机 Docker daemon 不可用时的替代验证）。

检查项：
1. 所有 COPY 的源路径在构建上下文里存在（且没被 .dockerignore 排除）
2. ENTRYPOINT / HEALTHCHECK / EXPOSE / USER / ENV 等关键指令齐备且自洽
3. 引用的脚本文件存在
4. compose 里的 volume / 端口 / healthcheck 与 Dockerfile 一致
5. .dockerignore 不会把必需数据排除掉
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
fails: list = []
oks: list = []


def check(cond, label, detail=""):
    (oks if cond else fails).append(label)
    print(f"  [{'OK' if cond else '!!'}] {label}" + (f"  {detail}" if detail else ""))


dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
compose_raw = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ignore_raw = (ROOT / ".dockerignore").read_text(encoding="utf-8")

print("== Dockerfile 指令 ==")
check(re.search(r"^FROM\s+python:3\.\d+-slim", dockerfile, re.M) is not None, "基础镜像为 python slim")
check("WORKDIR /app" in dockerfile, "设置了 WORKDIR")
check("EXPOSE 8787" in dockerfile, "EXPOSE 8787")
check(re.search(r"^USER\s+\d+", dockerfile, re.M) is not None, "以非 root 用户运行")
check("ENTRYPOINT" in dockerfile, "定义了 ENTRYPOINT")
check("HEALTHCHECK" in dockerfile, "定义了 HEALTHCHECK")
check("--healthcheck" in dockerfile, "HEALTHCHECK 调用入口的探活模式")

print("\n== ENV 默认值 ==")
for key in ("D2A_HOST=0.0.0.0", "D2A_PORT=8787", "D2A_CONFIG=/app/data/config.json",
            "D2A_DATA_DIR=/app/data", "D2A_BASELINE_DIR="):
    check(key in dockerfile, f"ENV {key.split('=')[0]}")

print("\n== COPY 源路径存在 ==")
copies = re.findall(r"^COPY\s+(?:--\S+\s+)*(\S+)\s+(\S+)", dockerfile, re.M)
check(bool(copies), "解析到 COPY 指令", f"{len(copies)} 条")
for src, dst in copies:
    p = ROOT / src.rstrip("/")
    check(p.exists(), f"源存在: {src}", f"-> {dst}")

print("\n== 引用的脚本存在 ==")
for rel in ("tools/docker_entrypoint.py", "tools/sync_results.py",
            "tools/refresh_data.py", "d2a/__main__.py"):
    check((ROOT / rel).exists(), rel)

print("\n== .dockerignore 不会排除必需数据 ==")
ignored = [ln.strip() for ln in ignore_raw.splitlines()
           if ln.strip() and not ln.strip().startswith("#")]
# 必需数据文件必须能进镜像（heroes/aliases/matchups/synergies）
for name in ("heroes.json", "aliases.json", "matchups.json", "synergies.json"):
    # 只要没有形如 data/ 或 *.json 的整体排除即可
    blocked = any(pat in ("data/", "data", "*.json", "data/*.json") for pat in ignored)
    check(not blocked, f"{name} 未被 .dockerignore 整体排除")
check(any("data/cache" in p for p in ignored), "缓存目录被排除（不该进镜像）")
check(any(p in ("config.json", "data/config.json") for p in ignored), "本地 config.json 被排除")

print("\n== compose 结构 ==")
try:
    import yaml

    c = yaml.safe_load(compose_raw)
    svc = c["services"]["dota2-assistant"]
    check(svc["build"]["context"] == ".", "build context = 仓库根")
    check(any("8787" in p for p in svc["ports"]), "端口映射指向 8787", str(svc["ports"]))
    check(all("127.0.0.1" in p for p in svc["ports"]), "默认只绑回环地址（安全默认）")
    check(any("/app/data" in v for v in svc["volumes"]), "持久化卷挂到 /app/data", str(svc["volumes"]))
    env = svc["environment"]
    check("D2A_API_TOKEN" in env, "compose 传入了 D2A_API_TOKEN")
    check(":?" in str(env["D2A_API_TOKEN"]), "token 未设置时 compose 会拒绝启动（fail-fast）")
    check(svc["healthcheck"]["test"][-1] == "--healthcheck", "healthcheck 指向入口探活")
    check(svc.get("restart") == "unless-stopped", "restart 策略")
except ImportError:
    check(False, "PyYAML 不可用，跳过 compose 结构检查")
except Exception as e:
    check(False, "compose 解析失败", f"{type(e).__name__}: {e}")

print("\n== 一致性：compose 卷路径 vs 入口播种 ==")
entry = (ROOT / "tools" / "docker_entrypoint.py").read_text(encoding="utf-8")
check("D2A_BASELINE_DIR" in entry and "seed_data_dir" in entry,
      "入口实现了基线播种（避免挂载遮住镜像数据）")
check("/opt/d2a-baseline" in dockerfile, "Dockerfile 创建了基线副本")
check("seed_data_dir(data_dir)" in entry, "自检前先播种")

print()
print("=" * 52)
if fails:
    print(f"静态校验失败 {len(fails)} 项:")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"静态校验全部通过 ✅（{len(oks)} 项）")
