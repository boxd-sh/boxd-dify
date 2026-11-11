from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import CREATED_BY_LABEL, build_client, friendly_error, get_machine, require


class DeleteMachineTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        machine = get_machine(client, machine_id)
        key, value = CREATED_BY_LABEL
        if (machine.labels or {}).get(key) != value:
            raise ValueError(
                f"Machine '{machine.name}' was not created by this plugin "
                f"(no {key}={value} label), so it is not deleted."
            )
        try:
            client.machines.delete(machine_id)
        except BoxdError as e:
            raise friendly_error(e) from e
        yield self.create_text_message(f"Deleted machine {machine.name} ({machine_id}).")
        yield self.create_json_message({"success": True, "machine_id": machine_id})
