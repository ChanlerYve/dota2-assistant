# Agent Deployment Contract — dota2-assistant

> **Audience: an automated local agent (LLM tool-runner), not a human.**
> Every step below is written to be **directly executable**: exact commands, exact
> expected output, explicit pass/fail gates, and a defined action for each failure.
> Do not improvise commands that are not listed here.
>
> Human-facing docs: [`用户操作手册.md`](用户操作手册.md) · [`../README.md`](../README.md)
---

## 0. TL;DR for the agent

```bash
# 1. Pick a deploy mode (see §2). If Docker works:
cp .env.example .env && <write a random token into .env>
docker compose up -d --build
# 2. Gate: this must print "healthy"
docker compose exec -T dota2-assistant python /app/tools/docker_entrypoint.py --healthcheck
# 3. Gate: HTTP 200 on /api/health from the host
curl -fsS http://127.0.0.1:8787/api/health
```

**Success contract**: `/api/health` returns HTTP 200 with JSON `{"ok": true, ...}`.
Anything else = deployment failed; see §8.

---

## 1. Machine-readable inputs

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `D2A_API_TOKEN` | **yes for Docker** | none | Bearer token guarding all mutating endpoints. Generate: `python -c "import secrets;print(secrets.token_hex(24))"` |
| `D2A_PORT` | no | `8787` | Host port. Container-internal port is fixed at 8787. |
| `D2A_BIND` | no | `127.0.0.1` | Set to `0.0.0.0` **only** if a remote host must reach it; requires the token. |
| `D2A_DATA_DIR` | no | `./data-docker` (Docker) / `./data` (native) | Persistent volume holding config + API cache. |

**Decide and record these two values before proceeding**: `PORT`, `TOKEN`.
If the operator did not supply a token, generate one and **report it back** to the operator.

---

## 2. Preflight — decide the deploy mode

Run these checks. Do not skip; the correct branch depends on them.

```bash
# P1: is the Docker CLI present?
docker --version

# P2: is the Docker daemon actually reachable? (CLI presence does NOT imply this)
docker info --format '{{.ServerVersion}}'

# P3: is Python 3.9+ present? (fallback path)
python --version    # or python3 --version
```

Branch selection:

| P1 docker | P2 daemon | P3 python | → Mode |
|---|---|---|---|
| ok | ok | any | **A — Docker Compose** (preferred) |
| ok | **fails** | ok | **B — Native Python** (§6). Docker CLI without a reachable daemon is unusable; do NOT try to start Docker Desktop unless the operator asks. |
| missing | — | ok | **B — Native Python** |
| ok | ok | missing | **A** |
| missing | — | missing | **STOP.** Report: neither Docker nor Python 3.9+ is available. |

**Known failure signature** (seen on locked-down Windows hosts):
`open //./pipe/dockerDesktopLinuxEngine: Access is denied` → daemon not reachable
by this process → take Mode B.

---

## 3. Mode A — Docker Compose

### A1. Ensure required data files are present in the build context

The image bakes `data/`. Two of those files are **git-ignored** (they are refresh
artifacts), so a fresh clone will NOT have them: `data/meta.json`,
`data/matchups_live.json`. Verify:

```bash
python - <<'PY'
import json, pathlib
need = ["heroes.json", "aliases.json", "matchups.json", "synergies.json"]
opt  = ["meta.json", "matchups_live.json"]
d = pathlib.Path("data")
for n in need + opt:
    p = d / n
    tag = "REQUIRED" if n in need else "optional"
    print(f"{'OK ' if p.exists() else 'MISS'} {n:22s} {tag}")
PY
```

- All 4 **REQUIRED** present → continue to A2.
- Any REQUIRED missing → **STOP**, report `data/ is incomplete (missing <names>)`.
- Optional missing → continue, but if outbound HTTPS to `api.opendota.com` works,
  run `python tools/refresh_data.py --meta` (≈1 min) to generate `meta.json`.
  The container still runs without it; ranking quality is just lower.

### A2. Create `.env` with a token

```bash
cp .env.example .env
python - <<'PY'
import secrets, pathlib
p = pathlib.Path(".env")
tok = secrets.token_hex(24)
txt = p.read_text(encoding="utf-8")
txt = txt.replace("D2A_API_TOKEN=\n", f"D2A_API_TOKEN={tok}\n")
if "D2A_API_TOKEN=" not in txt:
    txt += f"\nD2A_API_TOKEN={tok}\n"
p.write_text(txt, encoding="utf-8")
print("TOKEN=" + tok)   # report this value to the operator
PY
```

Gate: `.env` contains a non-empty `D2A_API_TOKEN`. If empty, `docker compose`
will refuse to start (the compose file uses `${D2A_API_TOKEN:?...}` intentionally).

### A3. Build and start

```bash
docker compose up -d --build
```

