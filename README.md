# Portainer → Kasm Workspaces

**A third-party Kasm Workspaces registry that turns 420 Portainer app templates
into ephemeral Protected Web Apps.** Each workspace opens an isolated Chrome
browser against a waiting page; a Docker orchestrator starts the application
container on demand and removes it when the last session ends.

[Português](README.pt-BR.md) · [Registry repository](https://github.com/ph7ti/kasm-registry) · [Registry URL](https://ph7ti.github.io/kasm-registry/)

## What this is

- A **Kasm 1.1 workspace registry** with 420 browser workspaces named
  `Teste: …` (Nextcloud, Gitea, Uptime Kuma, AdGuard, MinIO, and many more).
- A **waiting page** (`kasm-wait`) that tells the user the application container
  is being created, polls it every 2 seconds, and redirects when it is ready.
- A **backend orchestrator** that polls the Kasm API and keeps exactly one
  Docker container per application while that application has sessions.
- A **converter** that generates the registry from
  `portainer-templates.json`, plus tests, examples and deployment automation.

## What this is not

- It is **not** a Kasm build with multiple containers per session. Kasm still
  runs one browser container per session; the application runs in a second
  container managed externally.
- It does **not** rebuild application images. Original upstream images are used
  as-is.
- It does **not** provide per-user isolated application state for concurrent
  sessions of the same workspace. One backend is shared by those sessions
  (reference-counted and removed when the last one ends).
- It does **not** convert `type 3` (compose stacks) or `type 2` (Swarm) yet.

## How it works

```
┌──────────┐  click "Teste: Nextcloud"   ┌─────────────────────────────┐
│  User    │ ──────────────────────────▶ │ Kasm Manager / UI           │
└──────────┘                             └──────────────┬──────────────┘
                                                      │ session
                                                      ▼
                                       ┌──────────────────────────────┐
                                       │ Chrome workspace             │
                                       │ kasmweb/chrome:1.19.0        │
                                       │ kiosk → kasm-wait:8080       │
                                       └──────────────┬───────────────┘
                                                      │ waiting page polls
                                                      │ http://nextcloud:80
                                                      ▼
┌──────────────────────────┐  GET /api/public/get_kasms   ┌──────────────────┐
│ Orchestrator (systemd/   │ ◀─────────────────────────── │ Kasm API         │
│ container on Docker host)│                              └──────────────────┘
│                          │  docker create/remove
│ active? start backend    │ ───────────────────────────▶ ┌──────────────────┐
│ gone?   remove backend   │                              │ Backend container│
└──────────────────────────┘                              │ name=nextcloud   │
                                                          │ linuxserver/...  │
                                                          └──────────────────┘
```

The workspace URL is:

```
--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80
```

`kasm-wait` serves a small HTML page. The page polls the target with
`fetch(mode:"no-cors")` every 2 seconds; as soon as the app responds, it
redirects. If the backend takes longer than 60 seconds, it shows an “Open now”
fallback link. The orchestrator creates the backend name after the workspace
slug on the shared Kasm Docker network.

## Live status

Tested against **Kasm Workspaces 1.19.0.842dcf**, single-server, Docker 29.9.3,
Debian 13, fixed network `kasm_default_network`
(`auto_generate_kasm_docker_networks: false`).

| Item | Status |
|---|---|
| Registry | 420 workspaces, schema 1.1, GitHub Pages live |
| Kasm images | 420 `Teste: …` images installed |
| Waiting page | `kasm-wait` on `kasm_default_network`; message/poll/redirect validated |
| Orchestrator | running on the Kasm host (`kasm-orch-test` unit) |
| Tests | **49 passed** (Windows and Debian 13) |
| E2E | Nextcloud, Gitea and Uptime Kuma loaded; waiting page validated (message → poll → redirect); backends removed 2.8–8.5 s after the session |
| Orphans | none after the tests; no leftover volumes |

## Repository layout

| Path | Purpose |
|---|---|
| `converter/` | Portainer → Kasm registry 1.1 converter, schema and port overrides |
| `orchestrator/` | Kasm API client, Docker backend lifecycle and reconciliation loop |
| `waiter/` | `kasm-wait` HTTP service and Dockerfile (waiting page) |
| `registry/` | Generated registry snapshot and `backend_manifest.json` |
| `tests/` | 49 unit/integration tests with mocked Kasm and Docker |
| `examples/` | Three generated `workspace.json` files |
| `docs/` | Architecture, deployment and test report (EN/PT-BR) |
| `systemd/` | Example unit for the orchestrator |

The **public registry** itself lives in a separate repository:
[ph7ti/kasm-registry](https://github.com/ph7ti/kasm-registry). End users add
`https://ph7ti.github.io/kasm-registry/` to Kasm; they do not need to clone or
convert the Portainer sources.

## Quick start

```bash
# 1. Generate the registry from the Portainer templates
python converter/convert_templates.py --output registry --refresh --prune

# 2. Validate
python -m pytest -q

# 3. Serve the waiting page + orchestrator
docker compose up -d --build

# 4. One-off dry run of the orchestrator
docker compose run --rm orchestrator --once --dry-run
```

Kasm setup:

1. Add `https://ph7ti.github.io/kasm-registry/` in
   **Admin → Workspaces → Registry → Add new** (no `/1.1`).
2. Install the workspaces as launchable images:

   ```bash
   python tools/publish_images_to_kasm.py --update
   ```

3. Recommended group policy:
   - `keepalive_expiration_action = delete`
   - `keepalive_expiration = 300`
   - `idle_disconnect = 5`

## Waiting page

The page is served by a tiny Python container (`waiter/`) attached to the Kasm
workspace network. Its URL is generated into each `workspace.json`:

```json
{
  "run_config": {
    "environment": {
      "LAUNCH_URL": "--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80"
    },
    "shm_size": "512m"
  }
}
```

Deploy it on the Docker host:

```bash
docker build -t kasm-wait:local -f waiter/Dockerfile .
docker run -d --name kasm-wait --restart unless-stopped \
  --network kasm_default_network kasm-wait:local
# check
docker run --rm --network kasm_default_network curlimages/curl:latest \
  -sS http://kasm-wait:8080/healthz
```

The container must be on the same Docker network as the browser workspaces.
With ephemeral Kasm networks (`auto_generate_kasm_docker_networks: true`), it
needs to be attached to each session network by the orchestrator; this repository
was validated with the fixed network.

Configuration: `WAITER_PORT` (default `8080`). The page accepts only
`http://host[:port]`/`https://host[:port]` targets and rejects open redirects.

## Mapping Portainer → Kasm

| Portainer | Kasm |
|---|---|
| `title` | `friendly_name = "Teste: <title without (container)>"` |
| `description` | description + ephemeral/browser notice |
| `logo` | downloaded to `workspaces/<slug>/<slug>.<ext>` and `1.1/icons/`; fallback `default-icon.png` |
| `categories` | first 3 |
| `ports` | container port, priority 443/80/8443/3000/…; overrides in `converter/port_overrides.json` |
| `image` | **only** in `backend_manifest.json` and the orchestrator |
| (fixed) | `architecture = ["amd64"]` — the source has no `architectures` field |
| (fixed) | `compatibility = [{version:"1.19.x", image:"kasmweb/chrome:1.19.0", ...}]` |
| (fixed) | `run_config.environment.LAUNCH_URL = "--kiosk [--ignore-certificate-errors] <wait-page>"` |
| (fixed) | `run_config.shm_size = "512m"` |

The launch URL uses HTTPS for ports 443/8443/9443 (with
`--ignore-certificate-errors`, because app containers commonly use self-signed
certificates) and HTTP otherwise. Nextcloud is overridden to port 80/HTTP: the
template only lists 443, but LinuxServer also serves HTTP on 80 and that avoids
the “plain HTTP request sent to HTTPS port” error.

## Orchestrator

Required environment:

| Variable | Default | Description |
|---|---|---|
| `KASM_URL` | — | Kasm base URL |
| `KASM_API_KEY` / `KASM_API_KEY_SECRET` | — | Developer API key |
| `KASM_API_MODE` | `auto` | `legacy`, `v2` or auto-detect |
| `KASM_VERIFY_TLS` | `true` | set `false` for an internal CA |
| `POLL_INTERVAL` | `5` | seconds between reconciliations |
| `RETRY_INTERVAL` | `30` | backoff after a start failure |
| `DOCKER_NETWORK` | `kasm_default_network` | fixed network |
| `DOCKER_DISCOVER_NETWORK` | `true` | inspect the browser container network |
| `MANIFEST_PATH` | `registry/backend_manifest.json` | generated manifest |
| `STATE_PATH` | `/var/lib/kasm-orchestrator/state.json` | observed state |
| `PULL_IMAGES` | `true` | pull missing app images |

Run modes:

```bash
python -m orchestrator.orchestrator --once --dry-run
python -m orchestrator.orchestrator --once
python -m orchestrator.orchestrator          # service loop
```

The orchestrator only removes containers carrying its labels
(`kasm-teste.managed=true`); it refuses to touch an unrelated container with the
same name. API errors keep existing backends instead of deleting them.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
# 49 passed
```

Coverage: converter filters/ports/slugs/schema/idempotency/icons, waiting page
routes and validation, Kasm API legacy/v2 normalization, orchestrator lifecycle,
refcount, ephemeral network discovery, retry backoff, orphan cleanup, dry-run,
name conflicts.

See [docs/TEST_REPORT.md](docs/TEST_REPORT.md) for the full E2E timings.

## Limitations

- **Shared backend per app**: the static `LAUNCH_URL` forces one container per
  app. Concurrent sessions share it. Reference counting removes it only when the
  last session ends.
- **Startup race**: the waiting page solves the “site cannot be reached” first
  impression, but the app itself may still be initializing after the redirect.
  For HTTPS apps with self-signed certificates, the polling fetch may fail; the
  page offers the “Open now” fallback after 60 s.
- **420 of 464 type 1 templates**: 17 privileged, 9 host-network, 16 without a
  TCP port and 2 Windows templates are skipped. Compose stacks/Swarm are out of
  scope.
- **No per-user isolation** for app data; this is a demo/test catalog.
- **Internal CA**: `KASM_VERIFY_TLS=false` is required for the Kasm instance
  used here; prefer installing the CA in production.
- The Kasm API key used by the orchestrator needs `Sessions View` and
  `Images View`; it does not create sessions. E2E was validated by clicking in
  the Kasm UI.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) · [Arquitetura (PT-BR)](docs/ARCHITECTURE.pt-BR.md)
- [Deploy](docs/DEPLOY.md) · [Deploy (PT-BR)](docs/DEPLOY.pt-BR.md)
- [Test report](docs/TEST_REPORT.md) · [Relatório de testes (PT-BR)](docs/TEST_REPORT.pt-BR.md)
- [Discovery notes](docs/discovery-notes.md) (PT-BR, environment-specific)

## Attribution

Templates and logos belong to their original authors
([lissy93/portainer-templates](https://github.com/lissy93/portainer-templates)
and the upstream template maintainers). The registry metadata, waiting page and
orchestrator are MIT licensed.
