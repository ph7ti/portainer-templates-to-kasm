# Portainer → Kasm Workspaces (source fork)

This repository is a **fork of
[lissy93/portainer-templates](https://github.com/lissy93/portainer-templates)**
that keeps the upstream Portainer template catalog and adds the tooling needed
to publish the same applications as **Kasm Workspaces** Protected Web Apps.

It is the **source and operations repository**, not the registry that Kasm
consumes. The public registry is published separately from
[ph7ti/kasm-registry](https://github.com/ph7ti/kasm-registry):

```
Kasm registry URL: https://ph7ti.github.io/kasm-registry/
```

[Português](README.pt-BR.md) · [Public registry](https://github.com/ph7ti/kasm-registry) · [Registry URL](https://ph7ti.github.io/kasm-registry/)

## Why this fork exists

The upstream project is a **Portainer** catalog and website. Its job is to
provide `templates.json`, the source data and the generation scripts for
Portainer users. Kasm-specific artifacts (schema 1.1 registry entries, a Chrome
workspace factory, a waiting page, a Docker orchestrator) do not belong in that
upstream catalog.

The fork exists to:

1. **Preserve the upstream template model and update flow.** We keep
   `templates.json`, `Schema.json`, `sources/` and `lib/` so we can pull
   upstream changes and regenerate deterministically.
2. **Add Kasm-only transformation without changing upstream.** The converter
   reads the fork's `templates.json` and emits registry 1.1 entries; upstream
   stays focused on Portainer.
3. **Keep the generated registry separate from the source.** The public
   `ph7ti/kasm-registry` repository contains only the static registry artifact;
   end users add that URL and never need to clone or convert this fork.
4. **Own the runtime pieces.** The orchestrator and waiting page are operated by
   the Kasm administrator, not by the upstream project.

Do not treat this repository as the Kasm catalog. Use it to regenerate or
operate; use `ph7ti/kasm-registry` to consume.

## Three artifacts

| Artifact | Role | Who uses it |
|---|---|---|
| [lissy93/portainer-templates](https://github.com/lissy93/portainer-templates) | Upstream Portainer templates, sources and site | Portainer users, upstream maintainers |
| **this fork** (`ph7ti/portainer-templates-to-kasm`) | Upstream data + converter, waiter, orchestrator, tests, docs and a generated `registry/` snapshot | Operators and contributors |
| [ph7ti/kasm-registry](https://github.com/ph7ti/kasm-registry) | Public static Kasm registry (Pages) | Kasm administrators/end users |

The fork's `templates.json` is the input; the dedicated registry's
`site/1.1/list.json` is the output Kasm consumes.

## What was added to the fork

| Path | Purpose |
|---|---|
| `converter/` | Portainer `templates.json` → Kasm registry 1.1; schema, icons, port overrides, `backend_manifest.json` |
| `waiter/` | `kasm-wait`: waiting page that says “Preparando seu aplicativo…”, polls every 2 s and redirects |
| `orchestrator/` | Polls the Kasm API, starts/stops the app containers on the Kasm Docker network |
| `registry/` | Generated snapshot for tests/dev; the public copy lives in `ph7ti/kasm-registry` |
| `tools/` | `publish_images_to_kasm.py`, `measure_cleanup.py`, backend smoke test |
| `tests/` | 50 unit/integration tests with mocked Kasm and Docker |
| `docs/` | Architecture, deployment and test report in English and Portuguese |
| `systemd/` | Example orchestrator unit |

## How to use the new resources

### A. As a Kasm administrator/end user (no conversion)

1. Add the public registry in Kasm:
   **Admin → Workspaces → Registry → Add new** →
   `https://ph7ti.github.io/kasm-registry/` (no `/1.1`).
2. Install the image entries for the workspaces you want (the registry import
   creates the metadata; this Kasm build does not auto-create launchable images):

   ```bash
   python tools/publish_images_to_kasm.py --update
   ```

   Run this from a checkout of this fork, or use the **Add** button per item in
   the Kasm registry UI.
3. The operator must be running:
   - `kasm-wait` on the Kasm workspace Docker network;
   - the orchestrator on the Docker host.
4. Click a `Teste: …` workspace. The browser first shows the waiting page; when
   the backend answers, it redirects automatically.

You do **not** need to clone this fork just to use the registry, but you do need
the waiter and orchestrator running.

### B. As an operator (regenerate and deploy)

```bash
git clone git@github.com:ph7ti/portainer-templates-to-kasm.git
cd portainer-templates-to-kasm

python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 1. convert current templates.json -> registry snapshot
python converter/convert_templates.py --output registry --refresh --prune

# 2. run the tests
python -m pytest -q

# 3. deploy the waiting page (same network as Kasm workspaces)
docker build -t kasm-wait:local -f waiter/Dockerfile .
docker run -d --name kasm-wait --restart unless-stopped \
  --network kasm_default_network kasm-wait:local

# 4. deploy the orchestrator
cp .env.example .env   # add KASM_URL, API key/secret
docker compose up -d --build
# or use systemd/kasm-orchestrator.service
```

Publish the generated registry to `ph7ti/kasm-registry`:

```bash
python converter/convert_templates.py --output /path/to/kasm-registry/site \
  --refresh --prune
cd /path/to/kasm-registry
git add -A && git commit -m "Refresh registry" && git push
```

See [docs/DEPLOY.md](docs/DEPLOY.md) for prerequisites, TLS/CA notes, image
installation and Kasm group policies.

### C. As a contributor

- Keep upstream data/model changes separate from Kasm output.
- Regenerate `registry/` and update the dedicated registry when
  `templates.json` changes.
- Run `pytest -q` (50 tests).
- See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design decisions.

## How the runtime works

```
User → Kasm workspace (Chrome kiosk) → kasm-wait page
                                      │ poll target every 2 s
                                      ▼
                         app container (name = workspace slug)
                                      ▲
Kasm API ── sessions ── orchestrator ─┘ create/remove on kasm_default_network
```

The waiting page solves the first-click race: Kasm starts the browser before the
application container exists. The page polls the backend and redirects when it
answers; if the backend takes longer than 60 seconds, it shows an “Open now”
link. The orchestrator handles the backend lifecycle.

### Ports and HTTPS overrides

The converter picks HTTPS for ports 443/8443/9443 (with
`--ignore-certificate-errors` for self-signed certificates) and HTTP otherwise.
Some apps expose a **second, non-standard TLS port**. Duckstation is the concrete
example:

- `http://duckstation:3000` → HTTP 200
- `https://duckstation:3001` → HTTPS 200 (self-signed)
- `https://duckstation:3000` → **ERR_SSL_PROTOCOL_ERROR** (plain HTTP port)

Those exceptions are configured in
[`converter/port_overrides.json`](converter/port_overrides.json) (port) and
[`converter/scheme_overrides.json`](converter/scheme_overrides.json) (scheme).
Duckstation is mapped to `3001` + `https`, so its waiting page redirects to
`https://duckstation:3001` with the certificate flag. To add another app, map
its slug to the TLS port and to `"https"`.

## Fork vs upstream sync

- Upstream files (`templates.json`, `Schema.json`, `sources/`, `lib/`) remain
  the source of truth for Portainer templates.
- The Kasm additions live in new top-level directories and do not patch
  upstream code.
- To refresh: pull/merge upstream, run the converter, commit the regenerated
  snapshot, then publish to `ph7ti/kasm-registry`.
- The generated `registry/` in this fork is a development snapshot; the public
  URL is the dedicated repository.

## Current status

Validated on **Kasm Workspaces 1.19.0.842dcf**, Docker 29.9.3, Debian 13,
`kasm_default_network` (fixed network).

| Item | Status |
|---|---|
| Conversion | 420 workspaces from 464 type-1 templates; 0 schema errors |
| Skipped | 44: 17 privileged, 9 host-network, 16 no TCP port, 2 Windows |
| Registry | 420 workspaces, schema 1.1, public Pages live |
| Kasm images | 420 `Teste: …` entries installed |
| Waiting page | validated: message → polling → automatic redirect |
| Tests | 50 passed on Windows and Debian 13 |
| E2E | Nextcloud, Gitea and Uptime Kuma loaded; backends removed 2.8–8.5 s after closing |
| Orphans | none after tests; no leftover volumes |

## Limitations

- One backend per app (shared by concurrent sessions; reference-counted), since
  `LAUNCH_URL` is static.
- No rebuild of app images. The waiting page is a separate container.
- `type 3` stacks / Swarm are not converted yet.
- Apps in HTTPS ports use `--ignore-certificate-errors` for self-signed
  certificates; the waiting-page probe may not detect them and falls back to the
  “Open now” link.
- The waiter must be on the same Docker network as the workspaces. The validated
  deployment uses the fixed network.
- No per-user app data isolation; this is a demo/test catalog.
- Kasm API keys used by the orchestrator need `Sessions View`/`Images View`; they
  do not create sessions.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) · [Arquitetura](docs/ARCHITECTURE.pt-BR.md)
- [Deploy](docs/DEPLOY.md) · [Deploy PT-BR](docs/DEPLOY.pt-BR.md)
- [Test report](docs/TEST_REPORT.md) · [Relatório PT-BR](docs/TEST_REPORT.pt-BR.md)
- [Waiting page](waiter/README.md)
- Public registry: [ph7ti/kasm-registry](https://github.com/ph7ti/kasm-registry)

## Credit and license

Forked from
[lissy93/portainer-templates](https://github.com/lissy93/portainer-templates);
templates and logos belong to their original authors. The added Kasm
conversion, orchestrator and waiting page are MIT licensed, following the
upstream license.
