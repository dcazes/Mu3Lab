"""Ephemeral trusted HTTPS for bw's loopback-only Vaultwarden connection.

Current Bitwarden requires HTTPS. The bridge binds only 127.0.0.1, forwards
only to the specified loopback origin, and supplies its certificate solely
to the job's bw process. No system trust, live proxy or firewall is changed.
"""

from __future__ import annotations

import ipaddress
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class LoopbackTLS:
    def __init__(self, upstream: str, folder: Path) -> None:
        parts = urlsplit(upstream)
        if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("The local HTTPS bridge accepts loopback HTTP only.")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Mu3Lab temporary vault connection")])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False
            )
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(key, hashes.SHA256())
        )
        self.certificate = folder / "bridge-cert.pem"
        private_path = folder / "bridge-key.pem"
        self.certificate.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        private_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        private_path.chmod(0o600)
        client = httpx.Client(base_url=upstream.rstrip("/"), timeout=60, trust_env=False)
        self.client = client

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                # Requests and paths can contain credentials. Never log them.
                return

            def forward(self) -> None:
                if not self.path.startswith("/") or self.path.startswith("//"):
                    self.send_error(400)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length > 10_000_000:
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                headers = {
                    k: v for k, v in self.headers.items() if k.lower() not in {"host", "connection", "content-length"}
                }
                try:
                    # Concatenation prevents an absolute request target changing the upstream.
                    reply = client.request(
                        self.command, upstream.rstrip("/") + self.path, content=body, headers=headers
                    )
                except httpx.HTTPError:
                    self.send_error(502)
                    return
                self.send_response(reply.status_code)
                for key, value in reply.headers.multi_items():
                    if key.lower() not in {"content-length", "transfer-encoding", "connection", "content-encoding"}:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(reply.content)))
                self.end_headers()
                self.wfile.write(reply.content)

            do_GET = forward
            do_POST = forward
            do_PUT = forward
            do_PATCH = forward
            do_DELETE = forward

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certificate, private_path)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.origin = f"https://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.client.close()
