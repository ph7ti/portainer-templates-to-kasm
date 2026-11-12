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
| Orquestrador `--once` contra API real | host 10.1.0.108 | modo `legacy`, 3 imagens, 0 sessões, sem erros |
| E2E Kasm (clique → backend → fechamento) | pendente | requer registry importado e clique no UI |

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
| Nextcloud | `--kiosk http://nextcloud:443` | `linuxserver/nextcloud:latest` |
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

## 5. E2E (a preencher)

Roteiro:

1. Adicionar o registry no Kasm (HTTPS) e importar 420 workspaces.
2. Clicar em **Teste: Nextcloud**; medir tempo até o app carregar.
3. Confirmar backend `nextcloud` em `docker ps`.
4. Fechar a aba; medir tempo até o backend sumir (política + poll).
5. Repetir para Gitea e Uptime Kuma.
6. Monitorar 30 min e conferir ausência de órfãos
   (`docker ps -a --filter label=kasm-teste.managed=true`).

Resultados observados serão anexados aqui.
