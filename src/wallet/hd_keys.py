"""
Bitcoin extended public keys (xpub / ypub / zpub): address derivation.

A modern ("HD", BIP32) Bitcoin wallet uses a new address for every payment it
receives and sends its change to new addresses too. All of those addresses come
from one extended public key, which wallet apps can export. This module turns
such a key into the wallet's addresses so the whole wallet can be fetched, not
just one address.

Only public derivation is done here: an extended public key cannot spend funds,
and no private keys are ever needed or accepted.

Supported:
- xpub: the key format alone does not say which address type the wallet uses
  (many apps export "xpub" for every account type), so the caller checks all
  three single-signature types: legacy (1...), nested SegWit (3...) and native
  SegWit (bc1q...).
- ypub: nested SegWit (BIP49), addresses starting with 3
- zpub: native SegWit (BIP84), addresses starting with bc1q

Not supported (rejected with a clear error): multisig keys (Ypub/Zpub),
testnet keys (tpub/upub/vpub), Taproot (bc1p) wallets, private keys.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

# --- secp256k1 -------------------------------------------------------------

_P = 2**256 - 2**32 - 977
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def _point_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % _P == 0:
        return None
    if a == b:
        lam = 3 * a[0] * a[0] * pow(2 * a[1], -1, _P) % _P
    else:
        lam = (b[1] - a[1]) * pow(b[0] - a[0], -1, _P) % _P
    x = (lam * lam - a[0] - b[0]) % _P
    return x, (lam * (a[0] - x) - a[1]) % _P


def _point_mul(k: int, point=_G):
    result = None
    while k:
        if k & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        k >>= 1
    return result


def _decompress(pub: bytes):
    if len(pub) != 33 or pub[0] not in (2, 3):
        raise InvalidExtendedKey("Not a compressed public key")
    x = int.from_bytes(pub[1:], "big")
    if x >= _P:
        raise InvalidExtendedKey("Public key is not on the curve")
    y = pow((pow(x, 3, _P) + 7) % _P, (_P + 1) // 4, _P)
    if (y * y - x**3 - 7) % _P:
        raise InvalidExtendedKey("Public key is not on the curve")
    if y % 2 != pub[0] % 2:
        y = _P - y
    return x, y


def _compress(point) -> bytes:
    return bytes([2 + (point[1] & 1)]) + point[0].to_bytes(32, "big")


# --- hashing and encodings -------------------------------------------------

def _ripemd160(data: bytes) -> bytes:
    try:
        return hashlib.new("ripemd160", data).digest()
    except ValueError:  # OpenSSL 3 builds may not provide RIPEMD-160
        return _ripemd160_py(data)


def hash160(data: bytes) -> bytes:
    return _ripemd160(hashlib.sha256(data).digest())


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58check_encode(payload: bytes) -> str:
    data = payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def _b58check_decode(text: str) -> bytes:
    n = 0
    for ch in text:
        idx = _B58.find(ch)
        if idx < 0:
            raise InvalidExtendedKey(f"Invalid character {ch!r}")
        n = n * 58 + idx
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    raw = b"\0" * (len(text) - len(text.lstrip("1"))) + raw
    payload, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        raise InvalidExtendedKey("Checksum does not match (typo in the key?)")
    return payload


_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _segwit_v0_address(program: bytes, hrp: str = "bc") -> str:
    acc, bits, data = 0, 0, [0]  # witness version 0
    for b in program:
        acc = (acc << 8) | b
        bits += 8
        while bits >= 5:
            bits -= 5
            data.append((acc >> bits) & 31)
    if bits:
        data.append((acc << (5 - bits)) & 31)
    hrp_exp = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    poly = _bech32_polymod(hrp_exp + data + [0] * 6) ^ 1
    checksum = [(poly >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32[d] for d in data + checksum)


# --- extended keys ---------------------------------------------------------

class InvalidExtendedKey(ValueError):
    """The text is not a usable mainnet single-signature extended public key."""


ADDRESS_TYPES = {
    "p2pkh": "legacy (1...)",
    "p2sh-p2wpkh": "nested SegWit (3...)",
    "p2wpkh": "native SegWit (bc1q...)",
}

_VERSIONS = {
    bytes.fromhex("0488b21e"): ("xpub", None),  # address type not encoded
    bytes.fromhex("049d7cb2"): ("ypub", "p2sh-p2wpkh"),
    bytes.fromhex("04b24746"): ("zpub", "p2wpkh"),
}
_REJECTED = {
    "0488ade4": "This is a private key (xprv). Never share it; export the public key (xpub/ypub/zpub) instead.",
    "049d7878": "This is a private key (yprv). Never share it; export the public key (ypub) instead.",
    "04b2430c": "This is a private key (zprv). Never share it; export the public key (zpub) instead.",
    "0295b43f": "Multisig keys (Ypub) are not supported yet.",
    "02aa7ed3": "Multisig keys (Zpub) are not supported yet.",
    "043587cf": "Testnet keys (tpub) are not supported.",
    "044a5262": "Testnet keys (upub) are not supported.",
    "045f1cf6": "Testnet keys (vpub) are not supported.",
}


@dataclass(frozen=True)
class ExtendedPublicKey:
    text: str
    prefix: str
    address_type: str | None  # None for xpub: the caller must detect it
    depth: int
    chain_code: bytes
    public_key: bytes

    def child(self, index: int) -> "ExtendedPublicKey":
        """Non-hardened public child key (BIP32 CKDpub)."""
        if not 0 <= index < 2**31:
            raise ValueError("Only non-hardened indexes can be derived from a public key")
        digest = hmac.new(self.chain_code, self.public_key + index.to_bytes(4, "big"), hashlib.sha512).digest()
        tweak = int.from_bytes(digest[:32], "big")
        if tweak >= _N:
            raise InvalidExtendedKey("Invalid child key (try the next index)")
        point = _point_add(_point_mul(tweak), _decompress(self.public_key))
        if point is None:
            raise InvalidExtendedKey("Invalid child key (try the next index)")
        return ExtendedPublicKey(self.text, self.prefix, self.address_type, self.depth + 1,
                                 digest[32:], _compress(point))

    def address(self, address_type: str) -> str:
        h = hash160(self.public_key)
        if address_type == "p2pkh":
            return _b58check_encode(b"\x00" + h)
        if address_type == "p2sh-p2wpkh":
            return _b58check_encode(b"\x05" + hash160(b"\x00\x14" + h))
        if address_type == "p2wpkh":
            return _segwit_v0_address(h)
        raise ValueError(f"Unknown address type {address_type}")


def looks_like_extended_key(text: str) -> bool:
    return (text or "").strip()[:4].lower() in {"xpub", "ypub", "zpub", "tpub", "upub", "vpub",
                                                  "xprv", "yprv", "zprv"}


def parse_extended_key(text: str) -> ExtendedPublicKey:
    text = (text or "").strip()
    payload = _b58check_decode(text)
    if len(payload) != 78:
        raise InvalidExtendedKey("Wrong length for an extended public key")
    version = payload[:4]
    if version.hex() in _REJECTED:
        raise InvalidExtendedKey(_REJECTED[version.hex()])
    if version not in _VERSIONS:
        raise InvalidExtendedKey("Unrecognised key type; expected an xpub, ypub or zpub")
    prefix, address_type = _VERSIONS[version]
    public_key = payload[45:]
    _decompress(public_key)  # validates the point
    return ExtendedPublicKey(text, prefix, address_type, payload[4], payload[13:45], public_key)


def validate_extended_key(text: str) -> bool:
    try:
        parse_extended_key(text)
        return True
    except (InvalidExtendedKey, ValueError):
        return False


def derive_address(key: ExtendedPublicKey, address_type: str, chain: int, index: int) -> str:
    """Address at <account key>/chain/index (chain 0 = receive, 1 = change)."""
    return key.child(chain).child(index).address(address_type)


# --- pure-Python RIPEMD-160 (fallback only) --------------------------------

def _ripemd160_py(message: bytes) -> bytes:
    def rol(x, n):
        return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF

    fns = [
        lambda x, y, z: x ^ y ^ z,
        lambda x, y, z: (x & y) | (~x & z),
        lambda x, y, z: (x | ~y) ^ z,
        lambda x, y, z: (x & z) | (y & ~z),
        lambda x, y, z: x ^ (y | ~z),
    ]
    kl = [0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E]
    kr = [0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000]
    rl = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
          7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
          3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
          1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
          4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13]
    rr = [5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
          6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
          15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
          8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
          12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11]
    sl = [11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
          7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
          11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
          11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
          9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6]
    sr = [8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
          9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
          9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
          15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
          8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11]

    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    msg = message + b"\x80" + b"\0" * ((55 - len(message)) % 64) + (8 * len(message)).to_bytes(8, "little")
    for off in range(0, len(msg), 64):
        x = [int.from_bytes(msg[off + 4 * i: off + 4 * i + 4], "little") for i in range(16)]
        al, bl, cl, dl, el = h
        ar, br, cr, dr, er = h
        for j in range(80):
            r = j // 16
            t = (rol((al + (fns[r](bl, cl, dl) & 0xFFFFFFFF) + x[rl[j]] + kl[r]) & 0xFFFFFFFF, sl[j]) + el) & 0xFFFFFFFF
            al, el, dl, cl, bl = el, dl, rol(cl, 10), bl, t
            t = (rol((ar + (fns[4 - r](br, cr, dr) & 0xFFFFFFFF) + x[rr[j]] + kr[r]) & 0xFFFFFFFF, sr[j]) + er) & 0xFFFFFFFF
            ar, er, dr, cr, br = er, dr, rol(cr, 10), br, t
        t = (h[1] + cl + dr) & 0xFFFFFFFF
        h[1] = (h[2] + dl + er) & 0xFFFFFFFF
        h[2] = (h[3] + el + ar) & 0xFFFFFFFF
        h[3] = (h[4] + al + br) & 0xFFFFFFFF
        h[4] = (h[0] + bl + cr) & 0xFFFFFFFF
        h[0] = t
    return b"".join(v.to_bytes(4, "little") for v in h)
