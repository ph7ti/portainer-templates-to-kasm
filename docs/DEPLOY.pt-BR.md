[English](DEPLOY.md)

# Guia de deploy

Assume um Kasm Workspaces single-server com Docker (validado em Debian 13,
Docker 29.9.3, rede fixa `kasm_default_network`) e uma conta pública no GitHub
para o repositório do registry.

## 0. Pré-requisitos

- Kasm 1.19.0 (schema 1.1) com Developer API habilitada.
- API key com `Sessions View` e `Images View`
  (Settings → Developers → Add API Key).
- Acesso ao socket Docker no host dos agentes.
- O registry precisa estar em **HTTPS**; o Kasm recusa URL `http://`.
- O contêiner da página de espera deve estar na mesma rede Docker dos workspaces.

## 1. Gerar o registry

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python converter/convert_templates.py --output registry --refresh --prune
```

Valide:

```bash
python -m pytest -q
test -f registry/versions.txt
python -c "import json;d=json.load(open('registry/1.1/list.json'));print(d['workspacecount'])"
```

A `LAUNCH_URL` gerada já aponta para a página de espera:

```
--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80
```

Use `--no-wait-page` apenas para depurar sem o waiter.

## 2. Publicar o registry

O registry público canônico é **ph7ti/kasm-registry**:

- Pages: `https://ph7ti.github.io/kasm-registry/`
- Repo: `https://github.com/ph7ti/kasm-registry`

O diretório `site/` é publicado pelo workflow
`.github/workflows/deploy-pages.yml`. Para regenerar:

```bash
python converter/convert_templates.py --output /caminho/kasm-registry/site \
  --refresh --prune
cd /caminho/kasm-registry
git add -A && git commit -m "Refresh registry" && git push
```

Para criar um novo repositório de registry:

1. Crie um repo público (ex.: `kasm-registry`) e coloque a árvore gerada em
   `site/` (`site/versions.txt`, `site/1.1/list.json`, `site/1.1/icons/`).
2. Adicione o workflow de Pages e habilite
   **Settings → Pages → GitHub Actions**, ou chame
   `POST /repos/{owner}/{repo}/pages` com `{"build_type":"workflow"}`.
3. Aguarde o job **Deploy Kasm registry**.
4. Valide:
   - `https://<usuario>.github.io/kasm-registry/versions.txt`
   - `https://<usuario>.github.io/kasm-registry/1.1/list.json`

A URL raw do GitHub também funciona porque o Kasm só faz GET, mas Pages é a URL
recomendada.

## 3. Adicionar o registry no Kasm

1. **Admin → Workspaces → Registry → Add new**.
2. Cole `https://ph7ti.github.io/kasm-registry/` (sem `/1.1`).
3. Aguarde o Registry mostrar 420 workspaces.
4. Nesta build o Kasm importa os metadados, mas **não** cria as imagens
   lançáveis automaticamente. Use o botão **Add** de cada item ou:

   ```bash
   python tools/publish_images_to_kasm.py --update
   ```

   O script lê `.secrets.json` (`kasm_url`, `api_key`, `api_key_secret`) ou
   `KASM_URL`/`KASM_API_KEY`/`KASM_API_KEY_SECRET`, ignora imagens existentes e
   chama `/api/public/create_image` / `update_image`.
5. Confirme os itens `Teste: …` em **Workspaces**.

> A API key do orquestrador não cria sessões nem registries. A importação do
> registry e o clique no workspace são feitos pelo admin no UI. Para automação,
> use a API nova (`/api/session/sessions`) com `Users Auth Session`/`User`.

## 4. Implantar a página de espera

```bash
docker build -t kasm-wait:local -f waiter/Dockerfile .
docker run -d --name kasm-wait --restart unless-stopped \
  --network kasm_default_network kasm-wait:local

docker run --rm --network kasm_default_network curlimages/curl:latest \
  -sS http://kasm-wait:8080/healthz
# ok
```

Ou use o serviço `waiter` do `docker-compose.yml`
(`container_name: kasm-wait`, rede externa `kasm_default_network`).

Observações:

- O contêiner precisa ser resolvido como `kasm-wait` pelos workspaces.
- Com redes efêmeras, o orquestrador teria de anexar o waiter a cada rede de
  sessão; o setup validado usa rede fixa.
- `WAITER_PORT` padrão `8080`.

## 5. Implantar o orquestrador

Copie para `/opt/kasm-orchestrator`:

```
orchestrator/
registry/backend_manifest.json
requirements.txt
systemd/kasm-orchestrator.service
```

Instale e configure:

```bash
sudo python3 -m venv /opt/kasm-orchestrator/venv
sudo /opt/kasm-orchestrator/venv/bin/pip install -r /opt/kasm-orchestrator/requirements.txt
sudo cp .env.example /opt/kasm-orchestrator/.env
```

`/opt/kasm-orchestrator/.env`:

```ini
KASM_URL=https://kasm.exemplo.com
KASM_API_KEY=...
KASM_API_KEY_SECRET=...
KASM_API_MODE=auto
KASM_VERIFY_TLS=false        # CA interna; em produção instale a CA
DOCKER_NETWORK=kasm_default_network
MANIFEST_PATH=/opt/kasm-orchestrator/registry/backend_manifest.json
STATE_PATH=/var/lib/kasm-orchestrator/state.json
```

Dry-run:

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

Alternativa com Docker Compose:

```bash
cp .env.example .env   # edite
docker compose up -d --build
docker compose logs -f orchestrator
```

A unit roda como `root` para `/var/run/docker.sock`; um usuário no grupo
`docker` é uma alternativa mais restrita.

## 6. Políticas de grupo do Kasm

No grupo de teste:

| Setting | Valor |
|---|---|
| `keepalive_expiration_action` | `delete` |
| `keepalive_expiration` | `300` |
| `idle_disconnect` | `5` |

Ao fechar a aba, o Kasm apaga a sessão; o orquestrador remove o backend no
próximo poll (≤ 5 s).

## 7. Validar

1. Clique em **Teste: Nextcloud**.
2. O navegador mostra a página de espera (“Preparando seu aplicativo…”).
3. O backend aparece no Docker em ~2–5 s:

   ```bash
   watch -n2 'sudo docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}" | grep -E "nextcloud|kasm-wait"'
   ```

4. Quando o Nextcloud responde, a página redireciona automaticamente.
5. Feche a aba; o orquestrador remove `nextcloud` e o volume em poucos segundos.
6. Repita para Gitea e Uptime Kuma.

Teste de DNS sem Kasm:

```bash
sudo docker run --rm --network kasm_default_network curlimages/curl:latest \
  -sS -o /dev/null -w '%{http_code}\n' http://nextcloud:80
```

Teste da página de espera:

```bash
sudo docker run --rm --network kasm_default_network curlimages/curl:latest \
  -sS -o /dev/null -w '%{http_code}\n' \
  'http://kasm-wait:8080/wait?target=http://nextcloud:80'
```

## 8. Pré-pull

O primeiro clique baixa a imagem do backend e pode demorar. Pré-baixe:

```bash
sudo docker pull kasmweb/chrome:1.19.0
sudo docker pull linuxserver/nextcloud:latest
sudo docker pull gitea/gitea:latest
sudo docker pull louislam/uptime-kuma:latest
```

## 9. Rollback

- Pare o orquestrador: `sudo systemctl disable --now kasm-orchestrator`.
- Remova backends gerenciados:
  `sudo docker ps -a --filter label=kasm-teste.managed=true -q | xargs -r sudo docker rm -f`
- Remova o registry no UI (Admin → Workspaces → Registry).
- Remova workspaces importados em **Admin → Workspaces**.

## 10. Segurança

- Nunca commite `.env`/`.secrets.json`; ambos estão no `.gitignore`.
- O orquestrador só remove contêineres com labels `kasm-teste.*` e recusa
  contêiner homônimo não gerenciado.
- Templates `privileged` e `network: host` são pulados por padrão no conversor.
- `--ignore-certificate-errors` é usado em portas HTTPS com certificado
  autoassinado; instale a CA do app se isso importar.
- `KASM_VERIFY_TLS=false` é aceitável só no piloto; em produção instale a CA.
