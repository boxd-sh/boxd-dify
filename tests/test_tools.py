import sys
import time

import pytest
from boxd.errors import AuthenticationError, BoxdError
from boxd.models import ExecResult
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

import _client
from conftest import CREDS, invoke, make_machine, unwrap
from provider.boxd import BoxdProvider
from tools.create_machine import CreateMachineTool
from tools.delete_machine import DeleteMachineTool
from tools.download_file import DownloadFileTool
from tools.fork_machine import ForkMachineTool
from tools.get_preview_url import GetPreviewUrlTool
from tools.get_service_logs import GetServiceLogsTool
from tools.list_files import ListFilesTool
from tools.list_machines import ListMachinesTool
from tools.manage_machine import ManageMachineTool
from tools.read_file import ReadFileTool
from tools.run_code import RunCodeTool
from tools.run_command import RunCommandTool
from tools.start_service import StartServiceTool
from tools.upload_file import UploadFileTool
from tools.write_file import WriteFileTool


def created(fake):
    return [c for c in fake.calls if c[0][0] == "create"]


def execs(fake):
    return [c for c in fake.calls if c[0][0] == "exec"]


def test_create_machine_is_always_isolated_and_labelled(fake):
    r = invoke(CreateMachineTool, {"name": "box1", "size": "4x16", "labels": '{"team": "a"}'})
    ((args, kwargs),) = created(fake)
    assert args[1] == "box1"
    assert kwargs["isolated"] is True
    assert kwargs["org"] == "acme"
    assert kwargs["vcpu"] == 4 and kwargs["memory"] == "16G"
    assert kwargs["labels"] == {"team": "a", "created-by": "dify"}
    assert "wait_until_ready" in fake.names()
    assert r.json["machine_id"] == "id-1"


def test_create_machine_rejects_unknown_size(fake):
    with pytest.raises(ValueError, match="size"):
        invoke(CreateMachineTool, {"size": "3x12"})


def test_create_machine_generates_name_from_prefix(fake):
    invoke(CreateMachineTool, {}, creds={**CREDS, "name_prefix": "t-dify-"})
    ((args, _),) = created(fake)
    assert args[1].startswith("t-dify-") and len(args[1]) == len("t-dify-") + 8


def test_fractional_numbers_are_rejected(fake):
    with pytest.raises(ValueError, match="whole number"):
        invoke(CreateMachineTool, {"auto_suspend_timeout": 0.5})


def test_run_command_without_machine_creates_and_deletes(fake):
    fake.exec_results = [ExecResult(stdout="out\n", stderr="err\n", exit_code=3)]
    r = invoke(RunCommandTool, {"command": "echo out; echo err >&2; exit 3"})
    assert r.json["exit_code"] == 3
    assert r.json["stdout"] == "out\n" and r.json["stderr"] == "err\n"
    assert r.json["ephemeral"] is True
    names = fake.names()
    assert names.index("create") < names.index("exec") < names.index("delete")
    ((_, kwargs),) = created(fake)
    assert kwargs["isolated"] is True


def test_run_command_deletes_ephemeral_machine_when_exec_fails(fake):
    fake.exec_results = []

    def boom(*a, **k):
        raise RuntimeError("network down")

    fake.exec = boom
    with pytest.raises(RuntimeError):
        invoke(RunCommandTool, {"command": "true"})
    assert "delete" in fake.names()


def test_run_command_on_existing_machine_keeps_it(fake):
    fake.machines["m1"] = make_machine("m1", "keep")
    r = invoke(RunCommandTool, {"command": "ls", "machine_id": "m1", "cwd": "work", "env_vars": '{"A": 1}'})
    assert "delete" not in fake.names() and "create" not in fake.names()
    ((args, kwargs),) = [c for c in fake.calls if c[0][0] == "exec"]
    assert kwargs["cwd"] == "/home/boxd/work"
    assert kwargs["env"] == {"A": "1"}
    assert r.json["ephemeral"] is False


