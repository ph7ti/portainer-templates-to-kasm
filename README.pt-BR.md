# Portainer → Kasm Workspaces (fork de origem)

Este repositório é um **fork do
[lissy93/portainer-templates](https://github.com/lissy93/portainer-templates)**
que mantém o catálogo original de templates do Portainer e adiciona as
ferramentas para publicar as mesmas aplicações como **Protected Web Apps do
Kasm Workspaces**.

Ele é o **repositório de origem e operação**, não o registry que o Kasm consome.
O registry público é publicado separadamente em
[ph7ti/kasm-registry](https://github.com/ph7ti/kasm-registry):

```
URL do registry no Kasm: https://ph7ti.github.io/kasm-registry/
```

[English](README.md) · [Registry público](https://github.com/ph7ti/kasm-registry) · [URL do registry](https://ph7ti.github.io/kasm-registry/)

## Por que este fork existe

O projeto upstream é um catálogo/site **do Portainer**. O papel dele é fornecer
`templates.json`, dados de origem e scripts de geração para usuários do
Portainer. Artefatos específicos de Kasm (entradas de registry schema 1.1,
fábrica de workspaces Chrome, página de espera, orquestrador Docker) não
pertencem ao catálogo upstream.

O fork existe para:

1. **Preservar o modelo e o fluxo de atualização upstream.** Mantemos
   `templates.json`, `Schema.json`, `sources/` e `lib/` para conseguir puxar
   mudanças do upstream e regenerar de forma determinística.
2. **Adicionar transformação só de Kasm sem alterar o upstream.** O conversor lê
   o `templates.json` do fork e gera entradas registry 1.1; o upstream continua
   focado no Portainer.
3. **Separar o registry gerado da origem.** O repositório público
   `ph7ti/kasm-registry` contém apenas o artefato estático; o usuário final
   adiciona essa URL e não precisa clonar nem converter este fork.
4. **Ser dono das peças de runtime.** O orquestrador e a página de espera são
   operados pelo administrador do Kasm, não pelo projeto upstream.

Não trate este repositório como o catálogo do Kasm. Use-o para regenerar ou
operar; use `ph7ti/kasm-registry` para consumir.

## Três artefatos

| Artefato | Papel | Quem usa |
|---|---|---|
| [lissy93/portainer-templates](https://github.com/lissy93/portainer-templates) | Templates, fontes e site do Portainer | Usuários de Portainer, mantenedores upstream |
| **este fork** (`ph7ti/portainer-templates-to-kasm`) | Dados upstream + conversor, waiter, orquestrador, testes, docs e snapshot `registry/` | Operadores e contribuidores |
| [ph7ti/kasm-registry](https://github.com/ph7ti/kasm-registry) | Registry Kasm estático público (Pages) | Administradores/usuários do Kasm |

O `templates.json` do fork é a entrada; o `site/1.1/list.json` do registry
dedicado é a saída que o Kasm consome.

## O que foi adicionado ao fork

| Caminho | Finalidade |
|---|---|
| `converter/` | `templates.json` do Portainer → registry Kasm 1.1; schema, ícones, overrides de porta, `backend_manifest.json` |
| `waiter/` | `kasm-wait`: página “Preparando seu aplicativo…”, polling a cada 2 s e redirect |
| `orchestrator/` | Consulta a API do Kasm e cria/remove os contêineres na rede Docker |
| `registry/` | Snapshot gerado para testes/dev; a cópia pública fica em `ph7ti/kasm-registry` |
| `tools/` | `publish_images_to_kasm.py`, `measure_cleanup.py`, smoke test de backend |
| `tests/` | 50 testes unitários/integrados com mocks de Kasm e Docker |
| `docs/` | Arquitetura, deploy e relatório de testes em inglês e português |
| `systemd/` | Unit de exemplo do orquestrador |

## Como usar os novos recursos

### A. Como administrador/usuário do Kasm (sem conversão)

1. Adicione o registry público no Kasm:
   **Admin → Workspaces → Registry → Add new** →
   `https://ph7ti.github.io/kasm-registry/` (sem `/1.1`).
2. Instale as imagens dos workspaces que deseja (a importação do registry cria
   os metadados; esta build do Kasm não cria as imagens lançáveis sozinha):

   ```bash
   python tools/publish_images_to_kasm.py --update
   ```

   Rode a partir de um clone deste fork, ou use o botão **Add** de cada item no
   UI do registry.
3. O operador precisa manter em execução:
   - `kasm-wait` na rede Docker dos workspaces;
   - o orquestrador no host Docker.
4. Clique em um workspace `Teste: …`. O navegador mostra a página de espera;
   quando o backend responde, ele redireciona automaticamente.

Você **não** precisa clonar este fork só para usar o registry, mas precisa do
waiter e do orquestrador rodando.

### B. Como operador (regenerar e implantar)

```bash
git clone git@github.com:ph7ti/portainer-templates-to-kasm.git
cd portainer-templates-to-kasm

python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 1. converter o templates.json atual -> snapshot do registry
python converter/convert_templates.py --output registry --refresh --prune

# 2. rodar os testes
python -m pytest -q

# 3. implantar a página de espera (mesma rede dos workspaces)
docker build -t kasm-wait:local -f waiter/Dockerfile .
docker run -d --name kasm-wait --restart unless-stopped \
  --network kasm_default_network kasm-wait:local

# 4. implantar o orquestrador
cp .env.example .env   # KASM_URL, API key/secret
docker compose up -d --build
# ou use systemd/kasm-orchestrator.service
```

Publique o registry gerado em `ph7ti/kasm-registry`:

```bash
python converter/convert_templates.py --output /caminho/kasm-registry/site \
  --refresh --prune
cd /caminho/kasm-registry
git add -A && git commit -m "Refresh registry" && git push
```

Veja [docs/DEPLOY.pt-BR.md](docs/DEPLOY.pt-BR.md) para pré-requisitos, CA/TLS,
instalação de imagens e políticas de grupo do Kasm.

### C. Como contribuidor

- Mantenha mudanças de dados/modelo upstream separadas da saída Kasm.
- Regenere `registry/` e atualize o registry dedicado quando `templates.json`
  mudar.
- Rode `pytest -q` (50 testes).
- Veja [docs/ARCHITECTURE.pt-BR.md](docs/ARCHITECTURE.pt-BR.md) para as decisões.

## Como o runtime funciona

```
Usuário → workspace Kasm (Chrome kiosk) → página kasm-wait
                                        │ poll do alvo a cada 2 s
                                        ▼
                           contêiner da app (nome = slug do workspace)
                                        ▲
API do Kasm ── sessões ── orquestrador ─┘ cria/remove em kasm_default_network
```

A página de espera resolve a corrida do primeiro clique: o Kasm inicia o
navegador antes de o contêiner da aplicação existir. A página consulta o backend
e redireciona quando ele responde; se demorar mais de 60 s, mostra o link
“Abrir agora”. O orquestrador cuida do ciclo de vida do backend.

### Portas e overrides de HTTPS

O conversor usa HTTPS nas portas 443/8443/9443 (com
`--ignore-certificate-errors` para certificados autoassinados) e HTTP nas
demais. Alguns apps expõem uma **segunda porta TLS não padrão**. O Duckstation é
o caso concreto:

- `http://duckstation:3000` → HTTP 200
- `https://duckstation:3001` → HTTPS 200 (autoassinado)
- `https://duckstation:3000` → **ERR_SSL_PROTOCOL_ERROR** (porta HTTP)

As exceções ficam em
[`converter/port_overrides.json`](converter/port_overrides.json) (porta) e
[`converter/scheme_overrides.json`](converter/scheme_overrides.json) (esquema).
O Duckstation está mapeado para `3001` + `https`; a página redireciona para
`https://duckstation:3001` com a flag de certificado. Para outro app, mapeie o
slug para a porta TLS e para `"https"`.

## Fork vs sincronização upstream

- Os arquivos upstream (`templates.json`, `Schema.json`, `sources/`, `lib/`)
  continuam sendo a fonte de verdade dos templates Portainer.
- As adições Kasm ficam em diretórios novos e não alteram o código upstream.
- Para atualizar: faça pull/merge do upstream, rode o conversor, commit do
  snapshot e publique em `ph7ti/kasm-registry`.
- O `registry/` deste fork é snapshot de desenvolvimento; a URL pública é o
  repositório dedicado.

## Estado atual

Validado no **Kasm Workspaces 1.19.0.842dcf**, Docker 29.9.3, Debian 13,
`kasm_default_network` (rede fixa).

| Item | Situação |
|---|---|
| Conversão | 420 workspaces de 464 templates type 1; 0 erros de schema |
| Pulados | 44: 17 privileged, 9 host-network, 16 sem porta TCP, 2 Windows |
| Registry | 420 workspaces, schema 1.1, Pages público no ar |
| Imagens no Kasm | 420 entradas `Teste: …` instaladas |
| Página de espera | validada: mensagem → polling → redirect automático |
| Testes | 50 passando no Windows e Debian 13 |
| E2E | Nextcloud, Gitea e Uptime Kuma carregaram; backends removidos 2,8–8,5 s após fechar |
| Órfãos | nenhum após os testes; sem volumes residuais |

## Limitações

- Um backend por app (compartilhado por sessões simultâneas; refcount), pois a
  `LAUNCH_URL` é estática.
- Sem rebuild das imagens dos apps. A página de espera é um contêiner separado.
- `type 3` (stacks) e Swarm ainda não são convertidos.
- Apps em portas HTTPS usam `--ignore-certificate-errors` para certificados
  autoassinados; o probe da página pode não detectá-los e cai no link
  “Abrir agora”.
- O waiter precisa estar na mesma rede Docker dos workspaces. O deploy validado
  usa rede fixa.
- Sem isolamento de dados por usuário; é um catálogo de demonstração/teste.
- A API key do orquestrador precisa de `Sessions View`/`Images View`; ela não
  cria sessões.

## Documentação

- [Arquitetura](docs/ARCHITECTURE.pt-BR.md) · [Architecture](docs/ARCHITECTURE.md)
- [Deploy](docs/DEPLOY.pt-BR.md) · [Deploy EN](docs/DEPLOY.md)
- [Relatório de testes](docs/TEST_REPORT.pt-BR.md) · [Test report](docs/TEST_REPORT.md)
- [Página de espera](waiter/README.md)
- Registry público: [ph7ti/kasm-registry](https://github.com/ph7ti/kasm-registry)

## Créditos e licença

Fork de
[lissy93/portainer-templates](https://github.com/lissy93/portainer-templates);
templates e logos pertencem aos autores originais. A conversão Kasm, o
orquestrador e a página de espera são MIT, seguindo a licença upstream.
