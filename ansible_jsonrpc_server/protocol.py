from typing import BinaryIO, NoReturn

import msgspec

_HEADER_SEP = b"\r\n\r\n"


def _enc_fallback(obj: object) -> str:
    # Module results can carry arbitrary Python objects (exception instances, classes /
    # ABCMeta, …) that msgspec can't JSON-encode. Stringify them so a result is always
    # sendable — letting the encoder raise here would crash the reply and mask the real
    # task error (e.g. a module failure) with an opaque "unsupported type" TypeError.
    return repr(obj)


_encode = msgspec.json.Encoder(enc_hook=_enc_fallback).encode
_decode = msgspec.json.decode


def write_message(stream: BinaryIO, obj: object) -> None:
    # msgspec.json encodes msgspec Structs transparently, so result frames can be
    # written without first converting them to dicts.
    body = _encode(obj)
    stream.write(b"Content-Length: %d\r\n\r\n" % len(body))
    stream.write(body)
    stream.flush()


def read_message(stream: BinaryIO) -> dict | None:
    headers = _read_headers(stream)
    if headers is None:
        return None
    length = int(headers["content-length"])
    body = _read_exactly(stream, length)
    if body is None:
        raise ConnectionError("truncated message body")
    return _decode(body)


def _read_headers(stream: BinaryIO) -> dict[str, str] | None:
    buf = b""
    while _HEADER_SEP not in buf:
        chunk = stream.read(1)
        if not chunk:
            return None if not buf else _raise("truncated headers")
        buf += chunk
    headers = {}
    for line in buf[: -len(_HEADER_SEP)].split(b"\r\n"):
        if not line:
            continue
        k, _, v = line.partition(b":")
        headers[k.decode().strip().lower()] = v.decode().strip()
    return headers


def _read_exactly(stream: BinaryIO, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _raise(msg: str) -> NoReturn:
    raise ConnectionError(msg)
