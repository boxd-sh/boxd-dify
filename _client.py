"""Shared helpers for the boxd tools: the client cache, argument parsing and exec."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shlex
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import dify_plugin  # noqa: F401  (applies the runtime's gevent patch first)

GRPC_GEVENT_MODE = False


def _init_grpc_for_gevent() -> None:
    # The Dify plugin runtime patches the standard library with gevent. The boxd
    # SDK speaks gRPC, which has to be switched to gevent mode after that patch
    # and before the first channel exists, or every call blocks the event loop.
    global GRPC_GEVENT_MODE
    from gevent import monkey

    if monkey.is_module_patched("socket") and not GRPC_GEVENT_MODE:
        import grpc.experimental.gevent as grpc_gevent

        grpc_gevent.init_gevent()
        GRPC_GEVENT_MODE = True


_init_grpc_for_gevent()

from boxd import Boxd  # noqa: E402
from boxd.errors import (  # noqa: E402
    AuthenticationError,
    BoxdError,
    ConflictError,
    MachineAgentOutdatedError,
    NotFoundError,
    PermissionDeniedError,
)

# Every machine this plugin creates carries this label. delete_machine only
# deletes machines that have it, and list_machines shows only those by default.
CREATED_BY_LABEL = ("created-by", "dify")
DEFAULT_NAME_PREFIX = "dify-"
REGIONS = ("nyc", "fra")
SIZES = {"1x4": (1, "4G"), "2x8": (2, "8G"), "4x16": (4, "16G"), "8x32": (8, "32G")}
HOME = "/home/boxd"
MAX_OUTPUT_CHARS = 100_000
# Output is cut inside the machine, so a command that prints gigabytes never
# reaches the plugin's memory. Four bytes is the longest UTF-8 character.
MAX_OUTPUT_BYTES = 4 * MAX_OUTPUT_CHARS
MAX_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
READY_TIMEOUT = 120.0
# A machine made for one call is deleted when the call ends. This timer makes
# boxd delete it anyway if the plugin process stops before it gets there.
ONE_OFF_DELETE_AFTER = 3600
# States in which a machine is not running, so a command sent to it has not started.
ASLEEP = ("stopped", "hibernated", "suspended")
SERVICE_DIR = f"{HOME}/.dify-services"
SERVICE_ID_RE = re.compile(r"svc-[0-9a-f]{8}")

_client_cache: dict[str, Boxd] = {}
_client_lock = threading.Lock()


class Settings:
    """The provider credentials, normalised."""

    def __init__(self, credentials: Mapping[str, Any]):
        self.api_key = str(credentials.get("api_key") or "").strip()
        if not self.api_key:
            raise ValueError("A boxd API key is required")
        self.org = str(credentials.get("org") or "").strip() or None
        region = str(credentials.get("region") or "").strip().lower() or None
        if region is not None and region not in REGIONS:
            raise ValueError(f"region must be one of {', '.join(REGIONS)}, got {region!r}")
        self.region = region
        prefix = str(credentials.get("name_prefix") or "").strip() or DEFAULT_NAME_PREFIX
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", prefix):
            raise ValueError("name_prefix may hold lowercase letters, digits and hyphens only")
        self.name_prefix = prefix

    def cache_key(self) -> str:
        parts = [self.api_key, self.org or "", self.region or ""]
        raw = "".join(f"{len(p)}:{p}" for p in parts)
        return hashlib.sha256(raw.encode()).hexdigest()


def build_client(credentials: Mapping[str, Any]) -> tuple[Boxd, Settings]:
    """One client per credential set: the plugin process is long-lived."""
    settings = Settings(credentials)
    key = settings.cache_key()
    with _client_lock:
        client = _client_cache.get(key)
        if client is None:
            client = Boxd(api_key=settings.api_key, region=settings.region)
            _client_cache[key] = client
    return client, settings


def friendly_error(err: Exception) -> ValueError:
    """Turn an SDK error into a short message that never carries the key."""
    if isinstance(err, AuthenticationError):
        return ValueError("boxd rejected the API key. Check it in the plugin settings.")
    if isinstance(err, PermissionDeniedError):
        return ValueError(f"boxd denied the request: {err}")
    if isinstance(err, NotFoundError):
        return ValueError(f"Not found: {err}")
    return ValueError(f"boxd request failed: {err}")


def to_int(value: Any, name: str) -> int:
    # Dify sends number parameters as floats. Reject fractions instead of truncating.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a whole number, got {value!r}")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(f"{name} must be a whole number, got {value!r}") from None
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be a whole number, got {value}")
    return int(value)


def optional_int(params: Mapping[str, Any], name: str) -> int | None:
    value = params.get(name)
    if value is None or value == "":
        return None
    return to_int(value, name)


def timeout_param(params: Mapping[str, Any]) -> int:
    timeout = optional_int(params, "timeout")
    if timeout is None:
        return DEFAULT_TIMEOUT
    if timeout < 1 or timeout > MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    return timeout


def delete_after_param(value: int) -> int:
    if value < 60:
        raise ValueError("delete_after must be at least 60 seconds")
    return value


def parse_json_object(raw: Any, name: str) -> dict[str, str] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, Mapping):
        parsed: Any = raw
    else:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{name} must be a JSON object string: {e}") from None
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return {str(k): str(v) for k, v in parsed.items()}


def require(params: Mapping[str, Any], name: str) -> str:
    value = params.get(name)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{name} is required")
    return str(value).strip()


def abs_path(path: str, name: str = "path") -> str:
    path = path.strip()
    if not path:
        raise ValueError(f"{name} is required")
    if not path.startswith("/"):
        path = f"{HOME}/{path}"
    return path


def new_name(settings: Settings) -> str:
    return f"{settings.name_prefix}{secrets.token_hex(4)}"


def machine_summary(machine: Any) -> dict[str, Any]:
    return {
        "machine_id": machine.id,
        "name": machine.name,
        "status": machine.status,
        "region": getattr(machine, "region", None),
        "vcpu": machine.resources.vcpu,
        "memory_gib": round(machine.resources.memory_bytes / 2**30),
        "labels": dict(machine.labels or {}),
    }


def create_machine(
    client: Boxd,
    settings: Settings,
    *,
    name: str | None = None,
    size: str | None = None,
    labels: Mapping[str, str] | None = None,
    auto_suspend_timeout: int | None = None,
    from_snapshot: str | None = None,
    delete_after: int | None = None,
    on_created: Callable[[Any], None] | None = None,
) -> Any:
    """Create an isolated machine and wait until it runs commands.

    `on_created` gets the machine as soon as it exists, before the wait.
    """
    kwargs: dict[str, Any] = {}
    if size:
        if size not in SIZES:
            raise ValueError(f"size must be one of {', '.join(SIZES)}, got {size!r}")
        kwargs["vcpu"], kwargs["memory"] = SIZES[size]
    if auto_suspend_timeout is not None:
        if auto_suspend_timeout < 0:
            raise ValueError("auto_suspend_timeout must be 0 (off) or more seconds")
        kwargs["auto_suspend_timeout"] = auto_suspend_timeout
    if from_snapshot:
        kwargs["from_snapshot"] = from_snapshot
    if delete_after is not None:
        kwargs["delete_after"] = delete_after_param(delete_after)
    all_labels = dict(labels or {})
    all_labels[CREATED_BY_LABEL[0]] = CREATED_BY_LABEL[1]
    machine = client.machines.create(
        name or new_name(settings),
        org=settings.org,
        isolated=True,
        labels=all_labels,
        **kwargs,
    )
    if on_created is not None:
        on_created(machine)
    wait_ready_or_delete(client, machine)
    return machine


def wait_ready_or_delete(client: Boxd, machine: Any) -> None:
    """Wait until a machine this call just made takes commands. Delete it if it never does.

    Without this a machine that fails to come up would stay behind with nothing
    pointing at it: the caller never got its id.
    """
    try:
        client.machines.wait_until_ready(machine.id, timeout=READY_TIMEOUT)
    except BaseException as e:
        try:
            client.machines.delete(machine.id)
            fate = "It was deleted."
        except Exception:
            fate = "Deleting it failed too, so it still exists: delete it yourself."
        if not isinstance(e, Exception):
            raise
        raise ValueError(f"Machine {machine.name} ({machine.id}) did not become ready: {e}. {fate}") from e


def get_machine(client: Boxd, machine_id: str) -> Any:
    try:
        return client.machines.get(machine_id)
    except NotFoundError:
        raise ValueError(f"Machine '{machine_id}' not found") from None
    except BoxdError as e:
        raise friendly_error(e) from e


def ensure_running(client: Boxd, machine_id: str) -> Any:
    """Bring a stopped, paused or hibernated machine back before using it."""
    machine = get_machine(client, machine_id)
    _bring_up(client, machine_id, machine.status)
    return machine


def _bring_up(client: Boxd, machine_id: str, status: str) -> None:
    if status in ("failed", "destroyed"):
        raise ValueError(f"Machine '{machine_id}' is {status}")
    if status == "running":
        return
    try:
        if status == "stopped":
            client.machines.start(machine_id)
        elif status == "hibernated":
            client.machines.wake(machine_id)
        elif status == "suspended":
            client.machines.resume(machine_id)
        client.machines.wait_until_ready(machine_id, timeout=READY_TIMEOUT)
    except NotFoundError:
        raise ValueError(f"Machine '{machine_id}' not found") from None
    except BoxdError as e:
        raise friendly_error(e) from e
    except TimeoutError as e:
        raise ValueError(f"Machine '{machine_id}' was {status} and did not become ready: {e}") from e


def file_size(client: Boxd, machine_id: str, path: str) -> int:
    """A file's size, asked before any of it is transferred.

    Raises when it is over the limit, so a large file is refused without being
    read into the plugin's memory.
    """
    try:
        size = int(client.machines.fs.stat(machine_id, path).size)
    except MachineAgentOutdatedError:
        # A machine that has run since before boxd had this call: ask with `stat`.
        result = client.machines.exec(machine_id, shlex.join(["stat", "-L", "-c", "%s", "--", path]), timeout=30)
        if result.exit_code != 0:
            raise ValueError(f"Not found: {_text(result.stderr).strip() or path}") from None
        size = int(_text(result.stdout).strip())
    if size > MAX_FILE_BYTES:
        raise ValueError(f"file is larger than {MAX_FILE_BYTES // 2**20} MiB")
    return size


def wait_for_status(client: Boxd, machine_id: str, want: str | None, timeout: float = READY_TIMEOUT) -> str:
    """Poll until the machine reports `want` (a state change settles after the call returns)."""
    status = client.machines.get(machine_id).status
    deadline = time.monotonic() + timeout
    while want and status != want and time.monotonic() < deadline:
        time.sleep(1)
        status = client.machines.get(machine_id).status
    return status


# Runs the command under `timeout` with both streams cut at a byte limit.
# Arguments: seconds, byte limit, tag, command.
#  - Each stream goes through `cap`, which passes the first $1 bytes on and
#    reads the rest away, so the command is never blocked or stopped by the cut.
#    A cut stream ends with "[<tag>:truncated]".
#  - The exit code is the command's own.
#  - "[<tag>:timed-out]" at the end of stderr says the time limit stopped it:
#    `timeout` gives 124 when the command ended on SIGTERM and 137 when it had
#    to be killed five seconds later. A command that was killed earlier for
#    another reason, such as running out of memory, also gives 137, so the
#    clock decides. The shell's own "Killed" notice for `timeout` is dropped.
#  - A process the command leaves in the background keeps the streams open.
#    The script waits a second at most for them, then returns.
_RUN_SCRIPT = r"""
cap() {
  dd bs=65536 iflag=count_bytes count="$1" status=none
  if [ "$(dd bs=1 count=1 status=none | wc -c)" -gt 0 ]; then
    printf '\n[%s:truncated]' "$2"
    cat > /dev/null
  fi
}
exec 3> >(cap "$2" "$3")
out=$!
exec 4> >(cap "$2" "$3" >&2 3>&-)
err=$!
start=$(date +%s%N)
{
  timeout -k 5 "$1" bash -c "$4" >&3 2>&4 3>&- 4>&-
  code=$?
} 2> /dev/null
ms=$(( ($(date +%s%N) - start) / 1000000 ))
exec 3>&- 4>&-
for _ in 1 2 3 4 5 6 7 8 9 10; do
  kill -0 "$out" 2>/dev/null || kill -0 "$err" 2>/dev/null || break
  sleep 0.1
