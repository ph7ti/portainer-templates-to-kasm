# Deploy passo a passo

Este roteiro assume o host do Kasm com Docker (aqui: Debian 13, single-server,
rede `kasm_default_network`) e um fork público
`ph7ti/portainer-templates-to-kasm`.

## 0. Pré-requisitos

- Kasm 1.19.0 com Developer API habilitada.
- API key de um usuário administrador com `Sessions View` e `Images View`
  (Settings → Developers → Add API Key).
- Acesso ao socket Docker no host dos agentes.
- O registry precisa estar em **HTTPS** (o Kasm recusa `http://`).

## 1. Gerar o registry

No repositório:

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

## 2. Publicar no GitHub Pages

O workflow `.github/workflows/deploy-registry.yml` publica o diretório
`registry/` como site estático:

1. Faça commit/push do `registry/` e do workflow.
2. O Pages deste fork já está habilitado (`build_type: workflow`) e o job
   **Deploy Kasm registry** já concluiu com sucesso. Em um fork novo, habilite
   **Settings → Pages → Build and deployment → GitHub Actions** ou use a API
   `POST /repos/{owner}/{repo}/pages` com `{"build_type":"workflow"}`.
3. Aguarde o job **Deploy Kasm registry**.
4. Valide:
   - `https://ph7ti.github.io/portainer-templates-to-kasm/versions.txt`
   - `https://ph7ti.github.io/portainer-templates-to-kasm/1.1/list.json`

Alternativa sem Pages (o Kasm só faz GET nos arquivos):

```
https://raw.githubusercontent.com/ph7ti/portainer-templates-to-kasm/main/registry/
```

## 3. Adicionar o registry no Kasm

1. **Admin → Workspaces → Registry → Add new**.
2. Cole a URL **sem barra de versão** (o Kasm descobre o schema 1.1):
   - Pages: `https://ph7ti.github.io/portainer-templates-to-kasm/`
   - Raw: a URL raw acima.
3. Salve e aguarde o Kasm importar as 420 entradas (`Registry` deve mostrar o
   contador).
4. Instale as imagens lançáveis. Nesta build o registry importa os metadados,
   mas não cria as entradas de **Workspaces**. Use o botão **Add** de cada item
   ou o script em lote:

```bash
python tools/publish_images_to_kasm.py --limit 10   # piloto
python tools/publish_images_to_kasm.py              # todas
```

O script lê `.secrets.json` (`kasm_url`, `api_key`, `api_key_secret`) ou as
variáveis `KASM_URL`/`KASM_API_KEY`/`KASM_API_KEY_SECRET`, ignora itens já
existentes e usa `/api/public/create_image`.
5. Em **Workspaces**, filtre pelo registry e confirme os itens `Teste: …`.

> A API key usada pelo orquestrador **não** cria sessões nem registries. A
> importação do registry e o clique no workspace são feitos pelo usuário admin
> no UI. Para automação futura, use a API nova (`/api/session/sessions`) com
> permissões `Users Auth Session`/`User`.

## 4. Preparar o host do orquestrador

Copie para `/opt/kasm-orchestrator`:

```
orchestrator/
registry/backend_manifest.json
requirements.txt
systemd/kasm-orchestrator.service
```

Crie o venv e instale:

```bash
sudo python3 -m venv /opt/kasm-orchestrator/venv
sudo /opt/kasm-orchestrator/venv/bin/pip install -r /opt/kasm-orchestrator/requirements.txt
```

Crie `/opt/kasm-orchestrator/.env` (modo 600) a partir de `.env.example`:

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

Teste sem alterar Docker:

```bash
sudo set -a; . /opt/kasm-orchestrator/.env; set +a
sudo -E /opt/kasm-orchestrator/venv/bin/python -m orchestrator.orchestrator --once --dry-run
```

Saída esperada: modo `legacy`, `matched 0/420` (enquanto não há registry
importado), `sessions: 0`.

## 5. systemd

```bash
sudo cp systemd/kasm-orchestrator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kasm-orchestrator
sudo journalctl -u kasm-orchestrator -f
```

O unit roda como `root` para acessar `/var/run/docker.sock`. Alternativa mais
restrita: crie um usuário no grupo `docker` e ajuste `User=`.

## 6. Docker Compose (alternativa)

```bash
cp .env.example .env   # edite
docker compose up -d --build
docker compose logs -f orchestrator
```

O compose monta `/var/run/docker.sock` e um volume para o estado. O
`Dockerfile` do orquestrador fica em `orchestrator/Dockerfile` para não
substituir o Dockerfile do site Portainer no fork.

## 7. Políticas de grupo do Kasm

No grupo dos usuários de teste:

| Setting | Valor | Efeito |
|---|---|---|
| `keepalive_expiration_action` | `delete` | apaga a sessão ao expirar |
| `keepalive_expiration` | `300` | 5 min sem keepalive |
| `idle_disconnect` | `5` | desconecta após 5 min ocioso |

O fechamento da aba dispara a limpeza no Kasm; o orquestrador remove o backend
no poll seguinte (≤ 5 s).

## 8. Validar

1. Clique em **Teste: Nextcloud**.
2. Em paralelo:
   ```bash
   watch -n2 'sudo docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}" | grep -E "nextcloud|kasm"'
   ```
3. O contêiner `nextcloud` deve aparecer em até ~5 s; o navegador deve carregar
   o Nextcloud em 10–30 s (primeiro boot). A URL é `http://nextcloud:80`
   (override); se editar o workspace e voltar para 443, use `https` +
   `--ignore-certificate-errors`, pois o LinuxServer usa certificado
   autoassinado.
4. Feche a aba; aguarde a política do grupo + poll. O contêiner `nextcloud`
   deve desaparecer.
5. Repita com Gitea e Uptime Kuma.

Teste de DNS do backend, sem Kasm:

```bash
sudo docker run --rm --network kasm_default_network curlimages/curl:latest \
  -sS -o /dev/null -w '%{http_code}\n' http://nextcloud:80
```

## 9. Pré-pull (recomendado)

O primeiro clique baixa a imagem do backend e pode demorar. Pré-baixe os apps
do piloto:

```bash
sudo docker pull linuxserver/nextcloud:latest
sudo docker pull gitea/gitea:latest
sudo docker pull louislam/uptime-kuma:latest
sudo docker pull kasmweb/chrome:1.19.0
```

## 10. Rollback

- Pare o orquestrador: `sudo systemctl disable --now kasm-orchestrator`.
- Remova backends gerenciados:
  `sudo docker ps -a --filter label=kasm-teste.managed=true -q | xargs -r sudo docker rm -f`
- Remova o registry no UI (Admin → Workspaces → Registry).
- As workspaces importadas podem ser removidas em **Admin → Workspaces**.

## 11. Segurança

- A API key não deve ser commitada; `.env` e `.secrets.json` estão no
  `.gitignore`.
- O orquestrador só remove contêineres com os labels `kasm-teste.*`; conflito
  de nome com contêiner alheio é recusado.
- Templates `privileged` e `network: host` são pulados por padrão no conversor.
- `KASM_VERIFY_TLS=false` é aceitável só no piloto; instale a CA em produção.
