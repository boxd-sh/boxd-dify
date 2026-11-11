from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import MAX_OUTPUT_CHARS, abs_path, build_client, ensure_running, file_size, friendly_error, require


class ReadFileTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        remote = abs_path(require(tool_parameters, "remote_path"), "remote_path")
        ensure_running(client, machine_id)
        try:
            file_size(client, machine_id, remote)
            data = client.machines.files.download(machine_id, remote)
        except BoxdError as e:
            raise friendly_error(e) from e
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{remote} is not a UTF-8 text file. Use Download File instead.") from None
        truncated = len(text) > MAX_OUTPUT_CHARS
        if truncated:
            text = text[:MAX_OUTPUT_CHARS]
        yield self.create_text_message(text)
        yield self.create_json_message(
            {
                "machine_id": machine_id,
                "remote_path": remote,
                "size_bytes": len(data),
                "content": text,
                "truncated": truncated,
            }
        )