done
if { [ "$code" -eq 124 ] && [ "$ms" -ge $(($1 * 1000)) ]; } ||
   { [ "$code" -eq 137 ] && [ "$ms" -ge $((($1 + 5) * 1000)) ]; }; then
  printf '\n[%s:timed-out]' "$3" >&2
fi
exit "$code"
"""


def run(
    client: Boxd,
    machine_id: str,
    command: str,
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run a shell command and return stdout, stderr and the exit code.

    The command runs under `timeout`, so it is killed inside the machine when the
    limit is reached, not only abandoned by the caller. Its output is cut inside
    the machine too (see `_RUN_SCRIPT`).
    """
    tag = f"boxd-dify-{secrets.token_hex(8)}"
    wrapped = shlex.join(
        ["bash", "-c", _RUN_SCRIPT, "boxd-dify", str(int(timeout)), str(MAX_OUTPUT_BYTES), tag, command]
    )
    kwargs: dict[str, Any] = {"timeout": timeout + 30}
    if cwd:
        kwargs["cwd"] = abs_path(cwd, "cwd")
    if env:
        kwargs["env"] = dict(env)
    try:
        try:
            result = client.machines.exec(machine_id, wrapped, **kwargs)
        except ConflictError as e:
            # Only "the machine is not running" is safe to answer by running the
            # command again: it never started. Any other conflict may come after
            # the command ran, so it is reported and the command is not repeated.
            if e.code not in (None, "failed_precondition") or isinstance(e, OSError):
                raise
            status = get_machine(client, machine_id).status
            if status not in ASLEEP:
                raise
            _bring_up(client, machine_id, status)
            result = client.machines.exec(machine_id, wrapped, **kwargs)
    except NotFoundError:
        raise ValueError(f"Machine '{machine_id}' not found") from None
    except BoxdError as e:
        raise friendly_error(e) from e
    stderr, timed_out = _cut_marker(_text(result.stderr), f"\n[{tag}:timed-out]")
    stderr, err_cut = _cut_marker(stderr, f"\n[{tag}:truncated]")
    stdout, out_cut = _cut_marker(_text(result.stdout), f"\n[{tag}:truncated]")
    stdout, out_clipped = _clip(stdout)
    stderr, err_clipped = _clip(stderr)
    return {
        "exit_code": result.exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "truncated": out_cut or err_cut or out_clipped or err_clipped,
        "machine_id": machine_id,
    }


