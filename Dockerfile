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
# readable by the tunerbox user whatever permissions the files had where the image was built from
# (a folder copied from Windows can arrive as owner-only, and then nothing starts)
RUN chmod -R a+rX /app

# Settings, database, logs, recordings and the lineup CSV all live in /data
ENV TUNERBOX_DATA=/data \
    PYTHONUNBUFFERED=1 \
    TUNERBOX_TRUST_LAN=1
# TUNERBOX_TRUST_LAN: a NAS has no screen, so browsers on the home network can set it up and pair
# phones (as "this PC" can on a desktop install) until Setup › Phones › lock is turned on
RUN useradd --uid 1000 --create-home tunerbox \
    && mkdir /data && chown tunerbox /data
USER tunerbox
VOLUME /data
EXPOSE 8642

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8642/api/me', timeout=4).status_code == 200 else 1)"

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["python", "-m", "tunerbox", "--server"]
