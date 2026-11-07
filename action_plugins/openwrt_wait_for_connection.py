"""Use Ansible's reconnect/retry logic with a Python-free remote probe."""
from ansible.plugins.action.wait_for_connection import ActionModule as WaitForConnection


class ActionModule(WaitForConnection):
    def _execute_module(self, module_name=None, module_args=None, task_vars=None, **kwargs):
        if module_name == "ansible.legacy.ping":
            # The parent resets SSH before each attempt and retries exceptions.
            # OpenWrt commonly has no Python, so execute a shell builtin instead.
            result = self._low_level_execute_command("true", sudoable=True)
            return {"ping": "pong" if result.get("rc") == 0 else None}
        return super()._execute_module(
            module_name=module_name,
            module_args=module_args,
            task_vars=task_vars,
            **kwargs,
        )
