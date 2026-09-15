from __future__ import annotations

import base64
import hashlib


def onion_host(seed: int) -> str:
    public_key = hashlib.sha256(f"onionatlas-test-{seed}".encode()).digest()
    version = b"\x03"
    checksum = hashlib.sha3_256(b".onion checksum" + public_key + version).digest()[:2]
    label = base64.b32encode(public_key + checksum + version).decode().lower()
    assert len(label) == 56
    return label + ".onion"
