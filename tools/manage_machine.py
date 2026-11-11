from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import READY_TIMEOUT, build_client, friendly_error, get_machine, require, wait_for_status

ACTIONS = ("start", "stop", "pause", "resume", "hibernate", "wake")
SETTLED = {"stop": "stopped", "pause": "suspended", "hibernate": "hibernated"}


class ManageMachineTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        action = require(tool_parameters, "action").lower()
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {', '.join(ACTIONS)}, got {action!r}")
        get_machine(client, machine_id)
        try:
            getattr(client.machines, action)(machine_id)
            if action in ("start", "resume", "wake"):
                client.machines.wait_until_ready(machine_id, timeout=READY_TIMEOUT)
            status = wait_for_status(client, machine_id, SETTLED.get(action))
        except BoxdError as e:
            raise friendly_error(e) from e
        yield self.create_text_message(f"{action}: machine {machine_id} is now {status}.")
        yield self.create_json_message({"success": True, "machine_id": machine_id, "action": action, "status": status})