def _text(data: Any) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def _cut_marker(text: str, marker: str) -> tuple[str, bool]:
    if text.endswith(marker):
        return text[: -len(marker)], True
    return text, False


def _clip(text: str) -> tuple[str, bool]:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS], True
    return text, False


def format_output(result: Mapping[str, Any]) -> str:
    parts = []
    if result["stdout"]:
        parts.append(result["stdout"].rstrip("\n"))
    if result["stderr"]:
        parts.append("[stderr]\n" + result["stderr"].rstrip("\n"))
    if result["timed_out"]:
        parts.append("[timed out]")
    parts.append(f"[exit code {result['exit_code']}]")
    return "\n".join(parts)


class Ephemeral:
    """A machine for one call: created when no machine_id is given, deleted after."""

    def __init__(self, client: Boxd, settings: Settings, machine_id: str | None):
        self.client = client
        self.settings = settings
        self.machine_id = machine_id or None
        self.created = False

    def __enter__(self) -> str:
        if self.machine_id:
            get_machine(self.client, self.machine_id)
            return self.machine_id
        try:
            machine = create_machine(
                self.client, self.settings, delete_after=ONE_OFF_DELETE_AFTER, on_created=self._remember
            )
        except BoxdError as e:
            raise friendly_error(e) from e
        return machine.id

    def _remember(self, machine: Any) -> None:
        # Set before the ready-wait: from here on the machine is this call's to delete.
        self.machine_id = machine.id
        self.created = True

    def __exit__(self, *exc: Any) -> None:
        if self.created and self.machine_id:
            try:
                self.client.machines.delete(self.machine_id)
            except Exception:
                pass