def test_run_command_wraps_in_timeout_so_the_process_is_killed(fake):
    fake.machines["m1"] = make_machine("m1", "keep")
    fake.exec_results = [lambda tag: ExecResult(stdout="", stderr=f"\n[{tag}:timed-out]", exit_code=124)]
    r = invoke(RunCommandTool, {"command": "sleep 99", "machine_id": "m1", "timeout": 5})
    ((args, kwargs),) = execs(fake)
    sent = unwrap(args[2])
    assert (sent.seconds, sent.command) == (5, "sleep 99")
    assert 'timeout -k 5 "$1" bash -c "$4"' in _client._RUN_SCRIPT
    assert kwargs["timeout"] == 35
    assert r.json["timed_out"] is True and r.json["stderr"] == ""


def test_run_command_rejects_timeout_out_of_range(fake):
    with pytest.raises(ValueError, match="timeout"):
        invoke(RunCommandTool, {"command": "true", "timeout": 0})


def test_exec_on_stopped_machine_starts_it_and_retries(fake):
    fake.machines["m1"] = make_machine("m1", "s", status="stopped")
    fake.conflict_once = True
    r = invoke(RunCommandTool, {"command": "echo hi", "machine_id": "m1"})
    names = fake.names()
    assert names.count("exec") == 2
    assert names.index("start") < names.index("wait_until_ready")
    assert r.json["exit_code"] == 0


def test_exec_on_hibernated_machine_wakes_it(fake):
    fake.machines["m1"] = make_machine("m1", "h", status="hibernated")
    fake.conflict_once = True
    invoke(RunCommandTool, {"command": "echo hi", "machine_id": "m1"})
    assert "wake" in fake.names()


def test_unknown_machine_gives_clear_error(fake):
    with pytest.raises(ValueError, match="not found"):
        invoke(RunCommandTool, {"command": "true", "machine_id": "nope"})


def test_long_output_is_clipped(fake):
    fake.machines["m1"] = make_machine("m1", "k")
    fake.exec_results = [ExecResult(stdout="x" * (_client.MAX_OUTPUT_CHARS + 10), stderr="", exit_code=0)]
    r = invoke(RunCommandTool, {"command": "yes", "machine_id": "m1"})
    assert r.json["truncated"] is True and len(r.json["stdout"]) == _client.MAX_OUTPUT_CHARS


@pytest.mark.parametrize(
    "language,runner", [("python", "python3"), ("javascript", "node"), ("typescript", "bun run"), ("bash", "bash")]
)
def test_run_code_uploads_and_runs(fake, language, runner):
    fake.machines["m1"] = make_machine("m1", "k")
    r = invoke(RunCodeTool, {"code": "print(1)", "language": language, "machine_id": "m1"})
    (path_key,) = fake.files.store
    assert path_key[1].startswith("/tmp/dify-code-")
    ((args, _),) = [c for c in fake.calls if c[0][0] == "exec"]
    assert f"{runner} " in args[2] and "rm -f" in args[2]
    assert r.json["language"] == language


def test_run_code_rejects_unknown_language(fake):
    with pytest.raises(ValueError, match="language"):
        invoke(RunCodeTool, {"code": "x", "language": "cobol"})


def test_delete_refuses_machines_not_created_by_plugin(fake):
    fake.machines["m1"] = make_machine("m1", "prod-db")
    with pytest.raises(ValueError, match="not created by this plugin"):
        invoke(DeleteMachineTool, {"machine_id": "m1"})
    assert "delete" not in fake.names()


def test_delete_removes_plugin_machine(fake):
    fake.machines["m1"] = make_machine("m1", "x", labels={"created-by": "dify"})
    r = invoke(DeleteMachineTool, {"machine_id": "m1"})
    assert r.json["success"] and "m1" not in fake.machines


def test_list_machines_filters_by_label_unless_all(fake):
    fake.machines["a"] = make_machine("a", "mine", labels={"created-by": "dify"})
    fake.machines["b"] = make_machine("b", "other")
    assert invoke(ListMachinesTool, {}).json["count"] == 1
    assert invoke(ListMachinesTool, {"include_all": True}).json["count"] == 2


