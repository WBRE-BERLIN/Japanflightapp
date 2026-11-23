"""End-to-end test of the PHP push backend.

Starts `php -S` with the real API, a fake push service, registers a device,
runs cron.php and decrypts what arrives (RFC 8291) + verifies the VAPID JWT.

  python tests/push_e2e.py          (needs php + `pip install cryptography`)
"""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
RECEIVED: list[dict] = []


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PushService(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        RECEIVED.append({"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(410 if self.path.endswith("/gone") else 201)
        self.end_headers()

    def log_message(self, *a):
        pass


def hk(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def decrypt(body: bytes, ua_priv: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    assert rs == 4096 and idlen == 65
    ua_pub = ua_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    prk_key = hk(auth, shared)
    ikm = hk(prk_key, b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")
    prk = hk(salt, ikm)
    cek = hk(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = hk(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    assert plain.endswith(b"\x02"), "missing last-record delimiter"
    return plain[:-1]


def verify_vapid(auth_header: str, vapid_pub_b64u: str, expected_aud: str):
    parts = dict(p.strip().split("=", 1) for p in auth_header[len("vapid "):].split(","))
    assert parts["k"] == vapid_pub_b64u
    h, c, sig = parts["t"].split(".")
    claims = json.loads(unb64u(c))
    assert claims["aud"] == expected_aud, claims
    assert claims["exp"] > time.time() and claims["sub"]
    raw = unb64u(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(vapid_pub_b64u))
    pub.verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))


def http(method: str, url: str, data: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="jfw-e2e-"))
    data_dir, storage_dir = tmp / "data", tmp / "storage"
    subprocess.run([sys.executable, "-m", "flightscan", "demo", "--out", str(data_dir)], cwd=ROOT / "scraper",
                   check=True, capture_output=True)
    cron_key = "test-cron-key-0123456789"
    cfg = {"cron_key": cron_key, "app_token": "letmein", "vapid_subject": "mailto:test@example.com",
           "data_dir": str(data_dir), "storage_dir": str(storage_dir), "allow_http_endpoints": True}
    php_arr = ",\n".join(f"  '{k}' => " + ("true" if v is True else f"'{v}'") for k, v in cfg.items())
    (tmp / "config.php").write_text(f"<?php\nreturn [\n{php_arr}\n];\n")

    push_port, php_port = free_port(), free_port()
    srv = HTTPServer(("127.0.0.1", push_port), PushService)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    php = subprocess.Popen(["php", "-S", f"127.0.0.1:{php_port}", "-t", str(ROOT / "web")],
                           env={**os.environ, "JFW_CONFIG": str(tmp / "config.php")},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{php_port}/api"
    try:
        for _ in range(50):
            try:
                st, status = http("GET", f"{base}/status.php")
                break
            except OSError:
                time.sleep(0.1)
        assert st == 200 and status["vapid_public_key"] and status["token_required"], status
        vapid = status["vapid_public_key"]

        ua_priv = ec.generate_private_key(ec.SECP256R1())
        ua_pub = ua_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        sub = {"endpoint": f"http://127.0.0.1:{push_port}/push/dev1", "keys": {"p256dh": b64u(ua_pub), "auth": b64u(auth)}}
        prefs = {"notify_time": "00:00", "tz": "Europe/Berlin", "windows": ["may", "oct", "wild"], "alert_below": 99999}

        st, r = http("POST", f"{base}/subscribe.php", {"subscription": sub, "prefs": prefs})
        assert st == 403, "token must be required"
        st, r = http("POST", f"{base}/subscribe.php", {"subscription": sub, "prefs": prefs}, {"X-App-Token": "letmein"})
        assert st == 200 and r["created"], r
        gone = {"endpoint": f"http://127.0.0.1:{push_port}/push/gone", "keys": sub["keys"]}
        http("POST", f"{base}/subscribe.php", {"subscription": gone, "prefs": prefs}, {"X-App-Token": "letmein"})

        st, r = http("GET", f"{base}/cron.php?key=wrong")
        assert st == 403
        st, r = http("GET", f"{base}/cron.php?key={cron_key}&event=scrape")
        assert st == 200, r
        mine = [m for m in RECEIVED if m["path"] == "/push/dev1"]
        assert len(mine) == 2, f"expected daily + alert, got {len(mine)}: {r}"
        texts = []
        for m in mine:
            assert m["headers"]["Content-Encoding"] == "aes128gcm"
            verify_vapid(m["headers"]["Authorization"], vapid, f"http://127.0.0.1:{push_port}")
            texts.append(json.loads(decrypt(m["body"], ua_priv, auth)))
        daily = next(t for t in texts if t["tag"] == "daily")
        alert = next(t for t in texts if t["tag"] == "alert")
        assert daily["title"].startswith("✈ Japan for 2: from"), daily
        assert "May" in daily["body"] and "?offer=" in daily["url"], daily
        assert alert["title"].startswith("🔥 Price alert"), alert
        print("daily :", daily["title"], "|", daily["body"].replace("\n", " | "))
        print("alert :", alert["title"])

        # second tick: nothing new (daily sent, alert price unchanged); gone device removed
        n = len(RECEIVED)
        st, r = http("GET", f"{base}/cron.php?key={cron_key}")
        assert st == 200 and len([m for m in RECEIVED[n:] if m["path"] == "/push/dev1"]) == 0, r
        st, status = http("GET", f"{base}/status.php")
        assert status["subscriptions"] == 1, status

        st, r = http("POST", f"{base}/test.php", {"endpoint": sub["endpoint"]}, {"X-App-Token": "letmein"})
        assert st == 200 and r["ok"], r
        test_msg = json.loads(decrypt(RECEIVED[-1]["body"], ua_priv, auth))
        assert test_msg["title"].startswith("[Test]"), test_msg

        st, r = http("POST", f"{base}/unsubscribe.php", {"endpoint": sub["endpoint"]})
        assert r["removed"] == 1
        assert (storage_dir / ".htaccess").exists()
        print("push e2e: OK")
        return 0
    finally:
        php.terminate()
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
