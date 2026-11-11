from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError, ConflictError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import build_client, ensure_running, friendly_error, require, to_int


class GetPreviewUrlTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, _ = build_client(self.runtime.credentials)
        machine_id = require(tool_parameters, "machine_id")
        port = to_int(tool_parameters.get("port"), "port")
        if port < 1 or port > 65535:
            raise ValueError("port must be between 1 and 65535")
        machine = ensure_running(client, machine_id)
        route = f"p{port}"
        try:
            proxy = self._find(client, machine_id, route)
            if proxy is None:
                try:
                    client.machines.proxies.create(machine_id, route, port)
                except ConflictError:
                    pass
                proxy = self._find(client, machine_id, route)
        except BoxdError as e:
            raise friendly_error(e) from e
        if proxy is None:
            raise ValueError(f"Could not create a public URL for port {port} on {machine.name}")
        url = f"https://{proxy.domain}"
        yield self.create_text_message(f"Port {port} on {machine.name} is public at {url}")
        yield self.create_json_message({"url": url, "port": port, "machine_id": machine_id})
        yield self.create_variable_message("url", url)

    @staticmethod
    def _find(client: Any, machine_id: str, route: str) -> Any:
        for proxy in client.machines.proxies.list(machine_id):
            if proxy.name == route:
                return proxy
        return None