@pytest.mark.parametrize(
    "action,status", [("stop", "stopped"), ("hibernate", "hibernated"), ("wake", "running"), ("start", "running")]
)
def test_manage_machine(fake, action, status):
    fake.machines["m1"] = make_machine("m1", "x")
    r = invoke(ManageMachineTool, {"machine_id": "m1", "action": action})
    assert r.json["status"] == status


def test_manage_machine_rejects_unknown_action(fake):
    fake.machines["m1"] = make_machine("m1", "x")
    with pytest.raises(ValueError, match="action"):
        invoke(ManageMachineTool, {"machine_id": "m1", "action": "explode"})


def test_fork_is_isolated_and_labelled(fake):
    fake.machines["m1"] = make_machine("m1", "src", labels={"team": "a"})
    r = invoke(ForkMachineTool, {"machine_id": "m1", "name": "copy"})
    ((args, kwargs),) = [c for c in fake.calls if c[0][0] == "fork"]
    assert args[1:] == ("m1", "copy")
    assert kwargs["isolated"] is True
    assert kwargs["labels"] == {"team": "a", "created-by": "dify"}
    assert r.json["source_machine_id"] == "m1"


def test_write_read_download_round_trip(fake):
    fake.machines["m1"] = make_machine("m1", "x")
    invoke(WriteFileTool, {"machine_id": "m1", "remote_path": "notes/a.txt", "content": "héllo"})
    r = invoke(ReadFileTool, {"machine_id": "m1", "remote_path": "/home/boxd/notes/a.txt"})
    assert r.json["content"] == "héllo"
    d = invoke(DownloadFileTool, {"machine_id": "m1", "remote_path": "/home/boxd/notes/a.txt"})
    assert d.blobs and d.json["mime_type"] == "text/plain" and d.json["size_bytes"] == 6


def test_read_file_refuses_binary(fake):
    fake.machines["m1"] = make_machine("m1", "x")
    fake.files.store[("m1", "/tmp/b")] = b"\xff\xfe\x00"
    with pytest.raises(ValueError, match="not a UTF-8"):
        invoke(ReadFileTool, {"machine_id": "m1", "remote_path": "/tmp/b"})


def test_list_files_sorts_dirs_first(fake):
    fake.machines["m1"] = make_machine("m1", "x")
    r = invoke(ListFilesTool, {"machine_id": "m1"})
    assert r.json["path"] == "/home/boxd"
    assert [e["name"] for e in r.json["entries"]] == ["a", "b.txt"]


def test_start_service_and_logs(fake):
    fake.machines["m1"] = make_machine("m1", "x")
    fake.exec_results = [ExecResult(stdout="running\n", stderr="", exit_code=0)]
    r = invoke(
        StartServiceTool, {"machine_id": "m1", "command": "python3 -m http.server 8000", "env_vars": '{"X": "1"}'}
    )
    sid = r.json["service_id"]
    assert r.json["running"] is True
    ((args, _),) = [c for c in fake.calls if c[0][0] == "exec"]
    assert "setsid nohup" in args[2] and "export X=1" in args[2]
    # the service records its own pid before exec, so the pid file names the real process
    assert "echo $$ > /home/boxd/.dify-services/svc-" in args[2]
    assert args[2].index("mkdir -p") < args[2].index("setsid")
    fake.exec_results = [ExecResult(stdout="RUNNING\nServing HTTP\n", stderr="", exit_code=0)]
    logs = invoke(GetServiceLogsTool, {"machine_id": "m1", "service_id": sid})
    assert logs.json["running"] is True and "Serving HTTP" in logs.json["logs"]


def test_service_logs_rejects_bad_service_id(fake):
    with pytest.raises(ValueError, match="service_id"):
        invoke(GetServiceLogsTool, {"machine_id": "m1", "service_id": "../../etc/passwd"})


def test_preview_url_creates_route_once(fake):
    fake.machines["m1"] = make_machine("m1", "web")
    a = invoke(GetPreviewUrlTool, {"machine_id": "m1", "port": 8080})
    b = invoke(GetPreviewUrlTool, {"machine_id": "m1", "port": 8080.0})
    assert a.json["url"] == b.json["url"] == "https://p8080.m-m1.boxd.sh"
    assert len(fake.proxies.routes) == 1


