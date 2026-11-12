"""Tiny waiting-page server for Kasm browser workspaces.

The Kasm Chrome workspace opens ``http://kasm-wait:8080/wait?target=...``.
The page polls the target (the application container that the orchestrator is
starting) and redirects as soon as it answers. It is intentionally dependency
free so it can run in a small container on the Kasm Docker network.

Routes:
    GET /wait?target=http://app:port  -> waiting page
    GET /healthz                      -> "ok"
    GET /                             -> short help page
"""

from __future__ import annotations

import html
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("WAITER_PORT", "8080"))
TARGET_RE = re.compile(
    r"^https?://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?/?$"
)

PAGE = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Preparando o aplicativo…</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      background: radial-gradient(circle at top, #0f2537 0%, #08111c 55%, #05080d 100%);
      color: #e2e8f0; font: 16px/1.5 ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
    }}
    .card {{
      width: min(680px, calc(100vw - 40px)); padding: 40px 36px; border-radius: 22px;
      background: rgba(15, 23, 42, .78); border: 1px solid rgba(148, 163, 184, .18);
      box-shadow: 0 24px 80px rgba(0, 0, 0, .45); text-align: center;
      backdrop-filter: blur(12px);
    }}
    .spinner {{
      width: 54px; height: 54px; margin: 0 auto 26px; border-radius: 50%;
      border: 4px solid rgba(56, 189, 248, .18); border-top-color: #38bdf8;
      animation: spin 1s linear infinite;
    }}
    .ok .spinner {{ border-color: rgba(34, 197, 94, .25); border-top-color: #22c55e; animation: none; }}
    h1 {{ margin: 0 0 12px; font-size: clamp(24px, 4vw, 34px); letter-spacing: -.02em; }}
    p {{ margin: 8px 0; color: #94a3b8; }}
    .target {{ margin-top: 20px; padding: 10px 14px; border-radius: 10px;
      background: #0b1622; color: #7dd3fc; font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
      overflow-wrap: anywhere; }}
    .status {{ margin-top: 18px; font-size: 14px; color: #cbd5e1; min-height: 22px; }}
    .dots::after {{ content: ''; animation: dots 1.6s steps(4, end) infinite; }}
    .bar {{ height: 6px; margin-top: 22px; border-radius: 999px; background: #0b1622; overflow: hidden; }}
    .bar > i {{ display: block; width: 3%; height: 100%; background: linear-gradient(90deg,#0ea5e9,#22c55e); transition: width .4s ease; }}
    .hint {{ margin-top: 18px; font-size: 12px; color: #64748b; }}
    a {{ color: #7dd3fc; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    @keyframes dots {{ 0% {{ content: ''; }} 25% {{ content: '.'; }} 50% {{ content: '..'; }} 75% {{ content: '...'; }} }}
  </style>
</head>
<body>
  <main class="card" id="card">
    <div class="spinner"></div>
    <h1>Preparando seu aplicativo…</h1>
    <p>O contêiner está sendo criado. Aguarde, você será redirecionado automaticamente.</p>
    <p style="font-size:13px;opacity:.8">Your app container is starting. Please wait — you will be redirected automatically.</p>
    <div class="target" id="target"></div>
    <div class="bar"><i id="progress"></i></div>
    <div class="status"><span id="status">Verificando disponibilidade</span><span class="dots"></span></div>
    <div class="hint">Verificação a cada 2 segundos. Se demorar, você verá um botão para abrir mesmo assim.</div>
    <p id="fallback" style="display:none;margin-top:16px"><a id="fallback-link" href="#">Abrir agora / Open now</a></p>
  </main>
  <script>
    "use strict";
    const params = new URLSearchParams(window.location.search);
    const target = params.get("target") || "";
    const targetBox = document.getElementById("target");
    const statusEl = document.getElementById("status");
    const progress = document.getElementById("progress");
    const fallback = document.getElementById("fallback");
    const fallbackLink = document.getElementById("fallback-link");
    const card = document.getElementById("card");
    let attempts = 0;
    const maxAttempts = 30;

    if (!/^https?:\\/\\/[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\\d{{1,5}})?\\/?$/.test(target)) {{
      statusEl.textContent = "URL de destino inválida / invalid target";
      targetBox.textContent = target || "(vazio)";
      document.querySelector(".spinner").style.animation = "none";
      throw new Error("invalid target");
    }}
    targetBox.textContent = target;

    async function check() {{
      attempts += 1;
      statusEl.textContent = "Aguardando o contêiner (" + attempts + ")";
      progress.style.width = Math.min(3 + attempts * 3, 96) + "%";
      try {{
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 4000);
        await fetch(target, {{ mode: "no-cors", cache: "no-store", signal: controller.signal }});
        clearTimeout(timer);
        card.classList.add("ok");
        statusEl.textContent = "Pronto! Redirecionando…";
        progress.style.width = "100%";
        setTimeout(() => window.location.replace(target), 450);
        return;
      }} catch (err) {{
        if (attempts >= maxAttempts) {{
          statusEl.textContent = "Ainda está subindo. Use o link abaixo se preferir.";
          fallback.style.display = "block";
          fallbackLink.href = target;
          return;
        }}
        setTimeout(check, 2000);
      }}
    }}
    fallbackLink.href = target;
    check();
  </script>
</body>
</html>
"""

HELP = """<!doctype html>
<meta charset="utf-8"><title>Kasm waiter</title>
<h1>Kasm waiter</h1>
<p>Use <code>/wait?target=http://app:port</code>.</p>
<p><a href="/healthz">healthz</a></p>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "kasm-wait/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib API
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return
        if parsed.path == "/wait":
            target = (parse_qs(parsed.query).get("target") or [""])[0].strip()
            if not target or not TARGET_RE.fullmatch(target):
                self._send(400, b"invalid target", "text/plain; charset=utf-8")
                return
            body = PAGE.format(target=html.escape(target))
            self._send(200, body.encode("utf-8"))
            return
        if parsed.path in ("/", "/index.html"):
            self._send(200, HELP.encode("utf-8"))
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"kasm-wait listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
