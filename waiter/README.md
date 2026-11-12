# kasm-wait — waiting page

A dependency-free HTTP service that shows “Preparando seu aplicativo…” while the
application container starts, polls it every 2 seconds and redirects when it is
reachable.

## Run

```bash
docker build -t kasm-wait:local -f waiter/Dockerfile .
docker run -d --name kasm-wait --restart unless-stopped \
  --network kasm_default_network kasm-wait:local
```

It must be attached to the same Docker network as the Kasm browser workspaces.
The generated workspace URL is:

```
--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80
```

Endpoints:

- `GET /wait?target=http://host:port` — HTML waiting page.
- `GET /healthz` — `ok`.
- `GET /` — short usage page.

Only `http(s)://host[:port]` targets are accepted; other schemes and malformed
hosts are rejected with `400`.

`WAITER_PORT` (default `8080`).

## Português

Serviço HTTP sem dependências que mostra “Preparando seu aplicativo…” enquanto
o contêiner da aplicação inicia, verifica o alvo a cada 2 segundos e redireciona
quando ele responde. Rode como contêiner `kasm-wait` na mesma rede Docker dos
workspaces do Kasm. A URL gerada é
`http://kasm-wait:8080/wait?target=<url-codificada>`; `WAITER_PORT` padrão
`8080`.
