"""A throwaway HTTPS server with a self-signed certificate.

A client that verifies certificates must refuse it; a client that does not would connect.
That makes it a real behavioural proof that TLS verification is on, not just an assertion
about a config flag."""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class SelfSignedTlsServer:
    """Serves a canned HTTP reply over TLS using a certificate no trust store has heard of."""

    def __init__(self) -> None:
        self.connections_completed = 0

    @asynccontextmanager
    async def running(self) -> AsyncIterator[str]:
        with tempfile.TemporaryDirectory() as directory:
            context = self._server_context(Path(directory))
            server = await asyncio.start_server(self._serve, "127.0.0.1", 0, ssl=context)
            port = server.sockets[0].getsockname()[1]
            try:
                yield f"https://localhost:{port}"
            finally:
                server.close()
                await server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections_completed += 1
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
        await writer.drain()
        writer.close()

    @staticmethod
    def _server_context(directory: Path) -> ssl.SSLContext:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256())
        )
        cert_path, key_path = directory / "cert.pem", directory / "key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        return context
