# Portainer → Kasm Workspaces

**Registry de terceiros para o Kasm Workspaces que transforma 420 templates do
catálogo Portainer em Protected Web Apps efêmeros.** Cada workspace abre um
Chrome isolado contra uma página de espera; um orquestrador Docker cria o
contêiner da aplicação sob demanda e o remove quando a última sessão termina.

[English](README.md) · [Repositório do registry](https://github.com/ph7ti/kasm-registry) · [URL do registry](https://ph7ti.github.io/kasm-registry/)

## O que é

- Um **registry Kasm 1.1** com 420 workspaces de navegador chamados `Teste: …`
  (Nextcloud, Gitea, Uptime Kuma, AdGuard, MinIO e muitos outros).
- Uma **página de espera** (`kasm-wait`) que avisa que o contêiner está sendo
  criado, verifica o backend a cada 2 segundos e redireciona quando ele responde.
- Um **orquestrador** que consulta a API do Kasm e mantém um contêiner Docker
  por aplicação enquanto ela tiver sessões.
- Um **conversor** que gera o registry a partir do `templates.json` do Portainer,
  além de testes, exemplos e automação de deploy.

## O que não é

- **Não** é um Kasm com múltiplos contêineres por sessão. O Kasm continua com um
  contêiner de navegador por sessão; a aplicação fica em um segundo contêiner
  gerenciado externamente.
- **Não** rebuilda imagens dos apps. As imagens originais são usadas como estão.
- **Não** oferece estado isolado por usuário em sessões simultâneas do mesmo
  workspace. Um backend é compartilhado por essas sessões (com contagem de
  referência e remoção quando a última termina).
- **Não** converte templates `type 3` (stacks) nem `type 2` (Swarm) nesta fase.

## Como funciona

```
┌──────────┐  clique em "Teste: Nextcloud"  ┌─────────────────────────────┐
│ Usuário  │ ─────────────────────────────▶ │ Kasm Manager / UI           │
└──────────┘                                └──────────────┬──────────────┘
                                                          │ sessão
                                                          ▼
                                            ┌──────────────────────────────┐
                                            │ Chrome (kasmweb/chrome:1.19) │
                                            │ kiosk → kasm-wait:8080       │
                                            └──────────────┬───────────────┘
                                                          │ página consulta
                                                          │ http://nextcloud:80
                                                          ▼
┌──────────────────────────┐  /api/public/get_kasms    ┌──────────────────┐
│ Orquestrador (systemd/   │ ◀──────────────────────── │ API do Kasm      │
│ container no host Docker)│                           └──────────────────┘
│                          │  docker create/remove
│ ativo? cria backend      │ ────────────────────────▶ ┌──────────────────┐
│ sumiu? remove backend    │                           │ Backend Docker   │
└──────────────────────────┘                           │ name=nextcloud   │
                                                       │ linuxserver/...  │
                                                       └──────────────────┘
```

A URL do workspace é:

```
--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80
```

O `kasm-wait` serve uma página HTML simples. Ela consulta o alvo com
`fetch(mode:"no-cors")` a cada 2 segundos; quando o app responde, redireciona.
Se o backend demorar mais de 60 segundos, exibe o link “Abrir agora”. O
orquestrador cria o backend com o nome do slug do workspace na rede Docker
compartilhada do Kasm.

## Estado atual

Validado no **Kasm Workspaces 1.19.0.842dcf**, single-server, Docker 29.9.3,
Debian 13, rede fixa `kasm_default_network`
(`auto_generate_kasm_docker_networks: false`).

| Item | Situação |
|---|---|
| Registry | 420 workspaces, schema 1.1, GitHub Pages no ar |
| Imagens no Kasm | 420 imagens `Teste: …` instaladas |
| Página de espera | `kasm-wait` na `kasm_default_network`; mensagem/poll/redirect validados |
| Orquestrador | em execução no host do Kasm (unit `kasm-orch-test`) |
| Testes | **49 passando** (Windows e Debian 13) |
| E2E | Nextcloud, Gitea e Uptime Kuma carregaram; página de espera validada (mensagem → poll → redirect); backends removidos 2,8–8,5 s após a sessão |
| Órfãos | nenhum após os testes; sem volumes residuais |

## Estrutura do repositório

| Caminho | Finalidade |
|---|---|
| `converter/` | conversor Portainer → registry 1.1, schema e overrides de porta |
| `orchestrator/` | cliente da API, ciclo de vida Docker e reconciliação |
| `waiter/` | serviço HTTP `kasm-wait` e Dockerfile (página de espera) |
| `registry/` | snapshot gerado do registry e `backend_manifest.json` |
| `tests/` | 49 testes unitários/integrados com mocks de Kasm e Docker |
| `examples/` | três `workspace.json` gerados |
| `docs/` | arquitetura, deploy e relatório de testes (EN/PT-BR) |
| `systemd/` | unit de exemplo do orquestrador |

O **registry público** fica em um repositório separado:
[ph7ti/kasm-registry](https://github.com/ph7ti/kasm-registry). O usuário final
adiciona `https://ph7ti.github.io/kasm-registry/` ao Kasm; não precisa clonar
nem converter as fontes do Portainer.

## Início rápido

```bash
# 1. Gerar o registry
python converter/convert_templates.py --output registry --refresh --prune

# 2. Validar
python -m pytest -q

# 3. Subir página de espera + orquestrador
docker compose up -d --build

# 4. Dry-run do orquestrador
docker compose run --rm orchestrator --once --dry-run
```

No Kasm:

1. **Admin → Workspaces → Registry → Add new** com
   `https://ph7ti.github.io/kasm-registry/` (sem `/1.1`).
2. Instalar os workspaces como imagens lançáveis:

   ```bash
   python tools/publish_images_to_kasm.py --update
   ```

3. Política de grupo recomendada:
   - `keepalive_expiration_action = delete`
   - `keepalive_expiration = 300`
   - `idle_disconnect = 5`

## Página de espera

A página é servida por um contêiner Python mínimo (`waiter/`) conectado à rede
dos workspaces do Kasm. A URL é gravada em cada `workspace.json`:

```json
{
  "run_config": {
    "environment": {
      "LAUNCH_URL": "--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80"
    },
    "shm_size": "512m"
  }
}
```

No host Docker:

```bash
docker build -t kasm-wait:local -f waiter/Dockerfile .
docker run -d --name kasm-wait --restart unless-stopped \
  --network kasm_default_network kasm-wait:local
# teste
docker run --rm --network kasm_default_network curlimages/curl:latest \
  -sS http://kasm-wait:8080/healthz
```

O contêiner precisa estar na mesma rede Docker dos workspaces. Com redes
efêmeras (`auto_generate_kasm_docker_networks: true`), ele teria de ser anexado
a cada rede de sessão pelo orquestrador; este repositório foi validado com rede
fixa.

Configuração: `WAITER_PORT` (padrão `8080`). A página aceita apenas destinos
`http://host[:porta]`/`https://host[:porta]` e rejeita redirecionamentos
abertos.

## Mapeamento Portainer → Kasm

| Portainer | Kasm |
|---|---|
| `title` | `friendly_name = "Teste: <título sem (container)>"` |
| `description` | descrição + aviso de acesso efêmero/isolado |
| `logo` | baixado para `workspaces/<slug>/<slug>.<ext>` e `1.1/icons/`; fallback `default-icon.png` |
| `categories` | primeiras 3 |
| `ports` | porta do contêiner, prioridade 443/80/8443/3000/…; overrides em `converter/port_overrides.json` |
| `image` | **somente** no `backend_manifest.json` e no orquestrador |
| (fixo) | `architecture = ["amd64"]` — a fonte não tem `architectures` |
| (fixo) | `compatibility = [{version:"1.19.x", image:"kasmweb/chrome:1.19.0", ...}]` |
| (fixo) | `run_config.environment.LAUNCH_URL = "--kiosk [--ignore-certificate-errors] <página-de-espera>"` |
| (fixo) | `run_config.shm_size = "512m"` |

A URL usa HTTPS nas portas 443/8443/9443 (com `--ignore-certificate-errors`,
pois os apps costumam usar certificado autoassinado) e HTTP nas demais. O
Nextcloud é forçado para a porta 80/HTTP: o template só lista 443, mas o
LinuxServer também responde HTTP na 80, evitando o erro
“plain HTTP request sent to HTTPS port”.

## Orquestrador

Variáveis principais:

| Variável | Padrão | Descrição |
|---|---|---|
| `KASM_URL` | — | URL base do Kasm |
| `KASM_API_KEY` / `KASM_API_KEY_SECRET` | — | chave da Developer API |
| `KASM_API_MODE` | `auto` | `legacy`, `v2` ou detecção automática |
| `KASM_VERIFY_TLS` | `true` | `false` para CA interna |
| `POLL_INTERVAL` | `5` | segundos entre reconciliações |
| `RETRY_INTERVAL` | `30` | backoff após falha ao subir |
| `DOCKER_NETWORK` | `kasm_default_network` | rede fixa |
| `DOCKER_DISCOVER_NETWORK` | `true` | inspeciona a rede do navegador |
| `MANIFEST_PATH` | `registry/backend_manifest.json` | manifesto gerado |
| `STATE_PATH` | `/var/lib/kasm-orchestrator/state.json` | estado observado |
| `PULL_IMAGES` | `true` | baixa imagens ausentes |

Modos:

```bash
python -m orchestrator.orchestrator --once --dry-run
python -m orchestrator.orchestrator --once
python -m orchestrator.orchestrator          # loop de serviço
```

O orquestrador só remove contêineres com seus labels
(`kasm-teste.managed=true`); recusa tocar em contêiner homônimo não gerenciado.
Falha de API mantém os backends existentes em vez de removê-los.

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
# 49 passed
```

Cobertura: filtros/portas/slugs/schema/idempotência/ícones do conversor, rotas e
validação da página de espera, normalização legacy/v2 da API, ciclo de vida,
refcount, rede efêmera, backoff, limpeza de órfãos, dry-run e conflito de nome.

Veja [docs/TEST_REPORT.pt-BR.md](docs/TEST_REPORT.pt-BR.md) para os tempos do E2E.

## Limitações

- **Backend compartilhado por app**: a `LAUNCH_URL` estática força um contêiner
  por app; sessões simultâneas compartilham o serviço. A contagem de referência
  remove apenas quando a última sessão termina.
- **Corrida de inicialização**: a página de espera resolve a primeira impressão
  de “site não encontrado”, mas o app pode continuar inicializando após o
  redirecionamento. Em apps HTTPS com certificado autoassinado, o polling pode
  falhar; a página oferece “Abrir agora” após 60 s.
- **420 de 464 templates type 1**: 17 privileged, 9 host-network, 16 sem porta
  TCP e 2 Windows são pulados. Stacks/Swarm ficam fora.
- **Sem isolamento de dados por usuário**; é um catálogo de demonstração/teste.
- **CA interna**: `KASM_VERIFY_TLS=false` é necessário nesta instância; prefira
  instalar a CA em produção.
- A API key do orquestrador precisa de `Sessions View`/`Images View`; ela não
  cria sessões. O E2E foi validado clicando na UI do Kasm.

## Documentação

- [Arquitetura](docs/ARCHITECTURE.pt-BR.md) · [Architecture (EN)](docs/ARCHITECTURE.md)
- [Deploy](docs/DEPLOY.pt-BR.md) · [Deploy (EN)](docs/DEPLOY.md)
- [Relatório de testes](docs/TEST_REPORT.pt-BR.md) · [Test report (EN)](docs/TEST_REPORT.md)
- [Notas de discovery](docs/discovery-notes.md) (PT-BR, específicas do ambiente)

## Atribuição

Templates e logos pertencem aos autores originais
([lissy93/portainer-templates](https://github.com/lissy93/portainer-templates)
e mantenedores upstream). Os metadados do registry, a página de espera e o
orquestrador são MIT.
