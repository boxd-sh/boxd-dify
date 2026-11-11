import secrets
import shlex
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import HOME, SERVICE_DIR, abs_path, build_client, get_machine, parse_json_object, require, run


class StartServiceTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        command = require(tool_parameters, "command")
        cwd = abs_path(str(tool_parameters.get("cwd") or "").strip() or HOME, "cwd")
        env = parse_json_object(tool_parameters.get("env_vars"), "env_vars") or {}
        get_machine(client, machine_id)
        service_id = f"svc-{secrets.token_hex(4)}"
        log = f"{SERVICE_DIR}/{service_id}.log"
        pidfile = f"{SERVICE_DIR}/{service_id}.pid"
        exports = "".join(f"export {k}={shlex.quote(v)}; " for k, v in env.items() if k.isidentifier())
        # The service writes its own pid, then execs the command, so the pid stays valid.
        inner = f"echo $$ > {pidfile}; cd {shlex.quote(cwd)} && {exports}exec bash -c {shlex.quote(command)}"
        launch = (
            f"mkdir -p {SERVICE_DIR} && rm -f {pidfile}; "
            f"setsid nohup bash -c {shlex.quote(inner)} > {log} 2>&1 < /dev/null & "
            f"for i in 1 2 3 4 5 6 7 8 9 10; do test -s {pidfile} && break; sleep 0.1; done; sleep 1; "
            f"if kill -0 $(cat {pidfile} 2>/dev/null) 2>/dev/null; then echo running; else echo exited; fi"
        )
        result = run(client, machine_id, launch, timeout=30)
        if result["exit_code"] != 0:
            raise ValueError(f"Could not start the service: {result['stderr'] or result['stdout']}")
        state = result["stdout"].strip().splitlines()[-1] if result["stdout"].strip() else "unknown"
        yield self.create_text_message(
            f"Started service {service_id} on machine {machine_id} ({state}). Read its output with Get Service Logs."
        )
        yield self.create_json_message(
            {"service_id": service_id, "machine_id": machine_id, "running": state == "running", "log_path": log}
        )
        yield self.create_variable_message("service_id", service_id)