def test_preview_url_rejects_bad_port(fake):
    fake.machines["m1"] = make_machine("m1", "web")
    with pytest.raises(ValueError, match="port"):
        invoke(GetPreviewUrlTool, {"machine_id": "m1", "port": 70000})


def test_client_is_cached_per_credentials(fake):
    a, _ = _client.build_client(CREDS)
    b, _ = _client.build_client(dict(CREDS))
    c, _ = _client.build_client({**CREDS, "region": "fra"})
    assert a is b and a is not c


def test_bad_region_and_missing_key(fake):
    with pytest.raises(ValueError, match="region"):
        _client.build_client({**CREDS, "region": "mars"})
    with pytest.raises(ValueError, match="API key"):
        _client.build_client({"api_key": ""})


def test_provider_validation_maps_auth_error_without_leaking_key(fake, monkeypatch):
    def denied(**kwargs):
        raise AuthenticationError("invalid token fake-test-key-123")

    fake.list = denied
    with pytest.raises(ToolProviderCredentialValidationError) as e:
        BoxdProvider()._validate_credentials(CREDS)
    assert "fake-test-key-123" not in str(e.value)
    assert "API key" in str(e.value)


def test_provider_validation_ok(fake):
    BoxdProvider()._validate_credentials(CREDS)
    assert fake.calls[-1] == (("list",), {"org": "acme", "labels": {"created-by": "dify"}})


def test_grpc_gevent_mode_is_enabled_under_dify_runtime():
    from gevent import monkey

    assert monkey.is_module_patched("socket")
    assert _client.GRPC_GEVENT_MODE is True


def test_manage_machine_waits_until_hibernated(fake, monkeypatch):
    fake.machines["m1"] = make_machine("m1", "x")
    states = iter(["suspended", "suspended", "hibernated"])
    real_get = fake.get

    def get(mid):
        m = real_get(mid)
        if "hibernate" in fake.names():
            m.status = next(states, "hibernated")
        return m

    fake.get = get
    monkeypatch.setattr(_client.time, "sleep", lambda s: None)
    r = invoke(ManageMachineTool, {"machine_id": "m1", "action": "hibernate"})
    assert r.json["status"] == "hibernated"


def test_create_machine_has_no_create_time_env(fake):
    invoke(CreateMachineTool, {})
    ((_, kwargs),) = created(fake)
    assert "env" not in kwargs


def variables(result):
    return {m.message.variable_name: m.message.variable_value for m in result.msgs if m.type.value == "variable"}


def test_create_machine_passes_delete_after_and_returns_machine_id_variable(fake):
    r = invoke(CreateMachineTool, {"delete_after": 3600.0})
    ((_, kwargs),) = created(fake)
    assert kwargs["delete_after"] == 3600
    assert variables(r) == {"machine_id": "id-1"}


def test_create_machine_without_delete_after_sets_no_timer(fake):
    invoke(CreateMachineTool, {})
    ((_, kwargs),) = created(fake)
    assert "delete_after" not in kwargs


def test_delete_after_under_a_minute_is_rejected(fake):
    with pytest.raises(ValueError, match="at least 60"):
        invoke(CreateMachineTool, {"delete_after": 30})
    fake.machines["m1"] = make_machine("m1", "src")
    with pytest.raises(ValueError, match="at least 60"):
        invoke(ForkMachineTool, {"machine_id": "m1", "delete_after": 5})
    assert not [c for c in fake.calls if c[0][0] in ("create", "fork")]


def test_fork_passes_delete_after_and_returns_machine_id_variable(fake):
    fake.machines["m1"] = make_machine("m1", "src")
    r = invoke(ForkMachineTool, {"machine_id": "m1", "delete_after": 600})
    ((_, kwargs),) = [c for c in fake.calls if c[0][0] == "fork"]
    assert kwargs["delete_after"] == 600
    assert variables(r) == {"machine_id": r.json["machine_id"]}


def test_one_off_machine_gets_a_delete_after_safety_net(fake):
    invoke(RunCommandTool, {"command": "true"})
    ((_, kwargs),) = created(fake)
    assert kwargs["delete_after"] == _client.ONE_OFF_DELETE_AFTER


