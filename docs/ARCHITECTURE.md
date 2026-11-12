# Architecture and decisions

## Session flow

1. The user clicks **Teste: Nextcloud** in the Kasm dashboard.
2. Kasm creates a workspace session from the registry entry
   (`kasmweb/chrome:1.19.0`). Its `run_config.environment.LAUNCH_URL` points to
   the waiting page:
   `--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80`.
3. Chrome starts on the Kasm workspace network and opens `kasm-wait`.
4. The waiting page polls `http://nextcloud:80` every 2 seconds and, as soon as
   the backend answers, redirects the browser to it. If the backend is still
   down, the user sees a spinner and “your app container is starting”.
5. In parallel, the orchestrator polls `/api/public/get_kasms` (legacy API in
   this build). When it sees the session and matches it to a manifest app, it:
   - discovers the network (API `docker_network`, or `docker inspect` of the
     browser `container_id`);
   - runs the backend with `name=<slug>`, `network=<network>`, labels
     `kasm-teste.*`, `restart_policy=no`.
6. When the user closes the tab or the session expires, Kasm removes the browser
   session. On its next poll the orchestrator sees no session and removes the
   backend (`stop(timeout=5)` + `remove(force=True, v=True)`) and its named
   volumes.

## Why external orchestration

Kasm does **not** create multiple containers per session. A workspace is one
container (the browser). The application needs a second container, so an
external process with access to the Docker socket must manage it. Rebuilding the
browser image with the app embedded was rejected (image rebuilds are out of
scope).

## Waiting page

`waiter/wait_server.py` is a dependency-free Python HTTP server:

- `GET /wait?target=http://app:port` returns the waiting page.
- `GET /healthz` returns `ok`.
- The page validates the target (only `http(s)://host[:port]`), then polls with
  `fetch(target, {mode:"no-cors", cache:"no-store"})` every 2 s. A successful
  opaque response means the backend is up; the page redirects after 450 ms.
- After 30 attempts (60 s) it shows an “Open now / Abrir agora” link.
- It is deployed as `kasm-wait` on `kasm_default_network`, so the browser can
  resolve it by name. With ephemeral Kasm networks the container would need to
  be attached per session; the validated deployment uses the fixed network.

Limitations: HTTPS targets with self-signed certificates may reject the polling
fetch; the fallback link covers that case. The page can only test reachability,
not application readiness.

## Kasm API

The installed build (1.19.0.842dcf) exposes the **legacy Developer API**:

- `POST /api/public/get_kasms` with `{api_key, api_key_secret}` in the body;
- `POST /api/public/get_images` likewise;
- responses `{kasms: [...]}` and `{images: [...]}`;
- `POST /api/public/create_registry`, `get_registries`, `delete_registry`;
- `POST /api/public/create_image`, `update_image`, `get_images`.

The newer documentation describes `GET /api/session/sessions` with HTTP Basic,
but that route returns **404** in this build. `KasmClient` auto-detects the mode
and normalizes both response formats. Force it with `KASM_API_MODE=legacy|v2`.

Session matching order:

1. session `image_id` against the `friendly_name → image_id` map built from
   `get_images` (survives renaming a workspace in Kasm);
2. fallback: normalized `image.friendly_name` (casefold, collapsed spaces).

The image index is optional: without `Images View` the client logs a warning and
uses names only.

## Network

- **Fixed network** (this deployment): `auto_generate_kasm_docker_networks:
  false`, workspaces and `kasm-wait` use `kasm_default_network`, a user-defined
  bridge with embedded DNS. The backend joins it and its container name becomes
  the DNS name used by `LAUNCH_URL`.
- **Ephemeral network**: the orchestrator reads
  `NetworkSettings.Networks` of the browser container and uses that network. It
  removes the backend before Kasm deletes the network; the 5 s poll keeps the
  race window small.
- The waiting page must be reachable on the same network.

## One backend per app

The static `LAUNCH_URL` forces a predictable backend name, so one container is
shared by concurrent sessions of the same app. The orchestrator keeps a set of
`session_id` per slug (reference count): it starts on the first session and
removes when the last one leaves. Concurrent sessions read/write the same
service — acceptable for a demo, not per-user isolation. Per-user isolation
would require a session-aware proxy/router, outside the registry 1.1 scope.

## State and recovery

- Source of truth: Kasm API plus Docker labels `kasm-teste.managed`,
  `kasm-teste.app`, `kasm-teste.image`.
- `state.json` is observability only (active sessions, networks, timestamp).
- On the first successful reconciliation the orchestrator cleans managed
  containers that do not match active sessions (restart orphans).
- Kasm API errors **do not** remove backends; the loop logs and keeps them.
- Backend start failures enter a backoff (`RETRY_INTERVAL`, default 30 s).
- A name conflict with an unmanaged container is refused; the orchestrator never
  removes containers it did not create.

## Port and scheme selection

The Portainer source does not say which port is the web UI. The converter:

1. parses `host:container/proto`, `container/proto` and `container`;
2. considers TCP only;
3. picks by priority: 443, 80, 8443, 8080, 3000, 3001, 8000, 8096, 8123,
   8181, 9000, 9001, 9090, 8081, 8888; otherwise the first TCP port;
4. applies `converter/port_overrides.json` (`minio → 9001`,
   `adguard/adguardhome → 3000`, `nextcloud → 80`);
5. uses `https` for 443/8443/9443 (plus `--ignore-certificate-errors`) and
   `http` otherwise.

The port is only used in the browser URL; the backend does not publish host
ports.

## Volumes

Portainer binds point at host paths (e.g. `/srv/lsio/nextcloud/config`). Instead
of creating host directories, the orchestrator creates named volumes
`kasm-teste-vol-<slug>-<n>` and removes them with the container. System binds
(`/etc/timezone`, `/etc/localtime`, `/var/run/docker.sock`) are dropped during
conversion. Data does not persist across sessions.

## Registry distribution

- `ph7ti/portainer-templates-to-kasm` contains the converter, orchestrator,
  waiter, tests and a generated `registry/` snapshot.
- `ph7ti/kasm-registry` is the **public third-party registry** (only the static
  `site/` artifact). Its README tells users to add
  `https://ph7ti.github.io/kasm-registry/` — no source conversion required.
- The main project's GitHub Pages deployment is optional; the dedicated registry
  repository is the canonical URL.

## Startup race and readiness

The waiting page removes the first “site cannot be reached” impression, but an
app can still be initializing after the redirect (or a TLS app may not answer the
no-cors probe). Mitigations: 2 s polling, 60 s fallback link, pre-pulled images,
and notes in each workspace. A true readiness gate (proxy/landing page with
health checks) is a future phase; rebuilding the browser image is explicitly out
of scope.
