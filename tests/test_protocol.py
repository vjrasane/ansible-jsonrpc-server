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


def test_non_json_object_in_result_is_stringified_not_crashed():
    # A failed module result can carry non-JSON objects (here an ABCMeta); the encoder must
    # stringify them so the reply — and the real error message — still gets through, rather
    # than raising "Encoding objects of type ABCMeta is unsupported".
    import abc

    class Weird(abc.ABC):
        pass

    msg = {"jsonrpc": "2.0", "id": 1,
           "result": {"msg": "parent dir missing", "exception": Weird}}
    got = read_message(_pipe(msg))
    assert got["result"]["msg"] == "parent dir missing"
    assert isinstance(got["result"]["exception"], str)  # stringified, not dropped
