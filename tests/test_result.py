from ansible_jsonrpc_server.result import shape_result, status_from_exception
from ansible.errors import AnsibleConnectionFailure, AnsibleActionFail


def test_ok_and_changed_lifted():
    out = shape_result({"changed": True, "cmd": "echo", "stdout": "hi"}, no_log=False)
    assert out.status == "ok"
    assert out.changed is True
    assert out.result["stdout"] == "hi"


def test_failed_status():
    out = shape_result({"failed": True, "msg": "boom"}, no_log=False)
    assert out.status == "failed"
    assert out.result["msg"] == "boom"


def test_unreachable_status():
    out = shape_result({"unreachable": True, "msg": "no route"}, no_log=False)
    assert out.status == "unreachable"


def test_warnings_surfaced():
    out = shape_result({"changed": False, "warnings": ["w1"], "deprecations": ["d1"]}, no_log=False)
    assert out.warnings == ["w1"]
    assert out.deprecations == ["d1"]


def test_no_log_redacts_result_values():
    out = shape_result({"changed": True, "secret": "hunter2", "msg": "done"}, no_log=True)
    assert "hunter2" not in str(out.result)
    assert out.changed is True            # preserved flags survive redaction


def test_no_log_drops_msg_and_nested_like_ansible_censor():
    # ansible's censor_result keeps only _PRESERVE + a 'censored' note; msg is NOT
    # preserved, and modules routinely echo inputs into msg → must not leak.
    out = shape_result(
        {"changed": True, "msg": "token=hunter2", "data": {"inner": "hunter2"}},
        no_log=True,
    )
    assert "hunter2" not in str(out.result)      # msg + nested both gone
    assert "msg" not in out.result
    assert out.result["changed"] is True
    assert "censored" in out.result


def test_exception_mapping():
    status, raw = status_from_exception(AnsibleConnectionFailure("down"))
    assert status == "unreachable"
    status, raw = status_from_exception(AnsibleActionFail("nope"))
    assert status == "failed"