Gate: exit code 0. First build pulls `python:3.11-slim` and needs outbound
network to the registry; if the registry is unreachable, see §8 F4.

### A4. Wait for health, then verify

```bash
# Poll up to 90s
python - <<'PY'
import json, time, urllib.request, os, sys
port = os.environ.get("D2A_PORT", "8787")
url = f"http://127.0.0.1:{port}/api/health"
deadline = time.time() + 90
last = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            body = json.loads(r.read().decode())
        if r.status == 200 and body.get("ok"):
            print("HEALTHY", {k: body[k] for k in ("heroes", "matchup_edges", "patch")})
            sys.exit(0)
        last = f"HTTP {r.status} {body}"
    except Exception as e:
        last = f"{type(e).__name__}: {e}"
    time.sleep(3)
print("UNHEALTHY after 90s:", last); sys.exit(1)
PY
```

Expected: `HEALTHY {'heroes': 127, 'matchup_edges': <n>, 'patch': '<version>'}`.

**Verification gates (all must pass):**

```bash
# G1 container running
docker compose ps --format '{{.Name}} {{.State}} {{.Health}}'
#    expect: running / healthy

# G2 in-container selfcheck (exit 0 = pass; --check/--selfcheck are aliases)
docker compose exec -T dota2-assistant python tools/docker_entrypoint.py --check
#    expect: exit 0 and a line "[entrypoint] 自检通过 ✅"

# G3 auth actually enforced (must be 401)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/api/state
#    expect: 401

# G4 auth actually works (must be 200)
TOKEN=$(grep '^D2A_API_TOKEN=' .env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/state \
  | python -c "import json,sys; d=json.load(sys.stdin); print('ok=',d['ok'],'heroes=',d['data']['heroes'])"
#    expect: ok= True heroes= 127
```

If G3 returns 200 → the token was not applied; **treat as a security failure**, see §8 F5.

---

## 4. Persistent volume semantics (read before touching volumes)

`docker-compose.yml` mounts `./data-docker` → `/app/data`. **The mount hides the
image's baked-in `data/`.** The entrypoint therefore seeds the volume from
`/opt/d2a-baseline/data` on first start, copying **only files that are missing**,
never overwriting.

Consequences the agent must respect:

1. A **fresh empty volume works** — seeding runs automatically. Verify by looking
   for `已从基线播种 N 个数据文件` in `docker compose logs`.
2. **Never `docker compose down -v`** or delete `./data-docker` unless explicitly
   asked to wipe state: it destroys the user's hero pool, weights and bracket.
3. To reset only the generated data but keep the user's pool: delete
   `data-docker/meta.json` and `data-docker/matchups_live.json`, then restart —
   seeding will restore the image's baseline copies.
4. The user's hero pool lives in `data-docker/config.json` as plain JSON; it is
   written on every successful mutating request and again on graceful shutdown.

---

## 5. Operating the deployment

```bash
# Logs (the entrypoint prints selfcheck results here)
docker compose logs -f --tail=50 dota2-assistant

# Restart (keeps volume)
docker compose restart

# Stop (keeps volume + container)
docker compose stop

# Remove container (keeps volume)
docker compose down

# ⚠ DESTRUCTIVE: also removes the volume, i.e. the user's hero pool
# docker compose down -v && rm -rf data-docker
```

### Refresh market data inside the container (needs outbound HTTPS)

```bash
docker compose exec -T dota2-assistant python tools/refresh_data.py --meta
docker compose exec -T dota2-assistant python tools/rebuild_meta.py       # offline rebuild from cache
docker compose exec -T dota2-assistant python tools/rebuild_matchups.py  # offline rebuild from cache
```

### Sync the user's real match results (needs the account's public match data)

```bash
docker compose exec -T dota2-assistant \
  python tools/sync_results.py --account-id <STEAM32_ID> --limit 20 --probe   # dry run first
docker compose exec -T dota2-assistant \
  python tools/sync_results.py --account-id <STEAM32_ID> --limit 20
```

---

## 6. Mode B — Native Python (no Docker)

The app has **zero third-party dependencies**, so "deployment" is just running it.

```bash
python -m d2a --web --host 127.0.0.1 --port 8787 --no-browser
```

For an env-driven, container-like start (same variables as Mode A):

```bash
D2A_HOST=127.0.0.1 D2A_PORT=8787 D2A_API_TOKEN=$TOKEN \
D2A_CONFIG=./data/config.json python tools/docker_entrypoint.py
```

Verify with the same gates G1–G4 from §A4, substituting plain `curl` for
`docker compose exec` ones.

Optional hardening on Linux — a systemd unit:

