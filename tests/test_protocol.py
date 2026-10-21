import io
from ansible_jsonrpc_server.protocol import read_message, write_message


def _pipe(obj):
    buf = io.BytesIO()
    write_message(buf, obj)
    buf.seek(0)
    return buf


def test_roundtrip_content_length_framed():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "runAction", "params": {"x": "héllo"}}
    assert read_message(_pipe(msg)) == msg


def test_frame_has_content_length_header():
    buf = io.BytesIO()
    write_message(buf, {"jsonrpc": "2.0", "id": 1, "result": {}})
    raw = buf.getvalue()
    assert raw.startswith(b"Content-Length: ")
    assert b"\r\n\r\n" in raw


def test_clean_eof_returns_none():
    assert read_message(io.BytesIO(b"")) is None
