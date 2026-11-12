# Exemplos de `workspace.json`

Três entradas geradas pelo conversor a partir de templates reais:

- `nextcloud/` — `linuxserver/nextcloud:latest`, porta 443
- `gitea/` — `gitea/gitea:latest`, porta 3000
- `uptime-kuma/` — `louislam/uptime-kuma:latest`, porta 3001

`backend_manifest.snippet.json` mostra as entradas correspondentes do
`backend_manifest.json` usadas pelo orquestrador.

Reproduza com:

```bash
python converter/convert_templates.py --output registry --refresh
python -c "import json;d=json.load(open('registry/1.1/list.json'));print(d['workspacecount'])"
```
