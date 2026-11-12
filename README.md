# Portainer → Kasm Workspaces

Piloto de **Protected Web Apps efêmeros**: cada template de contêiner do
[portainer-templates](https://github.com/lissy93/portainer-templates) vira um
workspace no catálogo do Kasm. Ao clicar, o Kasm abre um **Chrome isolado**
apontando para o serviço; um orquestrador externo cria o contêiner de backend
Docker na mesma rede e o remove quando a última sessão termina.

Testado em **Kasm Workspaces 1.19.0.842dcf** (single-server, Docker 29.9.3,
Debian 13) com a rede fixa `kasm_default_network` e
`auto_generate_kasm_docker_networks: false`.

---

## Arquitetura

```
┌──────────┐   1. clica "Teste: Nextcloud"   ┌──────────────────────────────┐
│ Usuário  │ ───────────────────────────────▶ │ Kasm Manager / UI            │
└──────────┘                                  └──────────────┬───────────────┘
                                                             │ 2. cria sessão
                                                             ▼
                                              ┌──────────────────────────────┐
                                              │ Chrome isolado (kasmweb/     │
                                              │ chrome:1.19.0) na rede       │
                                              │ kasm_default_network         │
                                              └──────────────┬───────────────┘
                                                             │ 3. --kiosk
                                                             │    http://nextcloud:80
                                                             ▼
┌──────────────────────────┐   4. poll /api/public/get_kasms   ┌──────────────────────────┐
│ Orquestrador (systemd/   │ ◀──────────────────────────────── │ Kasm API                 │
│ container, host Docker)  │                                   └──────────────────────────┘
│                          │   5. docker create/remove
│  sessão ativa?           │ ────────────────────────────────▶ ┌──────────────────────────┐
│  sim → garante backend   │                                   │ Backend Docker           │
│  não → remove backend    │                                   │ name=nextcloud           │
└──────────────────────────┘                                   │ image=linuxserver/...    │
                                                               └──────────────────────────┘
```

O navegador do Kasm resolve o backend pelo **nome DNS do contêiner** dentro da
rede Docker compartilhada. O Kasm cuida do ciclo de vida do navegador; o
orquestrador cuida do ciclo de vida do backend.

## Componentes

| Diretório | Conteúdo |
|---|---|
| `converter/` | `convert_templates.py`, schema 1.1 derivado, overrides de porta |
| `orchestrator/` | cliente das APIs do Kasm, lifecycle Docker, reconciliação, entrypoint |
| `tests/` | 40 testes unitários/integrados com mocks de Kasm e Docker |
| `registry/` | registry Kasm pronto (`versions.txt`, `1.1/list.json`, ícones, workspaces) e `backend_manifest.json` |
| `examples/` | 3 `workspace.json` gerados (Nextcloud, Gitea, Uptime Kuma) |
| `docs/` | arquitetura, deploy, relatório de testes e notas de discovery |
| `systemd/` | unit de exemplo para o orquestrador |
| `tools/` | smoke test do backend e bootstrap de dependências local |

## Estado atual

- **420 workspaces** gerados a partir de 464 templates `type 1`:
  - 439 têm porta TCP; 17 são `privileged` e 9 usam `network: host` (pulados por segurança), 16 não têm porta, 2 são Windows.
  - 404 stacks (`type 3`) e 16 Swarm (`type 2`) ficam fora desta fase.
- Registry validado contra schema 1.1 derivado: **0 erros**.
- 406 ícones únicos baixados; 14 templates sem logo/ícone válido usam `default-icon.png`.
- 40 testes passando (Windows e Linux/Debian 13).
- Registry importado no Kasm de teste: **420 workspaces** (schema 1.1).
- Imagens `Teste: Nextcloud`, `Teste: Gitea` e `Teste: Uptime Kuma` criadas no
  catálogo via API; as demais são criadas em lote por
  `tools/publish_images_to_kasm.py`.
- Backend real validado no host: `nginx:alpine` na `kasm_default_network` respondeu **200** por DNS (`http://kasm-orch-test/`).
- Orquestrador executado contra a API real do Kasm: modo `legacy`, 423 imagens
  indexadas, 420/420 workspaces casados por `image_id`.
- E2E validado com **Nextcloud**, **Gitea** e **Uptime Kuma**: backend criado
  em 1,5–4,4 s após a sessão aparecer; removido em 2,8–8,5 s após a sessão
  desaparecer; sem volumes órfãos.

## Mapeamento Portainer → Kasm 1.1

| Portainer | Kasm |
|---|---|
| `title` | `friendly_name = "Teste: <título sem (container)>"` |
| `description` | descrição + aviso de acesso efêmero/isolado |
| `logo` | baixado para `workspaces/<slug>/<slug>.<ext>` e `1.1/icons/`; fallback `default-icon.png` |
| `categories` | primeiras 3 categorias |
| `ports` | escolhe a porta do contêiner (TCP; prioridade 443/80/8443/3000/3001/…, overrides em `converter/port_overrides.json`) |
| `image` | usada **somente** no `backend_manifest.json` e no orquestrador |
| (fixo) | `architecture = ["amd64"]` (o `templates.json` não traz arquiteturas) |
| (fixo) | `compatibility = [{version: "1.19.x", image: "kasmweb/chrome:1.19.0", uncompressed_size_mb: 2500}]` |
| (fixo) | `run_config.environment.LAUNCH_URL = "--kiosk [--ignore-certificate-errors] <scheme>://<slug>:<porta>"` |
| (fixo) | `run_config.shm_size = "512m"` |

O `notes` de cada workspace documenta o nome do contêiner de backend, a imagem e
a URL alvo para troubleshooting.

O esquema é escolhido pela porta do contêiner: `https` para 443/8443/9443
(com `--ignore-certificate-errors`, pois os apps usam certificados
autoassinados) e `http` para as demais. Exceções conhecidas ficam em
`converter/port_overrides.json` — por exemplo, o Nextcloud expõe 80 e 443, mas
o template só lista 443; usamos **80/HTTP** para evitar o erro
“plain HTTP request sent to HTTPS port”.

> **Nota sobre `architectures`**: o briefing pedia o mapeamento
> `architectures → architecture`, mas o `templates.json` atual e o
> `Schema.json` do Portainer não possuem esse campo. O conversor assume
> `["amd64"]` e permite trocar via código; a imagem `kasmweb/chrome:1.19.0` é
> amd64.

## Pré-requisitos

1. Kasm Workspaces com a Developer API habilitada.
   - API key em **Settings → Developers → Add API Key**.
   - Permissões mínimas: `Sessions View`, `Images View`.
   - Nesta instalação a API é a **legacy** (`POST /api/public/get_kasms`).
     Builds mais novas usam `GET /api/session/sessions` (o cliente detecta).
2. Docker no mesmo host/agente do Kasm, com acesso ao socket
   (`/var/run/docker.sock`).
3. Rede Docker compartilhada entre os workspaces e o backend.
   - Aqui: `kasm_default_network`, fixa
     (`auto_generate_kasm_docker_networks: false`).
   - Se o Kasm usar rede efêmera por sessão, o orquestrador descobre a rede
     inspecionando o contêiner do navegador (`container_id`).
4. Imagem do navegador `kasmweb/chrome:1.19.0` (ou o tag correspondente à sua
   versão). Ela é definida no registry e baixada pelo agente no primeiro uso.
5. HTTPS para o registry. O Kasm **rejeita** URL `http://` ao adicionar um
   registry; use GitHub Pages/raw.githubusercontent.com ou outro host HTTPS.

## Uso rápido

### 1. Converter os templates

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python converter/convert_templates.py --output registry --refresh --prune
```

O comando:

- baixa `templates.json` do fork (`--source` para outro caminho/URL);
- gera `registry/1.1/workspaces/<slug>/workspace.json` + ícone;
- gera `registry/1.1/list.json`, `registry/versions.txt`, `registry/list.json`;
- gera `registry/backend_manifest.json`;
- é idempotente e valida cada saída contra `converter/kasm_workspace_1.1.schema.json`.

Opções úteis: `--max-apps 20` (piloto), `--no-icons`, `--offline`,
`--allow-privileged`, `--allow-host-network`, `--prune`.

### 2. Publicar o registry

O registry é estático. Neste fork ele vive em `registry/` e é publicado em
GitHub Pages pelo workflow `.github/workflows/deploy-registry.yml`.

- Pages (habilitado e publicado): `https://ph7ti.github.io/portainer-templates-to-kasm/`
- Alternativa sem Pages (funciona porque o Kasm só faz GET em arquivos):
  `https://raw.githubusercontent.com/ph7ti/portainer-templates-to-kasm/main/registry/`

O workflow `.github/workflows/deploy-registry.yml` já rodou com sucesso; o Pages
está configurado com `build_type: workflow`.

Adicione a URL **sem barra de versão** em
**Admin → Workspaces → Registry → Add new**.

> Nesta build (1.19.0.842dcf) o Kasm importa os 420 workspaces no registry, mas
> **não** cria automaticamente as 420 entradas em `Admin → Workspaces` (as
> imagens lançáveis). Isso pode ser feito pelo botão **Add** de cada item no
> registry ou em lote com a API:

```bash
# usa .secrets.json (kasm_url/api_key/api_key_secret) ou as env vars KASM_*
python tools/publish_images_to_kasm.py            # idempotente
python tools/publish_images_to_kasm.py --limit 10 # piloto
python tools/publish_images_to_kasm.py --dry-run
```

A chave precisa de permissão de criação de imagens; a chave usada no piloto
conseguiu criar `Teste: Nextcloud`, `Teste: Gitea` e `Teste: Uptime Kuma`.
Depois disso, os itens `Teste: …` aparecem no catálogo de Workspaces.

### 3. Subir o orquestrador

**systemd**

```bash
sudo mkdir -p /opt/kasm-orchestrator
sudo cp -r orchestrator registry/backend_manifest.json requirements.txt /opt/kasm-orchestrator/
sudo python3 -m venv /opt/kasm-orchestrator/venv
sudo /opt/kasm-orchestrator/venv/bin/pip install -r /opt/kasm-orchestrator/requirements.txt
sudo cp systemd/kasm-orchestrator.service /etc/systemd/system/
sudo cp .env.example /opt/kasm-orchestrator/.env   # edite com URL/chaves
sudo systemctl daemon-reload && sudo systemctl enable --now kasm-orchestrator
```

**Docker Compose**

```bash
cp .env.example .env   # edite
docker compose up -d --build
docker compose logs -f orchestrator
```

Validação sem alterar nada:

```bash
docker compose run --rm orchestrator --once --dry-run
# ou, no host:
KASM_URL=... KASM_API_KEY=... KASM_API_KEY_SECRET=... \
  python -m orchestrator.orchestrator --once --dry-run
```

## Configuração

Variáveis de ambiente principais (todas em `.env.example`):

| Variável | Padrão | Descrição |
|---|---|---|
| `KASM_URL` | — | URL base do Kasm (sem `/api`) |
| `KASM_API_KEY` / `KASM_API_KEY_SECRET` | — | credenciais da API |
| `KASM_API_MODE` | `auto` | `legacy`, `v2` ou `auto` |
| `KASM_VERIFY_TLS` | `true` | `false` para CA interna (esta instalação) |
| `POLL_INTERVAL` | `5` | segundos entre reconciliações |
| `RETRY_INTERVAL` | `30` | backoff após falha ao subir um backend |
| `DOCKER_NETWORK` | `kasm_default_network` | rede fixa |
| `DOCKER_DISCOVER_NETWORK` | `true` | inspeciona a rede da sessão |
| `MANIFEST_PATH` | `registry/backend_manifest.json` | manifesto |
| `STATE_PATH` | `/var/lib/kasm-orchestrator/state.json` | estado observado |
| `PULL_IMAGES` | `true` | pull automático da imagem do app |
| `DRY_RUN` | `false` | não altera Docker |

> **TLS interno**: a instância de teste usa certificado de CA interna; o
> orquestrador precisa de `KASM_VERIFY_TLS=false` ou da CA instalada no host.
> Para produção, prefira instalar a CA.

## Políticas de grupo recomendadas (efemeridade)

No grupo dos usuários de teste (**Admin → Groups**):

| Setting | Valor |
|---|---|
| `keepalive_expiration_action` | `delete` |
| `keepalive_expiration` | `300` (5 min) |
| `idle_disconnect` | `5` (min) |

Assim o Kasm exclui a sessão do navegador quando o usuário fecha a aba/expira.
O orquestrador detecta a ausência da sessão no próximo poll (5 s) e remove o
backend. Não há necessidade de `docker stop` manual.

## Troubleshooting

| Sintoma | Causa provável | Ação |
|---|---|---|
| 502 no navegador ao abrir | backend ainda subindo ou DNS não resolve | aguarde 10–30 s e recarregue; verifique `docker logs <slug>`; o primeiro pull da imagem pode demorar |
| `http://<slug>` não resolve | backend não está na mesma rede | confira `docker inspect <slug> --format '{{json .NetworkSettings.Networks}}'` e `DOCKER_NETWORK`/`auto_generate_kasm_docker_networks` |
| backend não é criado | sessão não corresponde ao workspace | confira `image.friendly_name` na API e o `friendly_name` do workspace; rode `--once --dry-run` |
| contêiner órfão | orquestrador parado durante a sessão | ao reiniciar, ele adota/limpa por labels `kasm-teste.managed`; remova manualmente se necessário |
| `KasmApiError` 401/403 | chave sem permissão ou API key errada | gere chave com `Sessions View`/`Images View`; a chave usada aqui **não** cria sessões |
| erro de certificado | CA interna | `KASM_VERIFY_TLS=false` ou instale a CA |
| Kasm recusa o registry | URL HTTP | use HTTPS (Pages/raw) |
| nome de contêiner em uso | conflito com contêiner não gerenciado | o orquestrador recusa remover; renomeie/remova o conflito |

## Limitações conhecidas

- **Um backend por app, compartilhado**: o `LAUNCH_URL` é estático no
  `workspace.json`, então o nome do backend é previsível (`<slug>`). Duas
  sessões simultâneas do mesmo app compartilham o mesmo contêiner/serviço.
  O orquestrador usa contagem de referência (remove quando a última sessão
  sai). Para isolamento por usuário seria necessário URL dinâmica, que o
  registry 1.1 não oferece. Recomenda-se limitar a 1 sessão simultânea por app
  no grupo do Kasm durante os testes.
- **Corrida de inicialização**: o Chrome pode abrir antes do backend responder.
  No primeiro teste o navegador mostrou “domínio não encontrado” e depois 400;
  o backend já estava subindo. Mitigações: poll de 5 s, pré-pull das imagens e
  mensagem no `notes` para recarregar. **Não** foi feito rebuild da imagem do
  Chrome nem página de espera/redirect — isso violaria a restrição de não
  rebuildar imagens. Para produção, considere uma landing page/proxy de
  readiness como fase separada.
- **Certificados autoassinados**: apps em 443/8443 recebem
  `--ignore-certificate-errors`; isso reduz a validação TLS dentro do navegador
  isolado. Alternativa: instalar a CA dos apps no workspace.
- O Nextcloud usa a porta **80/HTTP** por override; a 443 do LinuxServer
  responde TLS com certificado autoassinado e o template só lista 443.
- **420 de 464** templates `type 1`: os demais são privileged/host-network,
  sem porta ou Windows. Stacks (type 3) ficam para uma fase seguinte.
- Aplicativos não-web (ex.: banco de dados, Minecraft) também entram no
  catálogo; o navegador exibirá erro/XML. Podem ser filtrados em uma lista de
  exclusão futura.
- Volumes do Portainer são convertidos em **volumes nomeados efêmeros**; dados
  não persistem entre sessões. Binds de sistema (`/etc/timezone` etc.) são
  ignorados.

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```

Cobertura atual: conversor (filtros, porta, slug, schema, idempotência,
fallback de ícone) + orquestrador (start/stop, refcount, rede efêmera,
backoff, órfãos, conflito de nome, dry-run) + cliente Kasm (legacy/v2).
Veja [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## Documentação

- [Arquitetura e decisões](docs/ARCHITECTURE.md)
- [Deploy passo a passo](docs/DEPLOY.md)
- [Relatório de testes](docs/TEST_REPORT.md)
- [Notas de discovery da instância](docs/discovery-notes.md)

## Licença

O código deste repositório segue a licença MIT do projeto original
([LICENSE](LICENSE)). Os templates e logos pertencem aos respectivos autores.
