from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import build_client, create_machine, friendly_error, machine_summary, optional_int, parse_json_object


class CreateMachineTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, settings = build_client(self.runtime.credentials)
        name = str(tool_parameters.get("name") or "").strip() or None
        size = str(tool_parameters.get("size") or "").strip() or None
        labels = parse_json_object(tool_parameters.get("labels"), "labels")
        auto_suspend = optional_int(tool_parameters, "auto_suspend_timeout")
        snapshot = str(tool_parameters.get("from_snapshot") or "").strip() or None
        delete_after = optional_int(tool_parameters, "delete_after")
        try:
            machine = create_machine(
                client,
                settings,
                name=name,
                size=size,
                labels=labels,
                auto_suspend_timeout=auto_suspend,
                from_snapshot=snapshot,
                delete_after=delete_after,
            )
            machine = client.machines.get(machine.id)
        except BoxdError as e:
            raise friendly_error(e) from e
        yield self.create_text_message(f"Created machine {machine.name} ({machine.id}).")
        yield self.create_json_message(machine_summary(machine))
        yield self.create_variable_message("machine_id", machine.id)
