"""Local cable lineup: channels.csv + Gracenote station details."""
import csv
import logging

from . import config, db

log = logging.getLogger("tunerbox.lineup")


def load_csv():
    path = config.data_path(config.get("lineup_csv"))
    made_for = config.get("lineup_csv_lineup") or ""
    picked = (config.get("gracenote") or {}).get("lineup_id") or ""
    if made_for and picked and made_for != picked:
        log.info("Skipping %s: it's for lineup %s, you picked %s", path.name, made_for, picked)
        return 0
    if not path.exists():
        log.debug("No lineup CSV at %s (optional)", path)
        return 0
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if len(r) < 2:
                continue
            num, name = r[0].strip(), ",".join(r[1:]).strip().strip('"')
            if not num.isdigit():
                continue  # header
            rows.append((int(num), name))
    with db.conn() as c:
        for num, name in rows:
            c.execute("""INSERT INTO lineup(number, name) VALUES (?, ?)
                         ON CONFLICT(number) DO UPDATE SET name=CASE WHEN excluded.name GLOB '[0-9]*'
                           AND lineup.callsign != '' THEN lineup.name ELSE excluded.name END""", (num, name))
    log.info("Loaded %d lineup channels from %s", len(rows), path.name)
    return len(rows)


def apply_gracenote(channels):
    """channels: list of gracenote channel dicts (callSign, channelNo, affiliateName, ...)."""
    with db.conn() as c:
        for ch in channels:
            try:
                num = int(float(ch["channelNo"]))
            except (KeyError, ValueError):
                continue
            logo = ch.get("thumbnail") or ""
            if logo.startswith("//"):
                logo = "https:" + logo
            aff = ch.get("affiliateName") or ""
            if aff.lower() == "null":
                aff = ""
            c.execute("""INSERT INTO lineup(number, name, callsign, affiliate, station_id, logo)
                         VALUES (?, ?, ?, ?, ?, ?)
                         ON CONFLICT(number) DO UPDATE SET callsign=excluded.callsign,
                           name=CASE WHEN lineup.name GLOB '[0-9]*' THEN excluded.name ELSE lineup.name END,
                           affiliate=excluded.affiliate, station_id=excluded.station_id, logo=excluded.logo""",
                      (num, ch.get("callSign") or str(num), ch.get("callSign") or "", aff,
                       str(ch.get("channelId") or ""), logo))


def all_channels():
    return db.rows("SELECT * FROM lineup ORDER BY number")
