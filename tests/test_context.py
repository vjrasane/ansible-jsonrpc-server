import sys
from ansible_jsonrpc_server.context import build_execution
from ansible_jsonrpc_server.messages import Connection, Flags, HostSpec

HOST = HostSpec(connection=Connection(type="local"))


def test_builds_plugin_with_configured_local_connection():
    plugin, task_vars = build_execution(
        HOST, "ansible.builtin.ping", {},
        {"ansible_python_interpreter": sys.executable}, Flags(),
    )
    assert plugin._connection is not None
    assert plugin._connection._shell is not None          # connection configured
    assert task_vars["ansible_python_interpreter"] == sys.executable


def test_flags_land_on_task():
    plugin, _ = build_execution(
        HOST, "ansible.builtin.command", {"_raw_params": "echo x"},
        {}, Flags(check_mode=True, diff=True, no_log=True),
    )
    assert plugin._task.check_mode is True
    assert plugin._task.diff is True
    assert plugin._task.no_log is True
    # parent-play scaffolding the module path reads (spike finding)
    assert plugin._task._parent._play._action_groups == {}


def test_plain_module_falls_back_to_normal_action():
    # ping has no action plugin → the generic 'normal' action
    plugin, _ = build_execution(HOST, "ansible.builtin.ping", {}, {}, Flags())
    assert type(plugin).__module__.endswith("action.normal")