def test_run_command_returns_output_variables(fake):
    fake.machines["m1"] = make_machine("m1", "x")
    fake.exec_results = [ExecResult(stdout="out\n", stderr="err\n", exit_code=3)]
    r = invoke(RunCommandTool, {"command": "x", "machine_id": "m1"})
    assert variables(r) == {"stdout": "out\n", "stderr": "err\n", "exit_code": 3}


def test_tool_yaml_output_schema_matches_the_variables(fake):
    import yaml

    from conftest import ROOT

    declared = {}
    for path in sorted((ROOT / "tools").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        declared[path.stem] = set((doc.get("output_schema") or {}).get("properties", {}))
    assert declared["create_machine"] == declared["fork_machine"] == {"machine_id"}
    assert declared["run_command"] == declared["run_code"] == {"stdout", "stderr", "exit_code"}
    assert declared["get_preview_url"] == {"url"}
    assert declared["start_service"] == {"service_id"}


# A command must never run twice


def test_conflict_on_a_running_machine_is_not_retried(fake):
    fake.machines["m1"] = make_machine("m1", "busy")
    fake.conflict_once = True
    with pytest.raises(ValueError, match="boxd request failed"):
        invoke(RunCommandTool, {"command": "charge-the-card", "machine_id": "m1"})
    names = fake.names()
    assert names.count("exec") == 1
    assert not {"start", "wake", "resume", "wait_until_ready"} & set(names)


@pytest.mark.parametrize("code", ["aborted", "already_exists"])
def test_conflict_that_is_not_about_a_sleeping_machine_is_not_retried(fake, code):
    # the machine went to sleep after the command was accepted: it may have run
    fake.machines["m1"] = make_machine("m1", "s", status="stopped")
    fake.conflict_once = True
    fake.conflict_code = code
    with pytest.raises(ValueError, match="boxd request failed"):
        invoke(RunCommandTool, {"command": "charge-the-card", "machine_id": "m1"})
    assert fake.names().count("exec") == 1 and "start" not in fake.names()


def test_conflict_on_a_machine_that_is_starting_is_not_retried(fake):
    fake.machines["m1"] = make_machine("m1", "s", status="starting")
    fake.conflict_once = True
    with pytest.raises(ValueError, match="boxd request failed"):
        invoke(RunCommandTool, {"command": "charge-the-card", "machine_id": "m1"})
    assert fake.names().count("exec") == 1


def test_exec_on_paused_machine_resumes_it_and_runs_once_more(fake):
    fake.machines["m1"] = make_machine("m1", "p", status="suspended")
    fake.conflict_once = True
    invoke(RunCommandTool, {"command": "echo hi", "machine_id": "m1"})
    names = fake.names()
    assert names.count("exec") == 2 and names.count("resume") == 1


def test_second_failure_after_a_wake_is_reported(fake):
    fake.machines["m1"] = make_machine("m1", "s", status="stopped")
    real_exec = fake.exec

    def always_conflict(mid, command, **kwargs):
        fake.conflict_once = True
        return real_exec(mid, command, **kwargs)

    fake.exec = always_conflict
    with pytest.raises(ValueError, match="boxd request failed"):
        invoke(RunCommandTool, {"command": "echo hi", "machine_id": "m1"})
    assert fake.names().count("exec") == 2


# A machine that does not become ready is not left behind


def test_create_machine_deletes_the_machine_when_it_never_becomes_ready(fake):
    fake.ready_error = TimeoutError("machine id-1 did not reach 'running' within 120s")
    with pytest.raises(ValueError, match=r"\(id-1\) did not become ready.*It was deleted"):
        invoke(CreateMachineTool, {"name": "slow"})
    assert ("delete", "id-1") in [c[0] for c in fake.calls]
    assert fake.machines == {}


def test_create_machine_error_names_the_machine_when_the_delete_fails_too(fake):
    fake.ready_error = BoxdError("machine id-1 entered terminal state 'failed' while waiting")
    fake.delete_error = BoxdError("unavailable")
    with pytest.raises(ValueError) as e:
        invoke(CreateMachineTool, {"name": "slow"})
    assert "slow (id-1)" in str(e.value) and "still exists" in str(e.value)


def test_one_off_machine_is_marked_created_before_the_ready_wait_and_deleted(fake):
    fake.ready_error = TimeoutError("not ready")
    client, settings = _client.build_client(CREDS)
    eph = _client.Ephemeral(client, settings, None)
    seen = {}
    real_wait = fake.wait_until_ready

    def wait(mid, timeout=None):
        seen.update(created=eph.created, machine_id=eph.machine_id)
        return real_wait(mid, timeout=timeout)

    fake.wait_until_ready = wait
    with pytest.raises(ValueError, match="did not become ready"):
        with eph:
            raise AssertionError("the body must not run")
    assert seen == {"created": True, "machine_id": "id-1"}
    assert eph.created is True and eph.machine_id == "id-1"
    assert fake.machines == {} and fake.names().count("delete") == 1


def test_run_command_leaves_no_machine_when_the_one_off_machine_never_becomes_ready(fake):
    fake.ready_error = TimeoutError("not ready")
    with pytest.raises(ValueError, match="did not become ready"):
        invoke(RunCommandTool, {"command": "true"})
    assert fake.machines == {} and "exec" not in fake.names()


def test_fork_deletes_the_copy_when_it_never_becomes_ready_and_keeps_the_source(fake):
    fake.machines["m1"] = make_machine("m1", "src")
    fake.ready_error = TimeoutError("not ready")
    with pytest.raises(ValueError, match=r"\(id-1\) did not become ready"):
        invoke(ForkMachineTool, {"machine_id": "m1", "name": "copy"})
    assert list(fake.machines) == ["m1"]
    assert [c[0] for c in fake.calls if c[0][0] == "delete"] == [("delete", "id-1")]


# Output is cut inside the machine


def test_run_sends_the_byte_limit_and_a_fresh_tag(fake):
    fake.machines["m1"] = make_machine("m1", "k")
    invoke(RunCommandTool, {"command": "yes", "machine_id": "m1"})
    invoke(RunCommandTool, {"command": "yes", "machine_id": "m1"})
    a, b = (unwrap(c[0][2]) for c in execs(fake))
    assert a.limit == _client.MAX_OUTPUT_BYTES == 400_000
    assert a.tag != b.tag and a.tag.startswith("boxd-dify-")


def test_truncation_marker_from_the_machine_sets_truncated_and_is_removed(fake):
    fake.machines["m1"] = make_machine("m1", "k")
    fake.exec_results = [
        lambda tag: ExecResult(stdout=f"abc\n[{tag}:truncated]", stderr=f"warn\n[{tag}:truncated]", exit_code=2)
    ]
    r = invoke(RunCommandTool, {"command": "x", "machine_id": "m1"})
    assert (r.json["stdout"], r.json["stderr"]) == ("abc", "warn")
    assert r.json["truncated"] is True and r.json["exit_code"] == 2


def test_marker_text_printed_by_the_command_itself_is_left_alone(fake):
    fake.machines["m1"] = make_machine("m1", "k")
    forged = "\n[boxd-dify-0000000000000000:truncated]"
    fake.exec_results = [
        ExecResult(stdout="abc" + forged, stderr=forged.replace("truncated", "timed-out"), exit_code=0)
    ]
    r = invoke(RunCommandTool, {"command": "x", "machine_id": "m1"})
    assert r.json["stdout"].endswith(forged)
    assert r.json["truncated"] is False and r.json["timed_out"] is False


linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="runs the wrapper in a local GNU shell")


