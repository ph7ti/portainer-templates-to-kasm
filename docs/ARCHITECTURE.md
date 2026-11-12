# Arquitetura e decisões

## Fluxo de uma sessão

1. O usuário clica em **Teste: Nextcloud** no dashboard do Kasm.
2. O Kasm cria a sessão do workspace registry 1.1. A imagem é
   `kasmweb/chrome:1.19.0` com `run_config.environment.LAUNCH_URL =
   "--kiosk http://nextcloud:80"` (override: o LinuxServer também escuta 80;
   a 443 usaria TLS autoassinado).
3. O navegador sobe na rede Docker do agente (`kasm_default_network`) e tenta
   abrir `http://nextcloud:80`. Ainda pode não haver backend — ver “corrida de
   inicialização”.
4. O orquestrador faz poll em `/api/public/get_kasms` (API legacy desta build).
   Quando vê a sessão cujo `image.friendly_name` (ou `image_id`) corresponde a um
   app do manifesto, executa:
   - descoberta da rede: usa `docker_network` da API se existir; senão
     inspeciona o `container_id` do navegador e escolhe a rede `kasm*`;
   - `docker run` do backend com `name=<slug>`, `network=<rede>`,
     `restart_policy=no`, labels `kasm-teste.*`.
5. O Kasm encaminha o stream do Chrome; o Chrome acessa o backend pelo DNS
   interno do Docker.
6. Ao fechar a aba/expirar, o Kasm remove a sessão do navegador (política de
   grupo `keepalive_expiration_action=delete`). No próximo poll o orquestrador
   não vê mais a sessão e remove o backend (`stop(timeout=5)` +
   `remove(force=True, v=True)`) e os volumes nomeados.

## Por que orquestração externa

O Kasm **não** cria múltiplos contêineres por sessão. O workspace é um único
contêiner (o navegador). O serviço da aplicação precisa de um segundo contêiner;
o lugar natural para gerenciá-lo é um processo com acesso ao socket Docker do
agente. A alternativa (embutir o backend na imagem do navegador) exigiria
rebuild de imagens, rejeitada.

## API do Kasm

A build instalada (1.19.0.842dcf) expõe a **Developer API legacy**:

- `POST /api/public/get_kasms` com `{api_key, api_key_secret}` no corpo;
- `POST /api/public/get_images` idem;
- respostas `{kasms: [...]}` e `{images: [...]}`.

A documentação 1.19 mais nova descreve `GET /api/session/sessions` com HTTP
Basic, mas essa rota responde **404** nesta build. O cliente detecta
automaticamente e normaliza os dois formatos para `Session`. O modo pode ser
forçado com `KASM_API_MODE=legacy|v2`.

Campos usados para casar sessão ↔ app:

1. `image_id` da sessão, comparado ao mapa `friendly_name → image_id` obtido de
   `get_images` (mais robusto; sobrevive a renomear o workspace no Kasm);
2. fallback: `image.friendly_name` normalizado (`casefold`, espaços
   colapsados).

O índice de imagens é opcional: se a chave não tiver `Images View`, o cliente
loga aviso e usa apenas o nome.

## Rede

- **Rede fixa** (esta instalação): `auto_generate_kasm_docker_networks: false`;
  os workspaces usam `kasm_default_network` (bridge user-defined, DNS interno).
  O backend entra nela e o nome do contêiner vira DNS.
- **Rede efêmera por sessão**: o Kasm cria uma rede por sessão. O orquestrador
  lê `NetworkSettings.Networks` do contêiner do navegador via `container_id` e
  usa essa rede. A API 1.19 nova também expõe `docker_network`, usado quando
  presente.
- O backend é removido **antes** de o Kasm apagar a rede efêmera; como o poll
  é de 5 s, a janela de corrida é pequena. Se a rede for removida enquanto o
  backend estiver anexado, o Docker impede a remoção; o Kasm pode registrar
  erro de limpeza. Monitore `docker network ls` em testes com rede efêmera.

## Backend único por app

O `LAUNCH_URL` é estático, então o nome do backend precisa ser previsível. Isso
força **um contêiner por app**, compartilhado por sessões simultâneas. O
orquestrador mantém um conjunto de `session_id` por slug (contagem de
referência): sobe na primeira e remove quando a última sai. Duas sessões do
mesmo app leem/escrevem no mesmo serviço — aceitável para demonstração, não para
isolamento real. Para isolamento por usuário seria necessário um proxy/roteador
que mapeasse sessão → backend dinâmico, fora do escopo do registry 1.1.

## Estado e recuperação

- A fonte de verdade é a API do Kasm + os labels Docker `kasm-teste.managed`,
  `kasm-teste.app`, `kasm-teste.image`.
- `state.json` é apenas observabilidade (sessões ativas, redes, timestamp).
- Na primeira reconciliação bem-sucedida o orquestrador limpa contêineres
  gerenciados que não correspondem a sessões ativas (órfãos de reinício).
- Falha de API **não** remove backends: o loop loga o erro e mantém o estado.
- Falha ao subir um backend entra em backoff (`RETRY_INTERVAL`, padrão 30 s),
  sem derrubar o loop.
- Conflito de nome com contêiner não gerenciado é recusado (nunca removemos um
  contêiner que não criamos).

## Mapeamento de portas

O Portainer não diz qual porta é a UI web. O conversor:

1. parseia `host:container/proto`, `container/proto` e `container`;
2. considera apenas TCP;
3. escolhe, por prioridade: 443, 80, 8443, 8080, 3000, 3001, 8000, 8096,
   8123, 8181, 9000, 9001, 9090, 8081, 8888; senão a primeira TCP;
4. aplica `converter/port_overrides.json` para exceções conhecidas
   (`minio → 9001`, `adguard/adguardhome → 3000`, `nextcloud → 80`);
5. define o esquema: `https` (com `--ignore-certificate-errors`) para
   443/8443/9443; `http` para o restante.

A porta é usada apenas na URL do navegador; o backend **não publica portas no
host**.

## Volumes

Binds do Portainer apontam para caminhos do host (ex.:
`/srv/lsio/nextcloud/config`). Em vez de criar diretórios no host, o
orquestrador cria **volumes nomeados** `kasm-teste-vol-<slug>-<n>` e os remove
junto com o contêiner. Binds de sistema (`/etc/timezone`, `/etc/localtime`,
`/var/run/docker.sock`) são descartados na conversão. Dados não persistem entre
sessões.

## Corrida de inicialização

O Chrome pode abrir antes de o backend aceitar conexões. Mitigações aplicadas:

- poll curto (5 s) e criação imediata ao detectar a sessão;
- `notes` do workspace orienta recarregar;
- recomendação de pré-pull das imagens dos apps de teste;
- `PULL_IMAGES=true` evita falha por imagem ausente (mas o primeiro pull pode
  levar minutos; faça pré-pull em produção).

Um gate de readiness exigiria uma página intermediária servida pelo
orquestrador ou um proxy por app — deliberadamente fora do escopo desta fase.
