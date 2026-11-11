import mimetypes
import posixpath
from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import MAX_FILE_BYTES, abs_path, build_client, friendly_error, get_machine, require


class DownloadFileTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        remote = abs_path(require(tool_parameters, "remote_path"), "remote_path")
        get_machine(client, machine_id)
        try:
            data = client.machines.files.download(machine_id, remote)
        except BoxdError as e:
            raise friendly_error(e) from e
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"file is larger than {MAX_FILE_BYTES // 2**20} MiB")
        filename = posixpath.basename(remote) or "download.bin"
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        yield self.create_blob_message(blob=data, meta={"mime_type": mime, "filename": filename})
        yield self.create_json_message(
            {
                "success": True,
                "machine_id": machine_id,
                "remote_path": remote,
                "size_bytes": len(data),
                "mime_type": mime,
                "filename": filename,
            }
        )
