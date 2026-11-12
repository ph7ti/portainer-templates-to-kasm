[Português](TEST_REPORT.pt-BR.md)

# Test report

Date: 2026-11-12
Kasm: `https://kasm.somosagility.com.br` (1.19.0.842dcf, single-server, Debian 13,
Docker 29.9.3, network `kasm_default_network`).

## Summary

| Test | Where | Result |
|---|---|---|
| Full conversion (884 templates) | Windows 11 / Python 3.13 | **420 workspaces**, 464 skipped, **0 schema errors** |
| Icons | idem | 406 unique downloaded; 14 fallback `default-icon.png` |
| `pytest` local | Windows / Python 3.13 | **49 passed** |
| `pytest` on the Kasm host | Debian 13 / Python 3.13 | **49 passed** |
| Real backend + DNS | host 10.1.0.108 | `nginx:alpine` → `http://kasm-orch-test/` → **200** |
| Orchestrator `--once` | host / real API | `legacy` mode, 423 images, 420/420 matched |
| Public registry | GitHub Pages (`ph7ti/kasm-registry`) | deploy success; `versions.txt`/`list.json` 200 |
| Registry import | Kasm API | 420 workspaces, schema 1.1 |
| Image installation | Kasm API | 420 `Teste: …` images |
| E2E Nextcloud | real Kasm | loaded; backend removed on next poll |
| E2E Gitea | real Kasm | loaded; backend removed ~2.8 s after session |
| E2E Uptime Kuma | real Kasm | loaded; backend removed ~8.5 s after session |
| Wait page | unit/HTTP + real click | validated: page appeared, polled, redirected to Nextcloud |

## 1. Converter

```bash
python converter/convert_templates.py --output registry --refresh --prune
```

```
templates: 884 | converted: 420 | workspaces: 420
skips: host-network=9, no-tcp-port=16, privileged=17,
       type-2-not-container=16, type-3-not-container=404, windows-platform=2
icon fallbacks: 9
```

- `registry/versions.txt` = `1.1`; `workspacecount = 420`.
- 0 jsonschema errors.
- Icon fallbacks: dradis, dozzle, foldingathome, projectsend, sickgear,
  thelounge, trilium, wizarr, xbackbone (404/403 or `.ico`).

Examples:

| App | Waiting-page launch URL | Backend image |
|---|---|---|
| Nextcloud | `--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80` | `linuxserver/nextcloud:latest` |
| Gitea | `--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fgitea%3A3000` | `gitea/gitea:latest` |
| Uptime Kuma | `--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fuptime-kuma%3A3001` | `louislam/uptime-kuma:latest` |
| Code Server | `--kiosk --ignore-certificate-errors http://kasm-wait:8080/wait?target=https%3A%2F%2Fcode-server%3A8443` | `linuxserver/code-server:latest` |

## 2. Unit tests (49)

- **Converter**: filters, ports/overrides, slug, categories, schema,
  `LAUNCH_URL`/waiting page, HTTPS scheme/flag, registry files, idempotency,
  volumes, icon fallback.
- **Wait page**: template formatting, `/healthz`, `/wait`, invalid targets
  (XSS/open redirect rejection), polling script markers.
- **Kasm API**: legacy/v2 detection and normalization, auth modes, error
  handling, image index.
- **Orchestrator**: start/stop/refcount, unrelated sessions, API errors, network
  discovery, retry backoff, unmanaged name conflict, orphan cleanup, volumes,
  dry-run, image-id matching.

```
.........................................  [100%]
49 passed in 3.88s        # Windows
49 passed in 4.45s        # Debian 13 host
```

## 3. Docker/network smoke test

`tools/test_backend.py` on the host:

```
backend status: running
backend networks: ['kasm_default_network']
curl exit: 0 status: 200
OK: http://kasm-orch-test/ resolved and answered
cleanup done
```

## 4. Real Kasm API

```
KASM_API_MODE auto → legacy (/api/public/get_kasms)
Kasm image index: 423 image(s)
matched 420/420 registry workspaces to Kasm image ids
reconcile: sessions=0, active_apps=0, started=0, removed=0, orphans=0
```

The instance uses an internal CA; `KASM_VERIFY_TLS=false` is required for the
pilot. The API key has `Sessions View`/`Images View` but not
`Users Auth Session`, so sessions were created manually in the UI.

## 5. Public registry

- New repository: https://github.com/ph7ti/kasm-registry
- Pages: https://ph7ti.github.io/kasm-registry/
- `versions.txt` 200; `1.1/list.json` 200 (`workspacecount=420`);
  `1.1/icons/nextcloud.png` 200.
- `list_url` points to the Pages URL; Nextcloud launch URL includes the waiter.
- Old raw registry entry in Kasm was deleted to avoid duplicates.

## 6. E2E

### Nextcloud

- First attempt: backend started on 80/443, but direct `http://nextcloud:443`
  returned **400 “plain HTTP request sent to HTTPS port”**.
- Fix: Nextcloud override to 80/HTTP; HTTPS ports use
  `https` + `--ignore-certificate-errors`.
- Retest: **Nextcloud loaded**.
- Backend started 15:00:07; removed 15:02:43 on the first poll after the
  session disappeared (~11 s from the last poll with a session).
- No leftover containers or volumes.

### Gitea

```
session seen at +29.7s
container seen at +34.1s
session gone at +80.7s
container gone at +83.5s
volumes gone at +83.5s
```

Backend created ~4.4 s after the session; removed ~2.8 s after it disappeared.

### Uptime Kuma

```
session seen at +16.7s
container seen at +18.2s
session gone at +59.4s
container gone at +67.9s
volumes gone at +67.9s
```

Backend created ~1.5 s after the session; removed ~8.5 s after it disappeared
(includes `stop(timeout=5)`).

## 7. Waiting page

- `kasm-wait` built and running on `kasm_default_network`.
- `/healthz` → 200; `/wait?target=http://nextcloud:80` → 200 HTML (4.9 KB).
- Invalid target (`javascript:`) → 400.
- Real click: the waiting page appeared (“Preparando seu aplicativo…”), the
  backend was created, and the browser redirected automatically to Nextcloud.
- After closing the tab, the orchestrator removed `nextcloud`; only an unrelated
  VS Code session remained. `kasm-wait` stayed up (expected).
- HTTPS targets with self-signed certificates may not answer the no-cors probe;
  the page offers the “Open now” fallback after 60 s.

## 8. Known limitations

- One shared backend per app, no per-user isolation.
- 420/464 type-1 templates; 44 skipped (privileged, host-network, no TCP port,
  Windows); stacks/Swarm out of scope.
- 30-minute soak was not run; no orphans after the three E2E sessions.
- Wait page is not a readiness gate; the app may still initialize after redirect.
