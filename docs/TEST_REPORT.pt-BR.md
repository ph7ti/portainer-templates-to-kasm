[English](TEST_REPORT.md)

# Relatório de testes

Data: 2026-11-12
Kasm: `https://kasm.somosagility.com.br` (1.19.0.842dcf, single-server, Debian
13, Docker 29.9.3, rede `kasm_default_network`).

## Resumo

| Teste | Onde | Resultado |
|---|---|---|
| Conversão completa (884 templates) | Windows 11 / Python 3.13 | **420 workspaces**, 464 pulados, **0 erros de schema** |
| Ícones | idem | 406 únicos baixados; 14 fallback `default-icon.png` |
| `pytest` local | Windows / Python 3.13 | **50 passed** |
| `pytest` no host Kasm | Debian 13 / Python 3.13 | **50 passed** |
| Backend real + DNS | host 10.1.0.108 | `nginx:alpine` → `http://kasm-orch-test/` → **200** |
| Orquestrador `--once` | host / API real | modo `legacy`, 423 imagens, 420/420 casados |
| Registry público | GitHub Pages (`ph7ti/kasm-registry`) | deploy success; `versions.txt`/`list.json` 200 |
| Importação no Kasm | API | 420 workspaces, schema 1.1 |
| Instalação de imagens | API | 420 imagens `Teste: …` |
| E2E Nextcloud | Kasm real | carregou; backend removido no poll seguinte |
| E2E Gitea | Kasm real | carregou; backend removido ~2,8 s após a sessão |
| E2E Uptime Kuma | Kasm real | carregou; backend removido ~8,5 s após a sessão |
| Página de espera | unit/HTTP + clique real | validada: página apareceu, fez polling e redirecionou ao Nextcloud |
| App HTTPS | Duckstation | 3000 é HTTP; 3001 é HTTPS; redirect para `https://duckstation:3001` carregou |

## 1. Conversor

```bash
python converter/convert_templates.py --output registry --refresh --prune
```

```
templates: 884 | converted: 420 | workspaces: 420
skips: host-network=9, no-tcp-port=16, privileged=17,
       type-2-not-container=16, type-3-not-container=404, windows-platform=2
icon fallbacks: 9
```

- `versions.txt` = `1.1`; `workspacecount = 420`.
- 0 erros de jsonschema.
- Fallbacks: dradis, dozzle, foldingathome, projectsend, sickgear, thelounge,
  trilium, wizarr, xbackbone.

Exemplos:

| App | LAUNCH_URL com página de espera | Imagem do backend |
|---|---|---|
| Nextcloud | `--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80` | `linuxserver/nextcloud:latest` |
| Gitea | `--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fgitea%3A3000` | `gitea/gitea:latest` |
| Uptime Kuma | `--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fuptime-kuma%3A3001` | `louislam/uptime-kuma:latest` |
| Code Server | `--kiosk --ignore-certificate-errors http://kasm-wait:8080/wait?target=https%3A%2F%2Fcode-server%3A8443` | `linuxserver/code-server:latest` |

## 2. Testes unitários (50)

- **Conversor**: filtros, portas/overrides, slug, categorias, schema,
  `LAUNCH_URL`/página de espera, HTTPS/flag, arquivos do registry, idempotência,
  volumes, fallback de ícone.
- **Página de espera**: template, `/healthz`, `/wait`, alvos inválidos
  (XSS/open redirect), marcadores do polling.
- **API Kasm**: detecção/normalização legacy/v2, autenticação, erros, índice de
  imagens.
- **Orquestrador**: start/stop/refcount, sessões de outros apps, erros de API,
  descoberta de rede, backoff, conflito de nome, órfãos, volumes, dry-run,
  matching por `image_id`.

```
.........................................  [100%]
50 passed in 4,89s        # Windows
50 passed in 4,45s        # host Debian 13
```

## 3. Smoke test Docker/rede

`tools/test_backend.py` no host:

```
backend status: running
backend networks: ['kasm_default_network']
curl exit: 0 status: 200
OK: http://kasm-orch-test/ resolved and answered
cleanup done
```

