"""Create the remote data key and an encrypted pre-migration rollback archive."""

import base64
import io
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ROOT = Path("/opt/tgclear")
KEY_FILE = ROOT / "crypto.env"


def main() -> None:
    if KEY_FILE.exists():
        raise SystemExit("crypto.env already exists; preserve the existing key")

    key = os.urandom(32)
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for relative in ("data/users", "backups/users"):
            source = ROOT / relative
            if source.exists():
                for path in source.rglob("*"):
                    if path.is_symlink():
                        raise RuntimeError("Refusing symlink in backup source")
                archive.add(source, arcname=relative)

    nonce = os.urandom(12)
    sealed = b"TGR1" + nonce + AESGCM(key).encrypt(nonce, data.getvalue(), b"tgclear-rollback-v1")
    name = "rollback-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".tar.gcm"
    backup = ROOT / name
    with backup.open("xb") as output:
        os.chmod(backup, 0o600)
        output.write(sealed)
        output.flush()
        os.fsync(output.fileno())
    if AESGCM(key).decrypt(sealed[4:16], sealed[16:], b"tgclear-rollback-v1") != data.getvalue():
        raise RuntimeError("Rollback archive verification failed")

    try:
        with KEY_FILE.open("x", encoding="ascii") as output:
            os.chmod(KEY_FILE, 0o600)
            output.write("TG_DATA_KEY=" + base64.b64encode(key).decode("ascii") + "\n")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    print("Encrypted rollback archive ready:", backup.name)


if __name__ == "__main__":
    main()
