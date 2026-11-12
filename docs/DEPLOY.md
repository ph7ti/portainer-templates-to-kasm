[Português](DEPLOY.pt-BR.md)

# Deployment guide

Assumes a Kasm Workspaces single-server with Docker (validated on Debian 13,
Docker 29.9.3, fixed network `kasm_default_network`) and a public GitHub
account for the registry repository.

## 0. Prerequisites

- Kasm 1.19.0 (schema 1.1) with Developer API enabled.
- API key with `Sessions View` and `Images View`
  (Settings → Developers → Add API Key).
- Docker socket access on the Kasm agent host.
- The registry is served over **HTTPS**; Kasm rejects `http://` registry URLs.
- The waiting page container must run on the same Docker network as the browser
  workspaces.

## 1. Generate the registry

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python converter/convert_templates.py --output registry --refresh --prune
```

Validate:

```bash
python -m pytest -q
test -f registry/versions.txt
python -c "import json;d=json.load(open('registry/1.1/list.json'));print(d['workspacecount'])"
```

The generated launch URL includes the waiting page:

```
--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80
```

Use `--no-wait-page` only for debugging without the waiter.

## 2. Publish the registry

The canonical public registry is **ph7ti/kasm-registry**:

- Pages: `https://ph7ti.github.io/kasm-registry/`
- Repo: `https://github.com/ph7ti/kasm-registry`

Its `site/` directory is generated from this project and published by
`.github/workflows/deploy-pages.yml`. To refresh it:

```bash
# from this repository, output directly into a clone of kasm-registry/site
python converter/convert_templates.py --output /path/to/kasm-registry/site \
  --refresh --prune
cd /path/to/kasm-registry
git add -A && git commit -m "Refresh registry" && git push
```

If you are creating a new registry repo:

1. Create a public repo (e.g. `kasm-registry`) and put the generated tree in
   `site/` (so `site/versions.txt`, `site/1.1/list.json`, `site/1.1/icons/`).
2. Add the Pages workflow (see the repository) and enable
   **Settings → Pages → GitHub Actions**, or call
   `POST /repos/{owner}/{repo}/pages` with `{"build_type":"workflow"}`.
3. Wait for the **Deploy Kasm registry** job.
4. Validate:
   - `https://<user>.github.io/kasm-registry/versions.txt`
   - `https://<user>.github.io/kasm-registry/1.1/list.json`

The raw GitHub fallback also works for Kasm because it only performs GETs, but
the Pages URL is the recommended one.

## 3. Add the registry in Kasm

1. **Admin → Workspaces → Registry → Add new**.
2. Paste `https://ph7ti.github.io/kasm-registry/` (no `/1.1`).
3. Wait for the Registry page to show 420 workspaces.
4. In this Kasm build the registry imports metadata but does **not** create the
   launchable image entries automatically. Use the **Add** button per item or:

   ```bash
   python tools/publish_images_to_kasm.py --update
   ```

   The tool reads `.secrets.json` (`kasm_url`, `api_key`, `api_key_secret`) or
   `KASM_URL`/`KASM_API_KEY`/`KASM_API_KEY_SECRET`, skips existing images and
   calls `/api/public/create_image` / `update_image`.
5. Confirm the `Teste: …` entries in **Workspaces**.

> The orchestrator API key does not create sessions or registries. Registry
> import and workspace launch are done by an admin in the UI. For automation,
> use the newer API (`/api/session/sessions`) with
> `Users Auth Session`/`User` permissions.

## 4. Deploy the waiting page

```bash
docker build -t kasm-wait:local -f waiter/Dockerfile .
docker run -d --name kasm-wait --restart unless-stopped \
  --network kasm_default_network kasm-wait:local

docker run --rm --network kasm_default_network curlimages/curl:latest \
  -sS http://kasm-wait:8080/healthz
# ok
```

Or with compose, the `waiter` service in `docker-compose.yml` does the same
(`container_name: kasm-wait`, external network `kasm_default_network`).

Notes:

- The container must be resolvable as `kasm-wait` from browser workspaces.
- With ephemeral Kasm networks, the orchestrator would have to attach the
  waiter to each session network; the validated setup uses the fixed network.
- `WAITER_PORT` defaults to `8080`.

