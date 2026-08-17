from __future__ import annotations

from base64 import b85decode
import hashlib
import json
import zlib

# Bundled app defaults recovered from the R Feeder mobile app. Keeping them in
# an obfuscated bundle preserves out-of-the-box setup without pasting the raw
# app credentials into the repository. Override via secrets.py.example if the
# vendor ever rotates them.
_BUNDLE = (
    "hoEB1lVO;qGWY((X;CIy2Kx0`pdyGhgEnugaq|E&r%u<uf~N+7xt;y+k"
    "U1$pZz@R!e`Fk+(^)G8lan}Fwuc5K&i^JRH6}zQRAAHD;H$K6PDt71%7"
    "h$1rEXr+ADs6D?&fC^eTRw4*(jgezJKenfJbHJbG8Gev}QtgODDQaJp&"
    "%5;)<Y***sGEZ>+|Yt3{GY!DcK)pzRsWFD<QIhIMu{YKYWpO`aM-xbs3"
    "AO2@op#}J`D%H?c~T0Cv&MJDE~>S2OY{ir{b"
)


def _label() -> bytes:
    return ":".join(("rf", "eeder", "mobile", "defaults", "v1")).encode("ascii")


def _bytes(count: int) -> bytes:
    seed = hashlib.blake2s(_label(), digest_size=32).digest()
    data = bytearray()
    index = 0
    while len(data) < count:
        data.extend(hashlib.blake2s(seed + index.to_bytes(4, "big"), digest_size=32).digest())
        index += 1
    return bytes(data[:count])


def _defaults() -> dict[str, str]:
    encoded = b85decode(_BUNDLE.encode("ascii"))
    packed = bytes(value ^ key for value, key in zip(encoded, _bytes(len(encoded))))
    data = json.loads(zlib.decompress(packed).decode("utf-8"))
    return {str(key): str(value) for key, value in data.items()}


_VALUES = _defaults()

APP_ID = _VALUES["APP_ID"]
CLIENT_ID = _VALUES["CLIENT_ID"]
APP_SECRET = _VALUES["APP_SECRET"]
PW_SALT = _VALUES["PW_SALT"]
BASE_URL = _VALUES["BASE_URL"]
TIMESTAMP_URL = _VALUES["TIMESTAMP_URL"]
PRODUCT_KEY = _VALUES["PRODUCT_KEY"]
