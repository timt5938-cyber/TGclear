"""Authenticated encryption for one remote TGclear account."""

import base64
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class DataCipher:
    def __init__(self, master_key: str, tenant_id: str) -> None:
        raw = base64.b64decode(master_key, validate=True)
        if len(raw) != 32:
            raise ValueError("TG_DATA_KEY must contain 32 random bytes in base64")
        self.tenant_id = tenant_id
        self.key = HKDF(algorithm=hashes.SHA256(), length=32, salt=tenant_id.encode(), info=b"tgclear-data-v1").derive(raw)

    def encrypt(self, data: bytes, purpose: str) -> bytes:
        nonce = os.urandom(12)
        return b"TGC1" + nonce + AESGCM(self.key).encrypt(nonce, data, (self.tenant_id + ":" + purpose).encode())

    def decrypt(self, data: bytes, purpose: str) -> bytes:
        if not data.startswith(b"TGC1") or len(data) < 32:
            raise ValueError("Invalid encrypted TGclear data")
        return AESGCM(self.key).decrypt(data[4:16], data[16:], (self.tenant_id + ":" + purpose).encode())

    def write(self, path: Path, data: bytes, purpose: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=".tgclear-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                os.chmod(name, 0o600)
                output.write(self.encrypt(data, purpose))
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def read(self, path: Path, purpose: str) -> bytes:
        return self.decrypt(path.read_bytes(), purpose)
