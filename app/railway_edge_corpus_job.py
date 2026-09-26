from __future__ import annotations

import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .edge_corpus_runner import main


def _serve_health() -> None:
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=_serve_health, daemon=True).start()
    sys.argv = [
        sys.argv[0],
        "--manifest",
        "app/edge-corpus-v1.json",
        "--output",
        "/tmp/edge-corpus-report.json",
    ]
    main()