## 4. API real do Kasm

```
KASM_API_MODE auto → legacy (/api/public/get_kasms)
Kasm image index: 423 image(s)
matched 420/420 registry workspaces to Kasm image ids
reconcile: sessions=0, active_apps=0, started=0, removed=0, orphans=0
```

A instância usa CA interna; `KASM_VERIFY_TLS=false` é necessário no piloto. A
API key tem `Sessions View`/`Images View`, mas não `Users Auth Session`; as
sessões E2E foram criadas manualmente no UI.

## 5. Registry público

- Repositório: https://github.com/ph7ti/kasm-registry
- Pages: https://ph7ti.github.io/kasm-registry/
- `versions.txt` 200; `1.1/list.json` 200 (`workspacecount=420`);
  `1.1/icons/nextcloud.png` 200.
- `list_url` aponta para Pages; a URL do Nextcloud inclui o waiter.
- A entrada antiga (raw) no Kasm foi removida para evitar duplicidade.

## 6. E2E

### Nextcloud

- Primeira tentativa: backend em 80/443, mas `http://nextcloud:443` retornou
  **400 “plain HTTP request sent to HTTPS port”**.
- Correção: override Nextcloud para 80/HTTP; portas HTTPS usam `https` +
  `--ignore-certificate-errors`.
- Reteste: **Nextcloud carregou**.
- Backend iniciado 15:00:07; removido 15:02:43 no primeiro poll após a sessão
  desaparecer (~11 s desde o último poll com sessão).
- Sem contêineres ou volumes residuais.

### Gitea

```
session seen at +29.7s
container seen at +34.1s
session gone at +80.7s
container gone at +83.5s
volumes gone at +83.5s
```

Backend criado ~4,4 s após a sessão; removido ~2,8 s após ela desaparecer.

### Uptime Kuma

```
session seen at +16.7s
container seen at +18.2s
session gone at +59.4s
container gone at +67.9s
volumes gone at +67.9s
```

Backend criado ~1,5 s após a sessão; removido ~8,5 s após ela desaparecer
(inclui `stop(timeout=5)`).

### Duckstation (exceção HTTPS)

- O template expõe 3000 e 3001. Verificação:
  - `http://duckstation:3000` → 200 (nginx, HTTP)
  - `https://duckstation:3001` → 200 (nginx, TLS autoassinado)
  - `https://duckstation:3000` → `ERR_SSL_PROTOCOL_ERROR`
- Correção: `port_overrides.json` mapeia `duckstation → 3001` e
  `scheme_overrides.json` mapeia `duckstation → https`; a imagem no Kasm foi
  atualizada para `--kiosk --ignore-certificate-errors ... target=https%3A%2F%2Fduckstation%3A3001`.
- Reteste: esperou na página, redirecionou e o **Duckstation carregou via HTTPS**.

## 7. Página de espera

- `kasm-wait` construído e rodando na `kasm_default_network`.
- `/healthz` → 200; `/wait?target=http://nextcloud:80` → 200 HTML (4,9 KB).
- Alvo inválido (`javascript:`) → 400.
- Clique real: a página apareceu (“Preparando seu aplicativo…”), o backend foi
  criado e o navegador redirecionou sozinho ao Nextcloud.
- Após fechar a aba, o orquestrador removeu o `nextcloud`; restou apenas uma
  sessão de VS Code de outro usuário. O `kasm-wait` continuou no ar (esperado).
- Alvos HTTPS com certificado autoassinado podem não responder ao probe
  no-cors; a página oferece “Abrir agora” após 60 s.

## 8. Limitações conhecidas

- Um backend compartilhado por app, sem isolamento por usuário.
- 420/464 templates type 1; 44 pulados (privileged, host-network, sem porta TCP,
  Windows); stacks/Swarm fora.
- Soak test de 30 min não executado; sem órfãos após as três sessões E2E.
- A página de espera não é um gate de readiness; o app pode inicializar depois
  do redirect.
