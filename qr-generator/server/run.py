"""Start the QR Generator server.

    python run.py                 # http://0.0.0.0:8000
    HOST=127.0.0.1 PORT=9000 python run.py
"""
import logging
import os

from qrserver import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    from waitress import serve
    logging.info("Serving on http://%s:%s", host, port)
    # Behind a proxy, keep X-Forwarded-* headers so ProxyFix (see create_app) can build https:// short links;
    # waitress would otherwise drop them.
    behind_proxy = os.environ.get("BEHIND_PROXY", "").lower() in ("1", "true", "yes")
    serve(app, host=host, port=port, threads=8, clear_untrusted_proxy_headers=not behind_proxy)