@linux_only
def test_output_is_cut_before_it_reaches_the_plugin_and_the_exit_code_is_kept(fake, monkeypatch):
    monkeypatch.setattr(_client, "MAX_OUTPUT_BYTES", 2000)
    fake.machines["m1"] = make_machine("m1", "k")
    fake.real_shell = True
    command = "head -c 3000000 /dev/zero | tr '\\0' x; head -c 5000 /dev/zero | tr '\\0' e >&2; exit 7"
    r = invoke(RunCommandTool, {"command": command, "machine_id": "m1"})
    (raw,) = fake.raw_results
    # what crossed the wire is the limit plus the short marker, not three megabytes
    assert len(raw.stdout) < 2100 and len(raw.stderr) < 2100
    assert r.json["stdout"] == "x" * 2000 and r.json["stderr"] == "e" * 2000
    assert r.json["truncated"] is True and r.json["exit_code"] == 7 and r.json["timed_out"] is False


@linux_only
def test_output_at_the_limit_is_whole_and_not_marked(fake, monkeypatch):
    monkeypatch.setattr(_client, "MAX_OUTPUT_BYTES", 2000)
    fake.machines["m1"] = make_machine("m1", "k")
    fake.real_shell = True
    r = invoke(
        RunCommandTool, {"command": "head -c 2000 /dev/zero | tr '\\0' x; echo oops >&2; exit 3", "machine_id": "m1"}
    )
    assert r.json["stdout"] == "x" * 2000 and r.json["stderr"] == "oops\n"
    assert r.json["truncated"] is False and r.json["exit_code"] == 3


