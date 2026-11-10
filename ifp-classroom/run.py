"""Start the IFP Classroom Attendance server.

    python run.py                       # http://0.0.0.0:8000
    HOST=127.0.0.1 PORT=9000 python run.py
"""
import logging
import os

from classroom_app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    try:
        from waitress import serve
        logging.info("Serving on http://%s:%s", host, port)
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, debug=False, use_reloader=False)
