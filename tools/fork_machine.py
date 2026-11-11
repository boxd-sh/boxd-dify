from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import (
    CREATED_BY_LABEL,
    build_client,
    delete_after_param,
    ensure_running,
    friendly_error,
    machine_summary,
    new_name,
    optional_int,
    require,
    wait_ready_or_delete,
)


class ForkMachineTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, settings = build_client(self.runtime.credentials)
        source_id = require(tool_parameters, "machine_id")
        name = str(tool_parameters.get("name") or "").strip() or new_name(settings)
        kwargs: dict[str, Any] = {}
        delete_after = optional_int(tool_parameters, "delete_after")
        if delete_after is not None:
            kwargs["delete_after"] = delete_after_param(delete_after)
        source = ensure_running(client, source_id)
        labels = dict(source.labels or {})
        labels[CREATED_BY_LABEL[0]] = CREATED_BY_LABEL[1]
        try:
            fork = client.machines.fork(source_id, name, isolated=True, labels=labels, **kwargs)
        except BoxdError as e:
            raise friendly_error(e) from e
        wait_ready_or_delete(client, fork)
        try:
            fork = client.machines.get(fork.id)
        except BoxdError as e:
            raise friendly_error(e) from e
        yield self.create_text_message(f"Forked {source.name} into {fork.name} ({fork.id}).")
        yield self.create_json_message({**machine_summary(fork), "source_machine_id": source_id})
        yield self.create_variable_message("machine_id", fork.id)
