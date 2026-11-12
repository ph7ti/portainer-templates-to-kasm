# Kasm Workspaces Registry (schema 1.1)

Snapshot gerado do registry. O registry **público** consumido pelo Kasm fica no
repositório dedicado:

- https://github.com/ph7ti/kasm-registry
- https://ph7ti.github.io/kasm-registry/

## Estrutura

```
registry/
├── versions.txt
├── list.json
├── index.html
├── backend_manifest.json   # consumido pelo orquestrador
└── 1.1/
    ├── list.json
    ├── versions.json
    ├── icons/
    └── workspaces/<slug>/
        ├── workspace.json
        └── <slug>.<ext>
```

Cada `LAUNCH_URL` abre a página de espera `kasm-wait`:

```
--kiosk http://kasm-wait:8080/wait?target=http%3A%2F%2Fnextcloud%3A80
```

A página consulta o backend a cada 2 s e redireciona quando ele responde.

## Uso

1. Adicione `https://ph7ti.github.io/kasm-registry/` em
   **Admin → Workspaces → Registry → Add new** (sem `/1.1`).
2. Instale as imagens lançáveis com o botão **Add** de cada item ou:
   `python tools/publish_images_to_kasm.py --update`.
3. Suba o contêiner `kasm-wait` na rede dos workspaces.
4. Suba o orquestrador no host Docker.

## Regenerar

```bash
python converter/convert_templates.py --output registry --refresh --prune
```

O conversor é idempotente e mantém `backend_manifest.json` em sincronia.
