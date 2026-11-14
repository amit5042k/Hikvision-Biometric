# QR Generator server + website. Build from the qr-generator/ folder:
#   docker build -t qr-generator qr-generator
#   docker run -p 8000:8000 -v qrdata:/data -e ADMIN_EMAIL=you@example.com -e ADMIN_PASSWORD=... qr-generator
FROM python:3.12-slim

WORKDIR /app
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
COPY server server
COPY web web

# The SQLite database and secret key live here; mount a volume so they survive redeploys.
ENV QR_DATA_DIR=/data \
    BEHIND_PROXY=1 \
    PORT=8000
VOLUME /data
EXPOSE 8000

WORKDIR /app/server
CMD ["python", "run.py"]
