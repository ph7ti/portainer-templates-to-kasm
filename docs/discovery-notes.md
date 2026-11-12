# Discovery — Portainer → Kasm Registry + Orquestração

Data: 2026-11-12
Status: descoberta concluída; bloqueado aguardando acessos ao Kasm e ao host Docker.

## 1. Fontes inspecionadas

| Fonte | O que foi verificado |
|---|---|
| `ph7ti/portainer-templates-to-kasm` (fork, branch `main`) | 884 templates, `version: "3"`, `Schema.json` (Portainer App Template V3), `templates.json` de 1,4 MB |
| `kasmtech/workspaces_registry_template` (branch `1.1`) | Schema 1.1 do registry, estrutura `workspaces/<Nome>/workspace.json`, workflow de GitHub Pages |
| Kasm Docs 1.17 / 1.19 | Protected Web Apps (`run_config.environment.LAUNCH_URL` com `--kiosk`), Developer API (duas gerações) |
| OpenAPI público do Kasm (2,4 MB) | Endpoints `/api/session/sessions` e `/api/admin/session/sessions`, schema `SessionSummary` |

Cópias locais: `repos/` (arquivos extraídos) e `research/` (OpenAPI + markdown da API 1.17).

## 2. Dados do Portainer (fatos, não suposições)

- `templates.json`: `version = "3"`, 884 templates.
- Tipos: **464 type 1** (container), 404 type 3 (stack), 16 type 2 (swarm).
- Type 1: 439 têm ao menos uma porta TCP; 5 têm somente UDP (ex.: 69/udp); 20 não têm portas (agentes/CLI, ex.: Watchtower) → não são web apps.
- **O campo `architectures` não existe** em nenhum template. O `Schema.json` do Portainer também não o define. Portanto o mapeamento do briefing precisa de fallback: `["amd64"]` por padrão.
- Portas: formatos `host:container/tcp`, `container/tcp`, `host:container/udp`. Não há formato sem protocolo.
- 39 imagens vêm de registries não-Docker-Hub (`ghcr.io`, `registry.gitlab.com`, etc.).
- 5 templates type 1 sem logo → usar `default-icon.png`.
- 4 templates com mais de 3 categorias → truncar em 3.
- 17 templates `privileged`, 13 com `network: host` → exigem tratamento/aviso (backend pode não funcionar isolado).
- Colisões de slug (após remover sufixo "(container)"): **nenhuma** nos 464 type 1.
- Títulos são únicos; maior título tem 48 caracteres.
- `env` sem `default`: 48 ocorrências (1.391 no total) → apps que podem não subir sem configuração manual.

## 3. Schema Kasm 1.1 (registry)

Obrigatórios: `friendly_name`, `description`, `image_src`, `architecture`, `compatibility`.
`compatibility[] = { version, image, uncompressed_size_mb, available_tags? }`.
Opcionais: `categories` (máx. 3), `docker_registry`, `run_config`, `exec_config`, `notes`, `cores`, `memory`, `gpu_count`, `cpu_allocation_method`.

- Kasm **1.16+** usa schema **1.1**; 1.12–1.15 usam 1.0.
- A imagem do workspace é `kasmweb/chrome:<tag da versão instalada>`; a imagem do app **não** entra no `workspace.json`.
- Protected Web App confirmado na documentação oficial:
  ```json
  "run_config": { "environment": { "LAUNCH_URL": "--kiosk http://<slug>:<porta>" } }
  ```
  (`--kiosk` é opcional; o briefing pede kiosk.)
- `docker_registry` do workspace deve apontar para o registry da imagem `kasmweb/*` (Docker Hub), não para o registry da app.

## 4. APIs do Kasm — duas gerações (importante)

| Versão | Base | Autenticação | Listagem de sessões | Campos relevantes |
|---|---|---|---|---|
| ≤ 1.17 (Developer API "public") | `/api/public/` | JSON body `{api_key, api_key_secret}` em POST | `POST /api/public/get_kasms` → `{kasms: [...]}` | `kasm_id`, `image.friendly_name`, `container_id`, `container_ip`, `user_id`, `operational_status`; **não** expõe `docker_network` no exemplo |
| 1.19+ (Developer API nova) | `/api/` | HTTP Basic `api_key:api_key_secret` | `GET /api/session/sessions` → `{sessions: [...]}`; admin: `/api/admin/session/sessions` | `SessionSummary` inclui `docker_network`, `container_id`, `image` (friendly_name/name), `session_id` |

Consequência para o orquestrador:

1. Detectar a geração da API na inicialização (tentar `/api/session/sessions` com Basic; cair para `/api/public/get_kasms`).
2. Normalizar os dois formatos para um `Session` interno.
3. **Não depender de `docker_network` na API**: inspecionar o contêiner do navegador via `container_id` e ler `NetworkSettings.Networks`. Isso cobre 1.17 e redes efêmeras.
4. Fallback final: rede fixa configurada por env var.

## 5. Arquitetura — pontos que o briefing não cobre

- **Backend único por app**: com `LAUNCH_URL` estático no `workspace.json`, o nome do backend tem de ser previsível (`<slug>`). Isso significa **um contêiner por app**, compartilhado entre sessões simultâneas. O briefing fala em "isolada", mas isolamento por usuário exigiria nome/URL dinâmica por sessão, o que o registry não suporta hoje.
  - Proposta: referência por contagem de sessões (inicia na 1ª, remove quando a última sumir) + documentar a limitação; opcionalmente limitar 1 sessão simultânea por app no grupo do Kasm.
- **Host do orquestrador**: tem de rodar no host Docker do agente Kasm (socket Docker). Esta máquina Windows não tem daemon Docker ativo (Docker Desktop parado); integração/E2E precisam rodar no host do Kasm.
- **Volumes**: binds do Portainer apontam para caminhos do host (ex.: `/srv/lsio/nextcloud/...`). Para ambientes efêmeros, o manifesto deve prever volume nomeado ou diretório-base controlado; decidir na implementação.
- **Rede efêmera**: se `auto_generate_kasm_docker_networks=true`, anexar o backend à rede descoberta via `container_id`; remover o backend antes de o Kasm destruir a rede (corrida).
- **Registry no GitHub**: o fork atual é o site do Portainer, não um registry Kasm. Para GitHub Pages é necessário um repositório criado a partir de `kasmtech/workspaces_registry_template` (branch 1.1), público.

## 6. Métricas estimadas do conversor

- 439 workspaces potencialmente conversíveis (type 1 com porta TCP).
- 20 type 1 sem porta → pular com motivo.
- 5 somente UDP → pular (não é web).
- 404 stacks + 16 swarm → fora de escopo nesta fase.
- Logos: ~434 downloads externos (raw.githubusercontent.com concentra 340).
- `uncompressed_size_mb`: consultar Docker Hub/tag da imagem `kasmweb/chrome` ou adotar valor documentado.

## 7. Pendências para iniciar o desenvolvimento

1. URL base + versão exata do Kasm.
2. `API_KEY` + `API_KEY_SECRET` de admin (permissões: Sessions View, Users View, Images View, Registries View/Create/Modify).
3. Acesso ao host Docker (SSH) ou confirmação de que o orquestrador será executado por outra pessoa.
4. Layout do Kasm: single-server ou multi-agent; `auto_generate_kasm_docker_networks`; redes existentes.
5. Decisão sobre repositório do registry (novo `kasm-registry` vs. fork) e token do GitHub se formos publicar.
6. Aceite da limitação de backend compartilhado por app (ou definir restrição de 1 sessão/app).
7. Definir lote inicial (recomendado: 3 apps de aceitação + lote piloto de ~20).