@linux_only
def test_wrapper_keeps_quoting_and_does_not_wait_for_background_children(fake):
    fake.machines["m1"] = make_machine("m1", "k")
    fake.real_shell = True
    started = time.monotonic()
    r = invoke(RunCommandTool, {"command": """sleep 6 & echo "it's" 'a "test"' $((6 * 7))""", "machine_id": "m1"})
    assert time.monotonic() - started < 4
    assert r.json["stdout"] == 'it\'s a "test" 42\n' and r.json["exit_code"] == 0


# timed_out comes from the machine's marker, not from the exit code


@pytest.mark.parametrize("code", [124, 137])
def test_exit_code_alone_does_not_mean_timed_out(fake, code):
    fake.machines["m1"] = make_machine("m1", "k")
    fake.exec_results = [ExecResult(stdout="", stderr="", exit_code=code)]
    r = invoke(RunCommandTool, {"command": "eat-all-memory", "machine_id": "m1"})
    assert r.json["exit_code"] == code and r.json["timed_out"] is False


@linux_only
def test_timeout_is_marked_and_a_kill_before_the_limit_is_not(fake):
    fake.machines["m1"] = make_machine("m1", "k")
    fake.real_shell = True
    slow = invoke(RunCommandTool, {"command": "echo started; sleep 30", "machine_id": "m1", "timeout": 1})
    assert slow.json["timed_out"] is True and slow.json["exit_code"] == 124 and slow.json["stdout"] == "started\n"
    killed = invoke(RunCommandTool, {"command": "kill -9 $$", "machine_id": "m1", "timeout": 20})
    assert killed.json["exit_code"] == 137 and killed.json["timed_out"] is False and killed.json["stderr"] == ""


# Files are measured before they are transferred


@pytest.mark.parametrize("tool", [DownloadFileTool, ReadFileTool])
def test_large_file_is_refused_before_it_is_downloaded(fake, monkeypatch, tool):
    monkeypatch.setattr(_client, "MAX_FILE_BYTES", 10)
    fake.machines["m1"] = make_machine("m1", "x")
    fake.files.store[("m1", "/data/big.bin")] = b"x" * 11
    with pytest.raises(ValueError, match="larger than"):
        invoke(tool, {"machine_id": "m1", "remote_path": "/data/big.bin"})
    names = fake.names()
    assert "stat" in names and "download" not in names


@pytest.mark.parametrize("tool", [DownloadFileTool, ReadFileTool])
def test_file_at_the_limit_is_measured_then_downloaded(fake, monkeypatch, tool):
    monkeypatch.setattr(_client, "MAX_FILE_BYTES", 10)
    fake.machines["m1"] = make_machine("m1", "x")
    fake.files.store[("m1", "/data/ok.txt")] = b"x" * 10
    r = invoke(tool, {"machine_id": "m1", "remote_path": "/data/ok.txt"})
    assert r.json["size_bytes"] == 10
    names = fake.names()
    assert names.index("stat") < names.index("download")


