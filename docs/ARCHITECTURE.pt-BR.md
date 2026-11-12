[English](ARCHITECTURE.md)

# Arquitetura e decisões

## Fluxo de uma sessão

1. O usuário clica em **Teste: Nextcloud** no dashboard do Kasm.
2. O Kasm cria a sessão do registry 1.1 com `kasmweb/chrome:1.19.0`. O
   `run_config.environment.LAUNCH_URL` aponta para a página de espera:
   `--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80`.
3. O Chrome sobe na rede dos workspaces e abre o `kasm-wait`.
4. A página consulta `http://nextcloud:80` a cada 2 s e redireciona quando o
   backend responde. Enquanto isso, mostra “Preparando seu aplicativo…”.
5. Em paralelo, o orquestrador consulta `/api/public/get_kasms`. Ao casar a
   sessão com um app do manifesto:
   - descobre a rede (`docker_network` da API ou `docker inspect` do
     `container_id` do navegador);
   - sobe o backend com `name=<slug>`, `network=<rede>`, labels
     `kasm-teste.*`, `restart_policy=no`.
6. Ao fechar a aba ou expirar, o Kasm remove a sessão do navegador. No próximo
   poll o orquestrador remove o backend (`stop(timeout=5)` +
   `remove(force=True, v=True)`) e os volumes nomeados.

## Por que orquestração externa

O Kasm não cria múltiplos contêineres por sessão. O workspace é um contêiner (o
navegador); a aplicação precisa de um segundo. Um processo externo com acesso ao
socket Docker gerencia esse segundo contêiner. Rebuildar a imagem do navegador
com o app embutido foi rejeitado (fora de escopo).

## Página de espera

`waiter/wait_server.py` é um servidor HTTP Python sem dependências:

- `GET /wait?target=http://app:porta` → página de espera;
- `GET /healthz` → `ok`;
- valida o alvo (apenas `http(s)://host[:porta]`), consulta com
  `fetch(target, {mode:"no-cors", cache:"no-store"})` a cada 2 s e redireciona
  quando recebe resposta;
- após 30 tentativas (60 s) mostra o link “Abrir agora”;
- roda como contêiner `kasm-wait` na `kasm_default_network` para que o navegador
  resolva o nome. Com redes efêmeras seria necessário anexar o waiter a cada
  rede de sessão; o deploy validado usa rede fixa.

Limitações: alvos HTTPS com certificado autoassinado podem rejeitar o fetch de
polling; o link de fallback cobre esse caso. A página testa alcance de rede, não
readiness da aplicação.

## API do Kasm

A build instalada (1.19.0.842dcf) expõe a **Developer API legacy**:

- `POST /api/public/get_kasms` com `{api_key, api_key_secret}` no corpo;
- `POST /api/public/get_images` idem;
- `POST /api/public/create_registry`, `get_registries`, `delete_registry`;
- `POST /api/public/create_image`, `update_image`, `get_images`.

A documentação mais nova descreve `GET /api/session/sessions` com Basic Auth,
mas essa rota retorna **404** nesta build. O cliente detecta o modo e normaliza
os dois formatos. Force com `KASM_API_MODE=legacy|v2`.

Ordem de casamento:

1. `image_id` da sessão contra o mapa `friendly_name → image_id` de
   `get_images` (sobrevive a renomear o workspace no Kasm);
2. fallback: `image.friendly_name` normalizado.

O índice é opcional: sem `Images View` o cliente loga aviso e usa só o nome.

## Rede

- **Rede fixa** (este deploy): `auto_generate_kasm_docker_networks: false`;
  workspaces e `kasm-wait` usam `kasm_default_network`, bridge user-defined com
  DNS interno. O backend entra nela e o nome do contêiner vira DNS.
- **Rede efêmera**: o orquestrador lê `NetworkSettings.Networks` do contêiner do
  navegador e usa essa rede. A remoção do backend ocorre antes de o Kasm apagar
  a rede; o poll de 5 s mantém a janela pequena.
- A página de espera precisa estar na mesma rede.

## Um backend por app

A `LAUNCH_URL` estática força um nome previsível, então um contêiner é
compartilhado por sessões simultâneas do mesmo app. O orquestrador mantém um
conjunto de `session_id` por slug (contagem de referência): sobe na primeira e
remove quando a última sai. Sessões simultâneas leem/escrevem o mesmo serviço —
aceitável para demonstração, não para isolamento real. Isolamento por usuário
exigiria um proxy/roteador por sessão, fora do escopo do registry 1.1.

## Estado e recuperação

- Fonte de verdade: API do Kasm + labels Docker `kasm-teste.managed`,
  `kasm-teste.app`, `kasm-teste.image`.
- `state.json` é apenas observabilidade.
- Na primeira reconciliação o orquestrador limpa contêineres gerenciados sem
  sessão ativa (órfãos de reinício).
- Erro de API **não** remove backends; o loop loga e mantém.
- Falha ao subir entra em backoff (`RETRY_INTERVAL`, padrão 30 s).
- Conflito de nome com contêiner não gerenciado é recusado; nunca removemos
  contêiner que não criamos.

## Porta e esquema

O Portainer não indica qual porta é a UI web. O conversor:

1. parseia `host:container/proto`, `container/proto` e `container`;
2. considera só TCP;
3. prioriza 443, 80, 8443, 8080, 3000, 3001, 8000, 8096, 8123, 8181, 9000,
   9001, 9090, 8081, 8888; senão a primeira TCP;
4. aplica `converter/port_overrides.json` (`minio → 9001`,
   `adguard/adguardhome → 3000`, `nextcloud → 80`);
5. usa `https` em 443/8443/9443 (com `--ignore-certificate-errors`) e `http`
   nas demais.

A porta só aparece na URL do navegador; o backend não publica portas no host.

## Volumes

Binds do Portainer apontam para caminhos do host; o orquestrador cria volumes
nomeados `kasm-teste-vol-<slug>-<n>` e os remove com o contêiner. Binds de
sistema (`/etc/timezone`, `/etc/localtime`, `/var/run/docker.sock`) são
descartados na conversão. Dados não persistem entre sessões.

## Distribuição do registry

- `ph7ti/portainer-templates-to-kasm`: conversor, orquestrador, waiter, testes e
  snapshot `registry/`.
- `ph7ti/kasm-registry`: **registry público de terceiros** (só o artefato
  estático `site/`). A URL canônica é
  `https://ph7ti.github.io/kasm-registry/`; o usuário não precisa converter as
  fontes.

## Corrida de inicialização e readiness

A página de espera elimina a primeira impressão de “site não encontrado”, mas o
app pode continuar inicializando após o redirect (ou um app TLS pode não
responder ao probe no-cors). Mitigações: poll de 2 s, fallback de 60 s, imagens
pré-baixadas e `notes` em cada workspace. Um gate real de readiness
(proxy/página com health check) é fase futura; rebuildar a imagem do navegador
está explicitamente fora de escopo.
