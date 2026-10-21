import signal

from ansible.errors import AnsibleError
from .context import build_execution
from .messages import ResultFrame, RunParams
from .reap import install_sigterm_reaper, kill_descendants, uninstall_sigterm_reaper
from .result import shape_result, status_from_exception


class _Timeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise _Timeout()


def run_action(params: RunParams) -> ResultFrame:
    no_log = params.flags.no_log
    timeout = params.flags.timeout
    plugin, task_vars = build_execution(
        params.host, params.fqcn, params.args, params.vars, params.flags,
    )
    # Each task owns its worker's main thread, so a per-task SIGALRM is a clean soft
    # timeout: it interrupts plugin.run (e.g. a blocking connection) and we return a
    # timed_out frame; the worker is recycled after (§4.10). A wedged worker that
    # ignores the alarm is the parent's hard backstop — deferred to concurrent dispatch.
    prev = signal.signal(signal.SIGALRM, _on_alarm) if timeout else None
    if timeout:
        signal.alarm(timeout)
    # A cancel or hard timeout arrives as pebble's SIGTERM while we block in plugin.run;
    # reap the module subtree before the worker dies (§4.10). Scoped to the run, so a
    # clean completion's worker recycle never reaps.
    install_sigterm_reaper()
    try:
        raw = plugin.run(task_vars=task_vars)
    except _Timeout:
        kill_descendants()   # soft timeout: the module subprocess is still running
        return ResultFrame(status="timed_out", changed=False,
                           result={"msg": "task timed out after %ds" % timeout})
    except AnsibleError as exc:               # task-level failures: map, don't raise
        status, raw = status_from_exception(exc)
        shaped = shape_result(raw, no_log)
        shaped.status = status
        return shaped
    finally:
        if timeout:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, prev)
        uninstall_sigterm_reaper()
    return shape_result(raw, no_log)
