from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import CREATED_BY_LABEL, build_client, friendly_error, machine_summary


class ListMachinesTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, settings = build_client(self.runtime.credentials)
        include_all = bool(tool_parameters.get("include_all"))
        labels = None if include_all else dict([CREATED_BY_LABEL])
        try:
            machines = list(client.machines.list(org=settings.org, labels=labels))
        except BoxdError as e:
            raise friendly_error(e) from e
        items = [machine_summary(m) for m in machines]
        lines = [f"{m['name']} ({m['machine_id']}): {m['status']}" for m in items]
        yield self.create_text_message("\n".join(lines) or "No machines.")
        yield self.create_json_message({"machines": items, "count": len(items)})
