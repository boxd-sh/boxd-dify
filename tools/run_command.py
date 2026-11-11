from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import Ephemeral, build_client, format_output, parse_json_object, require, run, timeout_param


class RunCommandTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, settings = build_client(self.runtime.credentials)
        command = require(tool_parameters, "command")
        cwd = str(tool_parameters.get("cwd") or "").strip() or None
        env = parse_json_object(tool_parameters.get("env_vars"), "env_vars")
        timeout = timeout_param(tool_parameters)
        machine_id = str(tool_parameters.get("machine_id") or "").strip() or None
        eph = Ephemeral(client, settings, machine_id)
        with eph as mid:
            result = run(client, mid, command, cwd=cwd, env=env, timeout=timeout)
        result["ephemeral"] = eph.created
        yield self.create_text_message(format_output(result))
        yield self.create_json_message(result)
        yield self.create_variable_message("stdout", result["stdout"])
        yield self.create_variable_message("stderr", result["stderr"])
        yield self.create_variable_message("exit_code", result["exit_code"])