@pytest.mark.parametrize("tool", [DownloadFileTool, ReadFileTool])
def test_missing_file_is_reported_by_the_size_check(fake, tool):
    fake.machines["m1"] = make_machine("m1", "x")
    with pytest.raises(ValueError, match="Not found"):
        invoke(tool, {"machine_id": "m1", "remote_path": "/nope"})
    assert "download" not in fake.names()


def test_size_check_falls_back_to_stat_on_a_machine_without_the_call(fake, monkeypatch):
    monkeypatch.setattr(_client, "MAX_FILE_BYTES", 10)
    fake.machines["m1"] = make_machine("m1", "old")
    fake.fs.outdated = True
    fake.files.store[("m1", "/data/big.bin")] = b"x" * 11
    fake.exec_results = [ExecResult(stdout="11\n", stderr="", exit_code=0)]
    with pytest.raises(ValueError, match="larger than"):
        invoke(DownloadFileTool, {"machine_id": "m1", "remote_path": "/data/big.bin"})
    ((args, _),) = execs(fake)
    assert args[2] == "stat -L -c %s -- /data/big.bin" and "download" not in fake.names()
    fake.exec_results = [
        ExecResult(stdout="", stderr="stat: cannot statx '/nope': No such file or directory\n", exit_code=1)
    ]
    with pytest.raises(ValueError, match="Not found: stat: cannot statx"):
        invoke(DownloadFileTool, {"machine_id": "m1", "remote_path": "/nope"})


# Every tool that needs the machine brings it back first


class FakeUpload:
    filename = "up.bin"
    blob = b"abc"


SLEEPY_TOOLS = [
    (WriteFileTool, {"remote_path": "a.txt", "content": "x"}, "upload"),
    (UploadFileTool, {"file": FakeUpload(), "remote_path": "/home/boxd/"}, "upload"),
    (ReadFileTool, {"remote_path": "/home/boxd/seed.txt"}, "stat"),
    (DownloadFileTool, {"remote_path": "/home/boxd/seed.txt"}, "stat"),
    (ListFilesTool, {}, "list_dir"),
    (ForkMachineTool, {"name": "copy"}, "fork"),
    (GetPreviewUrlTool, {"port": 8000}, None),
]


@pytest.mark.parametrize("status,action", [("stopped", "start"), ("hibernated", "wake"), ("suspended", "resume")])
@pytest.mark.parametrize("tool,params,first_call", SLEEPY_TOOLS, ids=lambda v: getattr(v, "__name__", None))
def test_tool_brings_a_sleeping_machine_back_first(fake, tool, params, first_call, status, action):
    fake.machines["m1"] = make_machine("m1", "sleepy", status=status)
    fake.files.store[("m1", "/home/boxd/seed.txt")] = b"seed"
    invoke(tool, {"machine_id": "m1", **params})
    names = fake.names()
    assert names.count(action) == 1
    assert names.index(action) < names.index("wait_until_ready")
    if first_call:
        assert names.index("wait_until_ready") < names.index(first_call)
    assert fake.machines["m1"].status == "running"


@pytest.mark.parametrize("tool,params,first_call", SLEEPY_TOOLS, ids=lambda v: getattr(v, "__name__", None))
def test_tool_on_a_running_machine_does_not_touch_its_state(fake, tool, params, first_call):
    fake.machines["m1"] = make_machine("m1", "awake")
    fake.files.store[("m1", "/home/boxd/seed.txt")] = b"seed"
    invoke(tool, {"machine_id": "m1", **params})
    assert not {"start", "wake", "resume"} & set(fake.names())


def test_failed_machine_is_reported_by_the_file_tools(fake):
    fake.machines["m1"] = make_machine("m1", "broken", status="failed")
    with pytest.raises(ValueError, match="is failed"):
        invoke(ListFilesTool, {"machine_id": "m1"})


def test_machine_that_does_not_come_back_is_reported(fake):
    fake.machines["m1"] = make_machine("m1", "s", status="stopped")

    def never(mid, timeout=None):
        raise TimeoutError("machine m1 did not reach 'running' within 120s")

    fake.wait_until_ready = never
    with pytest.raises(ValueError, match="was stopped and did not become ready"):
        invoke(ReadFileTool, {"machine_id": "m1", "remote_path": "/x"})
    assert "delete" not in fake.names()
