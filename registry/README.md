# Kasm Workspaces Registry (schema 1.1)

Este diretório é o registry estático consumido pelo Kasm Workspaces.

## Estrutura

```
registry/
├── versions.txt           # "1.1" — schema que o Kasm entende
├── list.json              # cópia de 1.1/list.json (compatibilidade)
├── index.html             # redireciona humanos para 1.1/
├── backend_manifest.json  # consumido pelo orquestrador (não pelo Kasm)
└── 1.1/
    ├── list.json          # catálogo (workspaces + sha)
    ├── versions.json      # versões de Kasm presentes (1.19.x)
    ├── icons/             # ícones referenciados por image_src
    └── workspaces/<slug>/
        ├── workspace.json
        └── <slug>.<ext>
```

O `list.json` segue o formato gerado pelo template oficial
`kasmtech/workspaces_registry_template`: cada entrada é o `workspace.json`
acrescido de `sha`.

## URLs

- GitHub Pages: `https://ph7ti.github.io/portainer-templates-to-kasm/`
- Raw (fallback HTTPS):
  `https://raw.githubusercontent.com/ph7ti/portainer-templates-to-kasm/main/registry/`

Adicione a URL **sem `/1.1`** em Admin → Workspaces → Registry → Add new.

## Regenerar

```bash
python converter/convert_templates.py --output registry --refresh --prune
```

O conversor é idempotente e mantém `backend_manifest.json` em sincronia com o
catálogo.
