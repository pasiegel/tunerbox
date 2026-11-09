# TunerBox server image (no desktop window). Build:  docker build -t tunerbox .
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.txt stays the single source of truth; the desktop-window package is dropped here.
COPY requirements.txt constraints.txt ./
RUN grep -vi pywebview requirements.txt > /tmp/requirements-server.txt \
    && pip install --no-cache-dir -r /tmp/requirements-server.txt -c constraints.txt

COPY tunerbox/ tunerbox/
COPY web/ web/
COPY data/channels.csv data/channels.csv
COPY docker-entrypoint.sh .

# Settings, database, logs, recordings and the lineup CSV all live in /data
ENV TUNERBOX_DATA=/data \
    PYTHONUNBUFFERED=1
RUN useradd --uid 1000 --create-home tunerbox \
    && mkdir /data && chown tunerbox /data
USER tunerbox
VOLUME /data
EXPOSE 8642

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8642/api/me', timeout=4).status_code == 200 else 1)"

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["python", "-m", "tunerbox", "--server"]
