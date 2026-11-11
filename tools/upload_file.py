from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import MAX_FILE_BYTES, abs_path, build_client, ensure_running, friendly_error, require


class UploadFileTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        file = tool_parameters.get("file")
        if file is None:
            raise ValueError("file is required")
        remote = str(tool_parameters.get("remote_path") or "").strip()
        if not remote or remote.endswith("/"):
            remote = remote + (getattr(file, "filename", None) or "upload.bin")
        remote = abs_path(remote, "remote_path")
        data = file.blob
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"file is larger than {MAX_FILE_BYTES // 2**20} MiB")
        ensure_running(client, machine_id)
        try:
            client.machines.files.upload(machine_id, remote, data)
        except BoxdError as e:
            raise friendly_error(e) from e
        yield self.create_text_message(f"Uploaded {len(data)} bytes to {remote}.")
        yield self.create_json_message(
            {"success": True, "machine_id": machine_id, "remote_path": remote, "size_bytes": len(data)}
        )
