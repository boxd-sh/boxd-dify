from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import HOME, abs_path, build_client, ensure_running, friendly_error, require


class ListFilesTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        path = abs_path(str(tool_parameters.get("path") or "").strip() or HOME)
        ensure_running(client, machine_id)
        try:
            listing = client.machines.files.list_dir(machine_id, path)
        except BoxdError as e:
            raise friendly_error(e) from e
        entries = [
            {"name": e.name, "is_dir": e.is_dir, "size_bytes": e.size_bytes, "permissions": e.permissions}
            for e in sorted(listing.entries, key=lambda e: (not e.is_dir, e.name))
        ]
        lines = [f"{e['name']}/" if e["is_dir"] else f"{e['name']} ({e['size_bytes']} B)" for e in entries]
        yield self.create_text_message("\n".join(lines) or "(empty)")
        yield self.create_json_message(
            {"path": path, "entries": entries, "count": len(entries), "truncated": listing.truncated}
        )
