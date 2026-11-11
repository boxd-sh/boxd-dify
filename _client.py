"""Shared helpers for the boxd tools: the client cache, argument parsing and exec."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shlex
import threading
import time
from collections.abc import Mapping
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
MAX_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
READY_TIMEOUT = 120.0
# A machine made for one call is deleted when the call ends. This timer makes
# boxd delete it anyway if the plugin process stops before it gets there.
ONE_OFF_DELETE_AFTER = 3600
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
) -> Any:
    """Create an isolated machine and wait until it runs commands."""
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
    client.machines.wait_until_ready(machine.id, timeout=READY_TIMEOUT)
    return machine


def get_machine(client: Boxd, machine_id: str) -> Any:
    try:
        return client.machines.get(machine_id)
    except NotFoundError:
        raise ValueError(f"Machine '{machine_id}' not found") from None
    except BoxdError as e:
        raise friendly_error(e) from e


def ensure_running(client: Boxd, machine_id: str) -> None:
    """Bring a stopped, paused or hibernated machine back before using it."""
    machine = get_machine(client, machine_id)
    status = machine.status
    if status == "stopped":
        client.machines.start(machine_id)
    elif status == "hibernated":
        client.machines.wake(machine_id)
    elif status == "suspended":
        client.machines.resume(machine_id)
    elif status in ("failed", "destroyed"):
        raise ValueError(f"Machine '{machine_id}' is {status}")
    if status != "running":
        client.machines.wait_until_ready(machine_id, timeout=READY_TIMEOUT)


def wait_for_status(client: Boxd, machine_id: str, want: str | None, timeout: float = READY_TIMEOUT) -> str:
    """Poll until the machine reports `want` (a state change settles after the call returns)."""
    status = client.machines.get(machine_id).status
    deadline = time.monotonic() + timeout
    while want and status != want and time.monotonic() < deadline:
        time.sleep(1)
        status = client.machines.get(machine_id).status
    return status


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
    limit is reached, not only abandoned by the caller.
    """
    wrapped = f"timeout -k 5 {int(timeout)} bash -c {shlex.quote(command)}"
    kwargs: dict[str, Any] = {"timeout": timeout + 30}
    if cwd:
        kwargs["cwd"] = abs_path(cwd, "cwd")
    if env:
        kwargs["env"] = dict(env)
    try:
        result = client.machines.exec(machine_id, wrapped, **kwargs)
    except ConflictError:
        ensure_running(client, machine_id)
        result = client.machines.exec(machine_id, wrapped, **kwargs)
    except NotFoundError:
        raise ValueError(f"Machine '{machine_id}' not found") from None
    except BoxdError as e:
        raise friendly_error(e) from e
    stdout, out_cut = _clip(result.stdout)
    stderr, err_cut = _clip(result.stderr)
    return {
        "exit_code": result.exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": result.exit_code in (124, 137),
        "truncated": out_cut or err_cut,
        "machine_id": machine_id,
    }


def _clip(text: Any) -> tuple[str, bool]:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = text or ""
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
            machine = create_machine(self.client, self.settings, delete_after=ONE_OFF_DELETE_AFTER)
        except BoxdError as e:
            raise friendly_error(e) from e
        self.machine_id = machine.id
        self.created = True
        return machine.id

    def __exit__(self, *exc: Any) -> None:
        if self.created and self.machine_id:
            try:
                self.client.machines.delete(self.machine_id)
            except Exception:
                pass
