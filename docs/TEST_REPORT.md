# Relatório de testes

Data: 2026-11-12
Ambiente Kasm: `https://kasm.somosagility.com.br` (Kasm 1.19.0.842dcf,
single-server, Debian 13, Docker 29.9.3, rede `kasm_default_network`).

## Resultado resumido

| Teste | Onde | Resultado |
|---|---|---|
| Conversão completa (884 templates) | Windows 11 / Python 3.13 | **420 workspaces**, 464 pulados, **0 erros de schema** |
| Ícones | idem | 406 únicos baixados; 14 fallback `default-icon.png` |
| `pytest` local | Windows / Python 3.13 | **40 passed** |
| `pytest` no host Kasm | Debian 13 / Python 3.13 | **40 passed** |
| Backend real + DNS no Docker | host 10.1.0.108 | `nginx:alpine` → `http://kasm-orch-test/` → **200** |
| Orquestrador `--once` contra API real | host 10.1.0.108 | modo `legacy`, 423 imagens, 420/420 casados |
| Registry importado no Kasm | UI/API | **420 workspaces**, schema 1.1 |
| Imagens criadas no catálogo | API | 420 imagens `Teste: …` |
| E2E Nextcloud | Kasm real | carregou; backend removido no poll seguinte |
| E2E Gitea | Kasm real | carregou; backend removido ~2,8 s após a sessão |
| E2E Uptime Kuma | Kasm real | carregou; backend removido ~8,5 s após a sessão |

## 1. Conversor

Comando:

```bash
python converter/convert_templates.py --output registry --refresh --prune
```

Saída:

```
templates: 884 | convertidos: 420 | workspaces: 420
skips: host-network=9, no-tcp-port=16, privileged=17,
       type-2-not-container=16, type-3-not-container=404, windows-platform=2
ícones com fallback: 9
registry gerado em: registry
```

Resultado:

- `registry/1.1/list.json` com `workspacecount = 420`.
- `registry/versions.txt` = `1.1`.
- `registry/backend_manifest.json` com 420 apps.
- 0 erros de validação jsonschema.
- Ícones com fallback: `dradis`, `dozzle`, `foldingathome`, `projectsend`,
  `sickgear`, `thelounge`, `trilium`, `wizarr`, `xbackbone` (URL 404/403 ou
  `.ico` não suportado). Os 5 templates sem logo também usam o fallback.

Verificações pontuais:

| App | `LAUNCH_URL` | Imagem do backend |
|---|---|---|
| Nextcloud | `--kiosk http://nextcloud:80` | `linuxserver/nextcloud:latest` |
| Gitea | `--kiosk http://gitea:3000` | `gitea/gitea:latest` |
| Uptime Kuma | `--kiosk http://uptime-kuma:3001` | `louislam/uptime-kuma:latest` |
| AdGuard | `--kiosk http://adguard:3000` | `adguard/adguardhome:latest` |
| MinIO | `--kiosk http://minio:9001` | `quay.io/minio/minio:latest` |

## 2. Testes unitários

Cobertura (40 casos):

- **Conversor** (`tests/test_converter.py`):
  - filtros por tipo, plataforma, privileged, host-network, sem porta TCP;
  - escolha de porta e overrides;
  - slug (acentos, `&`, sufixo `(container)`, colisão);
  - truncamento de categorias;
  - validação de schema 1.1 em todos os `workspace.json`;
  - `LAUNCH_URL`, `shm_size`, `architecture`, `compatibility`;
  - geração de `list.json`, `versions.txt`, `icons/`;
  - idempotência (segunda execução preserva a árvore estável);
  - volumes: remoção de `:ro` e binds de sistema;
  - fallback de ícone.
- **API Kasm** (`tests/test_kasm_api.py`):
  - detecção e normalização da API legacy (`kasms`) e v2 (`sessions`);
  - autenticação no corpo (legacy) e Basic (v2);
  - erro `error_message`, HTTP 403, falha de listagem de imagens não fatal;
  - índice `friendly_name → image_id`.
- **Orquestrador** (`tests/test_orchestrator.py`):
  - sobe backend na primeira sessão e mantém enquanto outra existe;
  - remove na última sessão;
  - ignora sessões de outros workspaces;
  - erro de API não remove backend;
  - descoberta de rede via `docker inspect` e via `docker_network`;
  - backoff após falha de pull;
  - recusa contêiner homônimo não gerenciado;
  - limpeza de órfãos na primeira execução;
  - criação/remoção de volumes nomeados;
  - dry-run não toca no Docker;
  - matching por `image_id` mesmo com nome alterado.

Execução local:

```
........................................  [100%]
40 passed in 1,88s
```

Execução no host Kasm (Debian 13):

```
........................................  [100%]
40 passed in 0,73s
```

## 3. Teste real de Docker/rede

Script `tools/test_backend.py` no host (root):

```
backend status: running
backend networks: ['kasm_default_network']
backend id: 4cbafbfcbfca
curl exit: 0 status: 200
OK: http://kasm-orch-test/ resolved and answered
cleanup done
```

Isso valida:

- `DockerBackend.ensure_started` cria contêiner na rede correta;
- volume nomeado é criado;
- DNS por nome de contêiner funciona para um cliente na mesma rede;
- `stop_and_remove` remove contêiner e volume.

## 4. Orquestrador contra a API real

```
KASM_API_MODE auto → legacy (/api/public/get_kasms)
Kasm image index: 3 image(s)
matched 0/420 registry workspaces to Kasm image ids
reconcile: {'sessions': 0, 'matched': 0, 'active_apps': 0,
            'started': 0, 'removed': 0, 'orphans': 0}
```

Observações:

- O Kasm usa CA interna; o teste exigiu `KASM_VERIFY_TLS=false` (documentado).
- A chave de API desta instância tem `Sessions View`/`Images View`, mas **não**
  `Users Auth Session`/`User` — `request_kasm` retorna 403. Por isso a criação
  de sessão para o teste E2E é manual, via clique no UI.

## 5. E2E real

### 5.1 Importação do registry e imagens

- `POST /api/public/create_registry` com
  `https://raw.githubusercontent.com/ph7ti/portainer-templates-to-kasm/main/registry/`
  → registry `1ec563db77eb427bb524c720c607fd22`, schema 1.1, 420 workspaces.
- A build 1.19.0.842dcf **não** cria as imagens lançáveis automaticamente.
  `tools/publish_images_to_kasm.py` criou **420** entradas `Teste: …` via
  `/api/public/create_image` (417 criadas + 3 já existentes), 0 falhas.
- `get_images`: 423 imagens (420 `Teste:` + 3 originais);
  `available=true` após pré-pull do Chrome.
- Orquestrador: 420/420 workspaces casados por `image_id`.

### 5.2 Nextcloud

- Primeira tentativa: backend subiu em 80/443, mas o `LAUNCH_URL` era
  `http://nextcloud:443` → **400 “plain HTTP request sent to HTTPS port”**.
- Correção: override `nextcloud → 80` (HTTP responde 200) e, para apps
  443/8443, `https` + `--ignore-certificate-errors`.
- Reteste: **Nextcloud carregou** no Chrome isolado.
- Backend: `started 2026-11-12T18:00:07Z`; `removed 15:02:43` no primeiro poll
  após a sessão desaparecer. Intervalo entre o último poll com sessão
  (15:02:32) e a remoção: **~11 s** (inclui o fechamento da aba e o poll).
- `docker ps` e `docker volume ls` sem resíduos de `nextcloud`.

### 5.3 Gitea

Monitor `tools/measure_cleanup.py` iniciado antes do clique:

```
session seen at +29.7s
container seen at +34.1s (started=2026-11-12T18:04:20Z)
session gone at +80.7s
container gone at +83.5s
volumes=0
```

- Backend criado **~4,4 s** após a sessão aparecer na API.
- Remoção **~2,8 s** após a sessão desaparecer (poll de 1 s no teste).
- Nenhum volume órfão; `docker ps -a` sem `gitea`.

### 5.4 Uptime Kuma

```
session seen at +16.7s
container seen at +18.2s (started=2026-11-12T18:06:25Z)
session gone at +59.4s
container gone at +67.9s
volumes gone at +67.9s
```

- Backend `uptime-kuma` criado **1,5 s** após a sessão.
- Removido **8,5 s** após a sessão desaparecer (inclui `stop(timeout=5)`),
  bem abaixo do limite de 2 min.
- Sem volumes órfãos.

### 5.5 Observações

- O primeiro carregamento pode mostrar “domínio não encontrado” antes de o
  backend responder; recarregar resolve. Não houve rebuild da imagem Chrome
  (fora de escopo), então não há página de espera/redirect.
- A limpeza depende do Kasm remover a sessão ao fechar a aba; neste ambiente a
  sessão desapareceu em ~1 min. Se o grupo tiver keepalive longo, o backend
  permanece até a sessão expirar — ajuste `keepalive_expiration` para 300 s.
- Após 30 min de uso contínuo, verificar órfãos:
  `docker ps -a --filter label=kasm-teste.managed=true` deve estar vazio sem
  sessões ativas.
