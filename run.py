"""Start the attendance server with the background device sync.

    python run.py                 # http://0.0.0.0:5000
    HOST=127.0.0.1 PORT=8080 python run.py
"""
import logging
import os

from app import create_app
from app.sync import SyncScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    SyncScheduler(app.config["DATABASE"]).start()
    try:
        from waitress import serve
        logging.info("Serving on http://%s:%s", host, port)
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, debug=False, use_reloader=False)
