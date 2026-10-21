from ansible.errors import AnsibleConnectionFailure, AnsibleActionSkip, AnsibleActionFail
from ansible._internal._errors import _error_utils
from .messages import ResultFrame

# Mirrors ansible's task-level censor_result (executor/task_result.py): keep only
# _PRESERVE, replace everything else with a single 'censored' note. Status flags and
# changed are read off the raw dict *before* redaction, so narrowing _PRESERVE here
# cannot change the frame-level status/changed. msg is intentionally NOT preserved.
_PRESERVE = {"attempts", "changed", "retries", "_ansible_no_log", "exception",
             "warnings", "deprecations"}
_CENSORED = ("the output has been hidden due to the fact that 'no_log: true' "
             "was specified for this result")


def shape_result(raw: dict, no_log: bool) -> ResultFrame:
    raw = dict(raw or {})
    if raw.get("unreachable"):
        status = "unreachable"
    elif raw.get("failed"):
        status = "failed"
    elif raw.get("skipped"):
        status = "skipped"
    else:
        status = "ok"
    changed = bool(raw.get("changed", False))
    warnings = list(raw.get("warnings") or [])
    deprecations = list(raw.get("deprecations") or [])
    result = _redact(raw) if no_log else raw
    return ResultFrame(status=status, changed=changed, result=result,
                       warnings=warnings, deprecations=deprecations)


def _redact(raw: dict) -> dict:
    censored = {k: raw[k] for k in _PRESERVE if k in raw}
    censored["censored"] = _CENSORED
    return censored


def status_from_exception(exc: BaseException) -> tuple[str, dict]:
    raw = _error_utils.result_dict_from_exception(exc)
    if isinstance(exc, AnsibleConnectionFailure):
        return "unreachable", raw
    if isinstance(exc, AnsibleActionSkip):
        return "skipped", raw
    if isinstance(exc, AnsibleActionFail):
        return "failed", raw
    raw.setdefault("failed", True)
    return "failed", raw
