from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import MAX_FILE_BYTES, abs_path, build_client, friendly_error, get_machine, require


class WriteFileTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        remote = abs_path(require(tool_parameters, "remote_path"), "remote_path")
        content = tool_parameters.get("content")
        if content is None:
            raise ValueError("content is required")
        data = str(content).encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"content is larger than {MAX_FILE_BYTES // 2**20} MiB")
        get_machine(client, machine_id)
        try:
            client.machines.files.upload(machine_id, remote, data)
        except BoxdError as e:
            raise friendly_error(e) from e
        yield self.create_text_message(f"Wrote {len(data)} bytes to {remote}.")
        yield self.create_json_message(
            {"success": True, "machine_id": machine_id, "remote_path": remote, "size_bytes": len(data)}
        )
