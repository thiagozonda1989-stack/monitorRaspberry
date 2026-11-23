"""Coleta local e sincronização, em processos independentes."""
import csv
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

FIELDS = ("internal_c", "external_c", "battery_v", "mains_v")
DEVICE_PATTERN = r"[A-Za-z0-9_-]{1,64}"
BATCH = 120


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def database(path):
    db = sqlite3.connect(path, timeout=15)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    return db


def local_db(path):
    db = database(path)
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS samples (
            seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0)""")
        db.execute("CREATE INDEX IF NOT EXISTS pending ON samples(seq) WHERE sent=0")
    return db


def read_temperature(rom):
    if not re.fullmatch(r"28-[0-9a-f]{12}", rom):
        raise ValueError("ROM 1-Wire inválida")
    data = (Path("/sys/bus/w1/devices") / rom / "w1_slave").read_text().splitlines()
    if len(data) < 2 or not data[0].endswith(" YES"):
        raise ValueError("CRC 1-Wire inválido")
    value = int(data[1].split("t=")[1]) / 1000
    if not -55 <= value <= 125:
        raise ValueError("Temperatura fora da faixa")
    return value


def read_battery(config):
    from smbus import SMBus

    address = 0x48
    bus = SMBus(1)
    try:
        # AIN0/GND, ±4.096 V, single shot, 128 SPS, comparador desativado.
        bus.write_i2c_block_data(address, 1, [0xC3, 0x83])
        time.sleep(0.012)
        status = bus.read_i2c_block_data(address, 1, 2)
        if not status[0] & 0x80:
            raise ValueError("Conversão ADS1115 pendente")
        raw = int.from_bytes(bytes(bus.read_i2c_block_data(address, 0, 2)), "big", signed=True)
    finally:
        bus.close()
    pin_v = raw * 4.096 / 32768
    value = pin_v * config.get("battery_gain", 11.1) + config.get("battery_offset", 0.0)
    if not -0.03 <= pin_v <= 3.05 or not -0.3 <= value <= 30:
        raise ValueError("Bateria fora da faixa do projeto")
    return round(max(0, value), 3)


def read_mains(config):
    import minimalmodbus

    meter = minimalmodbus.Instrument(config["serial_port"], config.get("modbus_id", 1),
                                    close_port_after_each_call=True)
    meter.serial.baudrate = config.get("baud", 9600)
    meter.serial.parity = config.get("parity", "N")
    meter.serial.stopbits = config.get("stopbits", 1)
    meter.serial.timeout = 0.5
    value = meter.read_float(0, functioncode=4, number_of_registers=2)
    if not 0 <= value <= 300:
        raise ValueError("Tensão CA fora da faixa")
    return round(value, 2)


def sample(config):
    readings = {}
    errors = {}
    if config.get("demo"):
        phase = time.time() / 60
        readings = dict(zip(FIELDS, [round(base + math.sin(phase + i), 2)
                                    for i, base in enumerate((36, 30, 14, 127))]))
    else:
        sensors = {
            "internal_c": lambda: read_temperature(config["internal_rom"]),
            "external_c": lambda: read_temperature(config["external_rom"]),
            "battery_v": lambda: read_battery(config),
            "mains_v": lambda: read_mains(config),
        }
        for field, read in sensors.items():
            try:
                readings[field] = read()
            except Exception as exc:
                readings[field] = None
                errors[field] = type(exc).__name__
                logging.warning("%s: %s", field, exc)
    return dict(id=str(uuid4()), device=config["device"], measured_at=utc_now(),
                demo=bool(config.get("demo")), errors=errors, **readings)


def collect(config, db):
    interval = config.get("interval_seconds", 30)
    if not 1 <= interval <= 3600:
        raise ValueError("interval_seconds deve estar entre 1 e 3600")
    while True:
        start = time.monotonic()
        payload = sample(config)
        with db:
            db.execute("INSERT INTO samples(payload) VALUES (?)", (json.dumps(payload),))
        time.sleep(max(0, interval - (time.monotonic() - start)))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_batch(config, db):
    rows = db.execute("SELECT seq,payload FROM samples WHERE sent=0 ORDER BY seq LIMIT ?",
                      (BATCH,)).fetchall()
    if not rows:
        return 0
    samples = [json.loads(payload) for _, payload in rows]
    request = Request(config["server_url"].rstrip("/") + "/api/ingest",
                      data=json.dumps(samples).encode(),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + config["upload_token"]})
    with build_opener(NoRedirect).open(request, timeout=10) as response:
        ack = json.loads(response.read(65536))
        if ack != {"accepted": [item["id"] for item in samples]}:
            raise ValueError("Confirmação do lote não corresponde aos registros enviados")
    with db:
        db.executemany("UPDATE samples SET sent=1 WHERE seq=?", [(seq,) for seq, _ in rows])
    return len(rows)


def sync(config, db):
    url = urlsplit(config["server_url"])
    if (url.scheme != "https" and not (config.get("demo") and url.scheme == "http"
                                      and url.hostname in {"localhost", "127.0.0.1"})):
        raise ValueError("Configure a URL HTTPS do servidor")
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("URL do servidor inválida")
    if len(config["upload_token"]) < 24:
        raise ValueError("Token de envio deve ter pelo menos 24 caracteres")
    delay = 5
    while True:
        try:
            count = send_batch(config, db)
            delay = 5
            if count:
                logging.info("Lote confirmado: %d leituras", count)
            if count < BATCH:
                time.sleep(5)
        except (ValueError, OSError) as exc:
            logging.warning("Envio adiado por %ds: %s", delay, exc)
            time.sleep(delay)
            delay = min(delay * 2, 300)


def export(db, output):
    fields = ("id", "device", "measured_at", "demo", *FIELDS, "errors", "sent")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for payload, sent in db.execute("SELECT payload,sent FROM samples ORDER BY seq"):
        item = json.loads(payload)
        writer.writerow(dict(item, errors=json.dumps(item["errors"]), sent=sent))


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("collect", "sync", "csv"):
        sys.exit("Uso: python3 monitor.py {collect|sync|csv} config.json")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = json.loads(Path(sys.argv[2]).read_text())
    if not re.fullmatch(DEVICE_PATTERN, config["device"]):
        raise ValueError("Identificador de gabinete inválido")
    with local_db(config["local_db"]) as db:
        if sys.argv[1] == "csv":
            export(db, sys.stdout)
        else:
            {"collect": collect, "sync": sync}[sys.argv[1]](config, db)


if __name__ == "__main__":
    main()
