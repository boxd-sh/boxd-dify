"""Live smoke test: runs the plugin through the Dify plugin runtime against real boxd.

It packages this directory, starts it with `dify plugin run` (the same runtime Dify
uses, gevent included) and calls every tool over the plugin protocol.

    BOXD_E2E=1 BOXD_API_KEY=bxd_... [BOXD_ORG=...] [BOXD_REGION=nyc] \
    [BOXD_NAME_PREFIX=dify-e2e-] [DIFY_CLI_PATH=/path/to/dify-plugin] \
    python scripts/live_smoke.py

It creates machines named with BOXD_NAME_PREFIX and deletes every one of them:
the machines Run Command creates for one call are deleted by the plugin, the fork
and the main test machine by Delete Machine. The main machine also has a one-hour
delete_after, so boxd removes it even when the run is interrupted.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if os.environ.get("BOXD_E2E") != "1":
    print("set BOXD_E2E=1 to run the live smoke test")
    sys.exit(0)

from boxd import Boxd  # noqa: E402
from boxd.errors import NotFoundError  # noqa: E402
from dify_plugin.config.integration_config import IntegrationConfig  # noqa: E402
from dify_plugin.core.entities.plugin.request import (  # noqa: E402
    PluginInvokeType,
    ToolActions,
    ToolInvokeRequest,
    ToolValidateCredentialsRequest,
)
from dify_plugin.entities.tool import ToolInvokeMessage  # noqa: E402
from dify_plugin.integration.run import PluginRunner  # noqa: E402
from pydantic import BaseModel  # noqa: E402

KEY = os.environ["BOXD_API_KEY"]
REGION = os.environ.get("BOXD_REGION", "nyc")
CREDS = {
    "api_key": KEY,
    "org": os.environ.get("BOXD_ORG", ""),
    "region": REGION,
    "name_prefix": os.environ.get("BOXD_NAME_PREFIX", "dify-e2e-"),
}
OTHER_MACHINE = os.environ.get("BOXD_OTHER_MACHINE_ID")  # optional: a machine not made by the plugin
RESULTS: list[tuple[str, bool, float, str]] = []
CREATED: list[str] = []


class ValidateResult(BaseModel):
    result: bool


def call(runner: PluginRunner, tool: str, params: dict, creds: dict | None = None) -> dict:
    out: dict = {"text": [], "json": [], "blob": [], "vars": {}}
    for msg in runner.invoke(
        PluginInvokeType.Tool,
        ToolActions.InvokeTool,
        ToolInvokeRequest(
            user_id="e2e", provider="boxd", tool=tool, credentials=creds or CREDS, tool_parameters=params
        ),
        ToolInvokeMessage,
    ):
        kind = msg.type.value
        if kind == "text":
            out["text"].append(msg.message.text)
        elif kind == "json":
            out["json"].append(msg.message.json_object)
        elif kind == "variable":
            out["vars"][msg.message.variable_name] = msg.message.variable_value
        elif kind in ("blob", "blob_chunk"):
            # the runtime streams large blobs as chunks
            out["blob"].append(msg.message.blob)
    out["j"] = out["json"][0] if out["json"] else {}
    return out


def check(name: str, fn) -> object:
    t0 = time.monotonic()
    try:
        detail = fn()
        ok = True
    except Exception as e:
        detail, ok = f"{type(e).__name__}: {short_error(e)}", False
    dt = time.monotonic() - t0
    RESULTS.append((name, ok, dt, str(detail)[:400]))
    print(f"{'PASS' if ok else 'FAIL'} {name} ({dt:.2f}s): {str(detail)[:400]}", flush=True)
    return detail if ok else None


def short_error(e: Exception) -> str:
    """The plugin runtime wraps tool errors in JSON with a traceback; keep the message."""
    text = str(e)
    try:
        payload = json.loads(e.args[0]["error"]) if isinstance(e.args[0], dict) else None
    except (TypeError, ValueError, KeyError, IndexError):
        payload = None
    if payload:
        return f"{payload.get('error_type', '')}: {payload.get('message', '')}"
    return text[-400:]


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def serve(path: Path) -> tuple[str, socketserver.TCPServer]:
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(path.parent), **k)  # noqa: E731
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/{path.name}", httpd


def main() -> int:
    sdk = Boxd(api_key=KEY, region=REGION)
    with PluginRunner(config=IntegrationConfig(), plugin_package_path=str(ROOT)) as runner:

        def validate(creds):
            return list(
                runner.invoke(
                    PluginInvokeType.Tool,
                    ToolActions.ValidateCredentials,
                    ToolValidateCredentialsRequest(user_id="e2e", provider="boxd", credentials=creds),
                    ValidateResult,
                )
            )

        check("credentials: valid key accepted", lambda: validate(CREDS))

        def wrong_key():
            try:
                validate({**CREDS, "api_key": "bxd_" + "0" * 40})
            except ValueError as e:
                expect("0" * 40 not in str(e), "error echoes the key")
                return short_error(e)
            raise AssertionError("wrong key was accepted")

        check("credentials: wrong key rejected, key not echoed", wrong_key)

        def missing_key():
            try:
                validate({**CREDS, "api_key": ""})
            except ValueError as e:
                return short_error(e)
            raise AssertionError("missing key was accepted")

        check("credentials: missing key rejected", missing_key)

        def create():
            r = call(runner, "create_machine", {"size": "2x8", "delete_after": 3600})
            CREATED.append(r["j"]["machine_id"])
            expect(r["vars"].get("machine_id") == r["j"]["machine_id"], f"no machine_id variable: {r['vars']}")
            return r["j"]

        m = check("create_machine (create to first exec ready)", create)
        if not m:
            return report()
        mid = m["machine_id"]

        def isolated():
            machine = sdk.machines.get(mid)
            expect(machine.networking.isolated is True, "machine is not isolated")
            expect(machine.labels.get("created-by") == "dify", "label missing")
            expect(machine.delete_at is not None, "delete_after was not set")
            return f"isolated={machine.networking.isolated} labels={machine.labels} delete_at={machine.delete_at}"

        check("create_machine: isolated, labelled, delete_after set", isolated)

        def first_exec():
            r = call(runner, "run_command", {"command": "echo out; echo err >&2; exit 3", "machine_id": mid})
            j = r["j"]
            expect(j["exit_code"] == 3 and j["stdout"] == "out\n" and j["stderr"] == "err\n", json.dumps(j))
            expect(r["vars"] == {"stdout": "out\n", "stderr": "err\n", "exit_code": 3}, f"variables: {r['vars']}")
            return {k: j[k] for k in ("exit_code", "stdout", "stderr")}

        check("run_command: exit code, stdout and stderr separate", first_exec)

        def env_cwd():
            call(runner, "run_command", {"command": "mkdir -p /tmp/w", "machine_id": mid})
            r2 = call(
                runner,
                "run_command",
                {
                    "command": 'pwd; echo "$GREETING"',
                    "cwd": "/tmp/w",
                    "env_vars": '{"GREETING": "hi there"}',
                    "machine_id": mid,
                },
            )
            expect(r2["j"]["stdout"] == "/tmp/w\nhi there\n", r2["j"]["stdout"])
            return r2["j"]["stdout"].strip().replace("\n", " | ")

        check("run_command: env and cwd reach the command", env_cwd)

        def timeout():
            t0 = time.monotonic()
            r = call(runner, "run_command", {"command": "sleep 45; echo late", "timeout": 3, "machine_id": mid})
            took = time.monotonic() - t0
            expect(r["j"]["timed_out"] is True and took < 20, f"{r['j']} in {took:.1f}s")
            left = call(
                runner, "run_command", {"command": "ps -eo args | grep -cx 'sleep 45' || true", "machine_id": mid}
            )
            expect(left["j"]["stdout"].strip() == "0", f"process survived: {left['j']['stdout']}")
            return f"exit {r['j']['exit_code']} after {took:.1f}s, remote process gone"

        check("run_command: timeout kills the remote process", timeout)

        for lang, code, want in [
            ("python", "import sys; print(sys.version_info[:2])", "(3, 12)"),
            ("javascript", "console.log([1,2,3].map(x => x * 2).join(','))", "2,4,6"),
            ("typescript", "const n: number = 21; console.log(n * 2)", "42"),
            ("bash", "echo $((6 * 7))", "42"),
        ]:

            def run_code(lang=lang, code=code, want=want):
                r = call(runner, "run_code", {"code": code, "language": lang, "machine_id": mid})
                expect(r["j"]["exit_code"] == 0 and r["j"]["stdout"].strip() == want, json.dumps(r["j"]))
                return r["j"]["stdout"].strip()

            check(f"run_code: {lang}", run_code)

        def files():
            call(
                runner, "write_file", {"machine_id": mid, "remote_path": "/home/boxd/new/dir/a.txt", "content": "héllo"}
            )
            r = call(runner, "read_file", {"machine_id": mid, "remote_path": "/home/boxd/new/dir/a.txt"})
            expect(r["j"]["content"] == "héllo", r["j"])
            ls = call(runner, "list_files", {"machine_id": mid, "path": "/home/boxd/new/dir"})
            expect([e["name"] for e in ls["j"]["entries"]] == ["a.txt"], ls["j"])
            return "write into a new nested dir, read back, list"

        check("write_file / read_file / list_files", files)

        def upload_download():
            data = os.urandom(5 * 1024 * 1024)
            digest = hashlib.sha256(data).hexdigest()
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "blob.bin"
                p.write_bytes(data)
                url, httpd = serve(p)
                try:
                    file_param = {
                        "dify_model_identity": "__dify__file__",
                        "url": url,
                        "type": "document",
                        "filename": "blob.bin",
                        "mime_type": "application/octet-stream",
                        "size": len(data),
                    }
                    up = call(
                        runner, "upload_file", {"machine_id": mid, "file": file_param, "remote_path": "/home/boxd/"}
                    )
                finally:
                    httpd.shutdown()
            expect(up["j"]["remote_path"] == "/home/boxd/blob.bin", up["j"])
            remote = call(runner, "run_command", {"command": "sha256sum /home/boxd/blob.bin", "machine_id": mid})
            expect(remote["j"]["stdout"].split()[0] == digest, "remote sha256 differs")
            down = call(runner, "download_file", {"machine_id": mid, "remote_path": "/home/boxd/blob.bin"})
            got = b"".join(down["blob"])
            expect(hashlib.sha256(got).hexdigest() == digest, f"downloaded {len(got)} bytes, sha differs")
            return f"5 MiB round trip, sha256 {digest[:16]}... matches on the machine and after download"

        check("upload_file / download_file 5 MiB sha256", upload_download)

        svc = {}

        def service():
            r = call(
                runner,
                "start_service",
                {"machine_id": mid, "command": "python3 -m http.server 8000 --bind 0.0.0.0", "cwd": "/home/boxd/new"},
            )
            svc.update(r["j"])
            expect(r["j"]["running"] is True, r["j"])
            time.sleep(2)
            logs = call(runner, "get_service_logs", {"machine_id": mid, "service_id": r["j"]["service_id"]})
            expect(logs["j"]["running"] is True, logs["j"])
            probe = call(runner, "run_command", {"command": "curl -s localhost:8000/dir/a.txt", "machine_id": mid})
            expect(probe["j"]["stdout"] == "héllo", probe["j"])
            return f"{r['j']['service_id']} running after the call returned; served a.txt"

        check("start_service / get_service_logs: daemon survives the call", service)

        def preview():
            r = call(runner, "get_preview_url", {"machine_id": mid, "port": 8000})
            url = r["j"]["url"] + "/dir/a.txt"
            again = call(runner, "get_preview_url", {"machine_id": mid, "port": 8000})
            expect(again["j"]["url"] == r["j"]["url"], "second call gave another URL")
            deadline, last = time.monotonic() + 120, ""
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(url, timeout=10) as resp:
                        body = resp.read().decode()
                    if body == "héllo":
                        return f"{r['j']['url']} served the file"
                    last = body[:80]
                except Exception as e:
                    last = str(e)[:80]
                time.sleep(5)
            raise AssertionError(f"URL never answered: {last}")

        check("get_preview_url: public HTTPS reaches the service", preview)

        def fork():
            call(
                runner,
                "write_file",
                {"machine_id": mid, "remote_path": "/home/boxd/marker.txt", "content": "before-fork"},
            )
            r = call(runner, "fork_machine", {"machine_id": mid})
            fid = r["j"]["machine_id"]
            CREATED.append(fid)
            seen = call(
                runner,
                "run_command",
                {"command": "cat /home/boxd/marker.txt; curl -s localhost:8000/dir/a.txt", "machine_id": fid},
            )
            expect(seen["j"]["stdout"] == "before-forkhéllo", seen["j"])
            expect(sdk.machines.get(fid).networking.isolated is True, "fork not isolated")
            d = call(runner, "delete_machine", {"machine_id": fid})
            expect(d["j"]["success"], d["j"])
            try:
                sdk.machines.get(fid)
            except NotFoundError:
                return f"fork {fid}: file and running server present, isolated; deleted, get() -> not found"
            raise AssertionError("fork still exists after delete")

        check("fork_machine + delete_machine (fork has files and processes)", fork)

        def lister():
            r = call(runner, "list_machines", {})
            ids = [x["machine_id"] for x in r["j"]["machines"]]
            expect(mid in ids, "test machine missing from list")
            return f"{r['j']['count']} plugin machines listed"

        check("list_machines", lister)

        if OTHER_MACHINE:

            def refuse():
                try:
                    call(runner, "delete_machine", {"machine_id": OTHER_MACHINE})
                except ValueError as e:
                    expect(sdk.machines.get(OTHER_MACHINE) is not None, "other machine gone")
                    return short_error(e)
                raise AssertionError("deleted a machine the plugin did not create")

            check("delete_machine refuses a machine the plugin did not create", refuse)

        def stop_then_exec():
            call(runner, "manage_machine", {"machine_id": mid, "action": "stop"})
            expect(sdk.machines.get(mid).status == "stopped", "not stopped")
            t0 = time.monotonic()
            r = call(runner, "run_command", {"command": "cat /home/boxd/marker.txt", "machine_id": mid})
            expect(r["j"]["stdout"] == "before-fork", r["j"])
            return f"exec on stopped machine started it and ran in {time.monotonic() - t0:.1f}s"

        check("run_command on a stopped machine", stop_then_exec)

        def pause_resume():
            r = call(runner, "manage_machine", {"machine_id": mid, "action": "pause"})
            r2 = call(runner, "run_command", {"command": "echo awake", "machine_id": mid})
            expect(r2["j"]["stdout"] == "awake\n", r2["j"])
            return f"paused ({r['j']['status']}), exec answered"

        check("manage_machine pause, then exec", pause_resume)

        def hibernate_wake():
            call(runner, "start_service", {"machine_id": mid, "command": "python3 -m http.server 8001"})
            r = call(runner, "manage_machine", {"machine_id": mid, "action": "hibernate"})
            expect(r["j"]["status"] == "hibernated", r["j"])
            t0 = time.monotonic()
            w = call(runner, "manage_machine", {"machine_id": mid, "action": "wake"})
            woke = time.monotonic() - t0
            p = call(
                runner,
                "run_command",
                {"command": "curl -s -o /dev/null -w '%{http_code}' localhost:8001/", "machine_id": mid},
            )
            expect(p["j"]["stdout"] == "200", p["j"])
            return f"hibernated, woke in {woke:.1f}s ({w['j']['status']}); server from before hibernate still answers"

        check("manage_machine hibernate / wake keeps processes", hibernate_wake)

        def ephemeral():
            r = call(runner, "run_command", {"command": "hostname; echo ok"})
            eid = r["j"]["machine_id"]
            CREATED.append(eid)
            expect(r["j"]["ephemeral"] is True and r["j"]["stdout"].endswith("ok\n"), r["j"])
            try:
                sdk.machines.get(eid)
            except NotFoundError:
                return f"ran on {eid}, which is gone afterwards"
            raise AssertionError("ephemeral machine still exists")

        check("run_command without machine_id (machine made and deleted)", ephemeral)

        def ephemeral_code():
            r = call(runner, "run_code", {"code": "print(sum(range(10)))"})
            CREATED.append(r["j"]["machine_id"])
            expect(r["j"]["stdout"] == "45\n", r["j"])
            return f"ran on {r['j']['machine_id']}"

        check("run_code without machine_id", ephemeral_code)

        def parallel():
            t0 = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(4) as ex:
                futs = [
                    ex.submit(call, runner, "run_command", {"command": f"sleep 3; echo {i}", "machine_id": mid})
                    for i in range(4)
                ]
                outs = sorted(f.result()["j"]["stdout"].strip() for f in futs)
            took = time.monotonic() - t0
            expect(outs == ["0", "1", "2", "3"] and took < 9, f"{outs} in {took:.1f}s")
            return f"4 x 'sleep 3' in {took:.1f}s (concurrent inside the plugin process)"

        check("concurrent tool calls do not block each other", parallel)

        def missing():
            try:
                call(runner, "run_command", {"command": "true", "machine_id": "00000000-0000-0000-0000-000000000000"})
            except ValueError as e:
                return short_error(e)
            raise AssertionError("no error")

        check("unknown machine id gives a clear error", missing)

        def delete_main():
            call(runner, "delete_machine", {"machine_id": mid})
            try:
                sdk.machines.get(mid)
            except NotFoundError:
                return f"{mid} deleted"
            raise AssertionError(f"{mid} still exists")

        check("delete the test machine", delete_main)

    return report()


def report() -> int:
    passed = sum(1 for r in RESULTS if r[1])
    print(f"\n{passed}/{len(RESULTS)} passed")
    print("machines created:", " ".join(CREATED))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
