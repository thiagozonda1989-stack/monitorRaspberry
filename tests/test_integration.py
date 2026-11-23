import csv
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from wsgiref.simple_server import make_server

from monitor import export, local_db, sample, send_batch
from server import application


class IntegrationTest(unittest.TestCase):
    def test_offline_recovery_and_server_deduplication(self):
        read_token = "r" * 32
        upload_token = "u" * 32
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SERVER_DB": str(Path(directory) / "server.sqlite3"),
            "READ_TOKEN": read_token,
            "UPLOAD_TOKEN": upload_token,
        }), make_server("127.0.0.1", 0, application) as http:
            server_url = f"http://127.0.0.1:{http.server_port}"
            config = dict(device="demo-01", demo=True, server_url=server_url,
                          upload_token=upload_token)
            local_path = Path(directory) / "local.sqlite3"
            db = local_db(local_path)
            item = sample(config)
            with db:
                db.execute("INSERT INTO samples(payload) VALUES (?)", (json.dumps(item),))
            with patch("monitor.build_opener") as opener:
                opener.return_value.open.side_effect = URLError("sem LTE")
                with self.assertRaises(URLError):
                    send_batch(config, db)
            db.close()
            db = local_db(local_path)
            self.addCleanup(db.close)
            self.assertEqual(db.execute("SELECT sent FROM samples").fetchone(), (0,))
            worker = threading.Thread(target=http.serve_forever, daemon=True)
            worker.start()
            try:
                self.assertEqual(send_batch(config, db), 1)
                self.assertEqual(send_batch(config, db), 0)
                with db:
                    db.execute("UPDATE samples SET sent=0")
                self.assertEqual(send_batch(config, db), 1)
                with self.assertRaises(HTTPError) as denied:
                    urlopen(server_url + "/api/history", timeout=3)
                self.assertEqual(denied.exception.code, 401)
                request = Request(server_url + "/api/history",
                                  headers={"Authorization": "Bearer " + read_token})
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(json.load(response)["readings"], [item])
                for path in ("/", "/app.js", "/style.css", "/healthz"):
                    with urlopen(server_url + path, timeout=3) as response:
                        self.assertTrue(response.read())
                output = io.StringIO()
                export(db, output)
                row, = csv.DictReader(io.StringIO(output.getvalue()))
                self.assertEqual((row["id"], row["sent"]), (item["id"], "1"))
            finally:
                http.shutdown()
                worker.join()
