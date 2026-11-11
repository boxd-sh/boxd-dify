import secrets
import shlex
from collections.abc import Generator
from typing import Any

from boxd.errors import BoxdError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from _client import (
    Ephemeral,
    build_client,
    format_output,
    friendly_error,
    parse_json_object,
    require,
    run,
    timeout_param,
)

RUNNERS = {
    "python": ("py", "python3"),
    "javascript": ("mjs", "node"),
    "typescript": ("ts", "bun run"),
    "bash": ("sh", "bash"),
}


class RunCodeTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        client, settings = build_client(self.runtime.credentials)
        code = require(tool_parameters, "code")
        language = str(tool_parameters.get("language") or "python").strip().lower()
        if language not in RUNNERS:
            raise ValueError(f"language must be one of {', '.join(RUNNERS)}, got {language!r}")
        env = parse_json_object(tool_parameters.get("env_vars"), "env_vars")
        timeout = timeout_param(tool_parameters)
        machine_id = str(tool_parameters.get("machine_id") or "").strip() or None
        ext, runner = RUNNERS[language]
        path = f"/tmp/dify-code-{secrets.token_hex(6)}.{ext}"
        eph = Ephemeral(client, settings, machine_id)
        with eph as mid:
            try:
                client.machines.files.upload(mid, path, code)
            except BoxdError as e:
                raise friendly_error(e) from e
            quoted = shlex.quote(path)
            command = f"{runner} {quoted}; status=$?; rm -f {quoted}; exit $status"
            result = run(client, mid, command, env=env, timeout=timeout)
        result["language"] = language
        result["ephemeral"] = eph.created
        yield self.create_text_message(format_output(result))
        yield self.create_json_message(result)
        yield self.create_variable_message("stdout", result["stdout"])
        yield self.create_variable_message("stderr", result["stderr"])
        yield self.create_variable_message("exit_code", result["exit_code"])
