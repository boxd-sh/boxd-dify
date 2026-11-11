from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import SERVICE_DIR, SERVICE_ID_RE, build_client, optional_int, require, run


class GetServiceLogsTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        service_id = require(tool_parameters, "service_id")
        if not SERVICE_ID_RE.fullmatch(service_id):
            raise ValueError(f"service_id looks like svc-1a2b3c4d, got {service_id!r}")
        lines = optional_int(tool_parameters, "lines") or 200
        if lines < 1 or lines > 5000:
            raise ValueError("lines must be between 1 and 5000")
        log = f"{SERVICE_DIR}/{service_id}.log"
        pidfile = f"{SERVICE_DIR}/{service_id}.pid"
        command = (
            f"test -f {log} || {{ echo 'no such service' >&2; exit 3; }}; "
            f"if kill -0 $(cat {pidfile}) 2>/dev/null; then echo RUNNING; else echo STOPPED; fi; "
            f"tail -n {lines} {log}"
        )
        result = run(client, machine_id, command, timeout=30)
        if result["exit_code"] != 0:
            raise ValueError(f"Service {service_id} not found on machine {machine_id}")
        state, _, logs = result["stdout"].partition("\n")
        yield self.create_text_message(logs or "(no output yet)")
        yield self.create_json_message(
            {"service_id": service_id, "machine_id": machine_id, "running": state == "RUNNING", "logs": logs}
        )