```ini
# /etc/systemd/system/dota2-assistant.service
[Unit]
Description=Dota 2 assistant web panel
After=network-online.target

[Service]
WorkingDirectory=/opt/dota2-assistant
Environment=D2A_HOST=127.0.0.1
Environment=D2A_PORT=8787
Environment=D2A_API_TOKEN=REPLACE_ME
Environment=D2A_CONFIG=/opt/dota2-assistant/data/config.json
ExecStart=/usr/bin/python3 tools/docker_entrypoint.py
Restart=unless-stopped
User=d2a

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now dota2-assistant
```

---

## 7. Environment variable reference (complete)

| Variable | Default | Effect |
|---|---|---|
| `D2A_HOST` | `127.0.0.1` | Bind address. Container sets `0.0.0.0`. |
| `D2A_PORT` | `8787` | Bind port. |
| `D2A_API_TOKEN` | *(empty)* | If set, all endpoints except `/api/health` require `Authorization: Bearer <token>` or `?token=<token>`. **Empty = no auth.** |
| `D2A_CONFIG` | *(none)* | Path to `config.json`; also the persistence target for pool/weights/bracket. |
| `D2A_DATA_DIR` | `data/` | Where data files are read from and seeded into. |
| `D2A_BASELINE_DIR` | `/opt/d2a-baseline/data` | Image-internal seed source. Leave alone. |
| `D2A_OPEN_BROWSER` | *(off)* | `1` opens a browser (pointless in a container). |
| `OPENDOTA_API_KEY` | *(none)* | Optional. Raises OpenDota rate limits. |
| `STEAM_API_KEY` | *(none)* | Optional. Only for the Valve API path. |

---

## 8. Failure playbook

| ID | Symptom | Cause | Action |
|---|---|---|---|
| **F1** | `docker info` → `Access is denied` / `pipe ... not found` | Daemon not reachable by this process | Use Mode B (§6). Do not try to install/start Docker. |
| **F2** | Container restarts in a loop; logs show `自检未通过` / `缺少 ... heroes.json` | Volume mounted but seeding failed or baseline absent | Check logs for `已从基线播种`. If absent, verify the image was built from this repo (`docker compose build --no-cache`). |
| **F3** | `/api/health` → HTTP 503 `{"ok":false,...}` | `HeroBook.validate()` reported dataset problems | Run `docker compose exec -T dota2-assistant python -c "from d2a.data_loader import HeroBook;print(HeroBook.load().validate())"` and report the list. |
| **F4** | Build fails: `failed to resolve python:3.11-slim` / TLS timeout | No outbound access to the container registry | (a) Retry once; (b) if a mirror is configured, use it; (c) else deploy Mode B. Report which. |
| **F5** | Gate G3 returned **200** instead of 401 | `D2A_API_TOKEN` empty/not passed | Verify `docker compose config \| grep D2A_API_TOKEN` is non-empty, then `docker compose up -d --force-recreate`. Re-run G3. **Do not leave it running without auth if the port is not loopback-only.** |
| **F6** | Port already in use: `bind: address already in use` | Another process holds `PORT` | Pick a free port and set `D2A_PORT`; re-run A3. |
| **F7** | `docker compose up` → `请先在 .env 里设置 D2A_API_TOKEN` | `.env` missing/empty | Re-run A2. |
| **F8** | Health OK but recommendations look empty | User's hero pool is empty (fresh install) | Expected. Populate via `--steam <id>`, the UI, or `tools/sync_results.py`. |
| **F9** | Logs show `未设置 D2A_API_TOKEN` warning | Token not set while bound to non-loopback | Set the token or bind to loopback only. |

When a step fails and none of F1–F9 match, **stop and report to the operator**:
the exact command, its full stderr, and the output of `docker compose ps` and
`docker compose logs --tail=100`. Do not iterate blindly.

---

## 9. Teardown

```bash
docker compose down            # keep the user's data
# docker compose down -v       # DESTRUCTIVE — also deletes the hero pool. Only on explicit request.
```

Report to the operator: `PORT`, the token location (`./.env`), the data path
(`./data-docker`), and whether teardown was destructive.

---

## 10. Hard constraints (do not violate)

1. **Do not bind to `0.0.0.0` without `D2A_API_TOKEN`.** The mutating endpoints
   (`POST /api/pool`, `/api/weights`, `/api/add`, …) have no other protection.
2. **Do not delete `data-docker/` or run `down -v`** unless explicitly instructed;
   it holds the user's hero pool, which is the single most valuable piece of state.
3. **This service makes no attempt to read the game.** Do not add screen capture,
   memory reading, or process injection to "auto-detect the draft" — see
   `../dota2-assistant-review.md` §2 for why that is both technically blocked and
   against Valve's stated policy. Voice/typed input is the only supported path.
4. **No third-party Python packages are required.** Do not `pip install` anything;
   the app is stdlib-only by design (this is what makes the 40 MB slim image work).
5. **`GET /api/health` must stay unauthenticated.** Docker's HEALTHCHECK depends
   on it; adding auth there will make every container report `unhealthy`.