## 5. Deploy the orchestrator

Copy to `/opt/kasm-orchestrator`:

```
orchestrator/
registry/backend_manifest.json
requirements.txt
systemd/kasm-orchestrator.service
```

Install and configure:

```bash
sudo python3 -m venv /opt/kasm-orchestrator/venv
sudo /opt/kasm-orchestrator/venv/bin/pip install -r /opt/kasm-orchestrator/requirements.txt
sudo cp .env.example /opt/kasm-orchestrator/.env   # edit it below
```

`/opt/kasm-orchestrator/.env`:

```ini
KASM_URL=https://kasm.example.com
KASM_API_KEY=...
KASM_API_KEY_SECRET=...
KASM_API_MODE=auto
KASM_VERIFY_TLS=false        # internal CA; install the CA in production
DOCKER_NETWORK=kasm_default_network
MANIFEST_PATH=/opt/kasm-orchestrator/registry/backend_manifest.json
STATE_PATH=/var/lib/kasm-orchestrator/state.json
```

Dry run:

```bash
sudo set -a; . /opt/kasm-orchestrator/.env; set +a
sudo -E /opt/kasm-orchestrator/venv/bin/python -m orchestrator.orchestrator --once --dry-run
```

systemd:

```bash
sudo cp systemd/kasm-orchestrator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kasm-orchestrator
sudo journalctl -u kasm-orchestrator -f
```

Docker Compose alternative:

```bash
cp .env.example .env   # edit
docker compose up -d --build
docker compose logs -f orchestrator
```

The unit runs as `root` for `/var/run/docker.sock`. A dedicated `docker` group
user is a more restrictive alternative.

## 6. Kasm group policies

For the test group:

| Setting | Value |
|---|---|
| `keepalive_expiration_action` | `delete` |
| `keepalive_expiration` | `300` |
| `idle_disconnect` | `5` |

Closing the tab lets Kasm delete the session; the orchestrator removes the
backend on the next poll (≤ 5 s).

## 7. Validate

1. Click **Teste: Nextcloud**.
2. The browser first shows the waiting page (“Preparando seu aplicativo…”).
3. The backend appears in Docker within ~2–5 s:

   ```bash
   watch -n2 'sudo docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}" | grep -E "nextcloud|kasm-wait"'
   ```

4. When Nextcloud responds, the waiting page redirects automatically.
5. Close the tab. Kasm deletes the session and the orchestrator removes
   `nextcloud` and its volume within a few seconds.
6. Repeat for Gitea and Uptime Kuma.

Backend DNS check without Kasm:

```bash
sudo docker run --rm --network kasm_default_network curlimages/curl:latest \
  -sS -o /dev/null -w '%{http_code}\n' http://nextcloud:80
```

Wait page check:

```bash
sudo docker run --rm --network kasm_default_network curlimages/curl:latest \
  -sS -o /dev/null -w '%{http_code}\n' \
  'http://kasm-wait:8080/wait?target=http://nextcloud:80'
```

## 8. Pre-pull

The first click pulls the backend image and can take minutes. Pre-pull the test
apps:

```bash
sudo docker pull kasmweb/chrome:1.19.0
sudo docker pull linuxserver/nextcloud:latest
sudo docker pull gitea/gitea:latest
sudo docker pull louislam/uptime-kuma:latest
```

## 9. Rollback

- Stop the orchestrator: `sudo systemctl disable --now kasm-orchestrator`.
- Remove managed backends:
  `sudo docker ps -a --filter label=kasm-teste.managed=true -q | xargs -r sudo docker rm -f`
- Remove the registry in the Kasm UI (Admin → Workspaces → Registry).
- Remove imported workspaces in **Admin → Workspaces**.

## 10. Security

- Never commit `.env`/`.secrets.json`; both are in `.gitignore`.
- The orchestrator only removes containers with `kasm-teste.*` labels and
  refuses to touch an unrelated container with the same name.
- `privileged` and `network: host` templates are skipped by default in the
  converter.
- `--ignore-certificate-errors` is used for HTTPS app ports with self-signed
  certificates; install the app CA if that matters.
- `KASM_VERIFY_TLS=false` is acceptable only for the pilot; install the CA in
  production.
