import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boxd.errors import ConflictError, NotFoundError  # noqa: E402
from boxd.models import ExecResult  # noqa: E402

import _client  # noqa: E402

CREDS = {"api_key": "fake-test-key-123", "org": "acme", "region": "nyc"}


def make_machine(mid, name, status="running", labels=None):
    return SimpleNamespace(
        id=mid,
        name=name,
        status=status,
        region="nyc",
        resources=SimpleNamespace(vcpu=2, memory_bytes=8 * 2**30),
        labels=dict(labels or {}),
    )


class FakeFiles:
    def __init__(self):
        self.store = {}

    def upload(self, mid, path, data):
        self.store[(mid, path)] = data.encode() if isinstance(data, str) else data
        return len(self.store[(mid, path)])

    def download(self, mid, path):
        if (mid, path) not in self.store:
            raise NotFoundError(f"no such file {path}")
        return self.store[(mid, path)]

    def list_dir(self, mid, path):
        entries = [
            SimpleNamespace(name="b.txt", is_dir=False, size_bytes=3, permissions="-rw-r--r--"),
            SimpleNamespace(name="a", is_dir=True, size_bytes=0, permissions="drwxr-xr-x"),
        ]
        return SimpleNamespace(entries=entries, truncated=False)


class FakeProxies:
    def __init__(self):
        self.routes = {}

    def create(self, mid, name, port):
        self.routes[(mid, name)] = port

    def list(self, mid):
        return [
            SimpleNamespace(name=n, domain=f"{n}.m-{m[:4]}.boxd.sh", port=p)
            for (m, n), p in self.routes.items()
            if m == mid
        ]


class FakeMachines:
    def __init__(self):
        self.machines = {}
        self.calls = []
        self.exec_results = []
        self.conflict_once = False
        self.files = FakeFiles()
        self.proxies = FakeProxies()
        self._n = 0

    def _rec(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def create(self, name, **kwargs):
        self._rec("create", name, **kwargs)
        self._n += 1
        m = make_machine(f"id-{self._n}", name, labels=kwargs.get("labels"))
        self.machines[m.id] = m
        return m

    def fork(self, source, name, **kwargs):
        self._rec("fork", source, name, **kwargs)
        return self._new(name, kwargs)

    def _new(self, name, kwargs):
        self._n += 1
        m = make_machine(f"id-{self._n}", name, labels=kwargs.get("labels"))
        self.machines[m.id] = m
        return m

    def get(self, mid):
        if mid not in self.machines:
            raise NotFoundError(f"machine {mid} not found")
        return self.machines[mid]

    def list(self, org=None, labels=None):
        self._rec("list", org=org, labels=labels)
        out = list(self.machines.values())
        if labels:
            out = [m for m in out if all(m.labels.get(k) == v for k, v in labels.items())]
        return out

    def wait_until_ready(self, mid, timeout=None):
        self._rec("wait_until_ready", mid)
        self.machines[mid].status = "running"
        return self.machines[mid]

    def exec(self, mid, command, **kwargs):
        self._rec("exec", mid, command, **kwargs)
        if mid not in self.machines:
            raise NotFoundError(f"machine {mid} not found")
        if self.conflict_once:
            self.conflict_once = False
            raise ConflictError("machine is stopped")
        if self.exec_results:
            return self.exec_results.pop(0)
        return ExecResult(stdout="hi\n", stderr="", exit_code=0)

    def delete(self, mid):
        self._rec("delete", mid)
        self.machines.pop(mid)

    def _state(self, action, mid, status):
        self._rec(action, mid)
        self.machines[mid].status = status

    def start(self, mid):
        self._state("start", mid, "starting")

    def stop(self, mid):
        self._state("stop", mid, "stopped")

    def wake(self, mid):
        self._state("wake", mid, "starting")

    def hibernate(self, mid):
        self._state("hibernate", mid, "hibernated")

    def pause(self, mid):
        self._state("pause", mid, "suspended")

    def resume(self, mid):
        self._state("resume", mid, "starting")

    def names(self):
        return [c[0][0] for c in self.calls]


class FakeBoxd:
    instances = []

    def __init__(self, api_key=None, region=None):
        self.api_key = api_key
        self.region = region
        self.machines = FakeMachines()
        FakeBoxd.instances.append(self)


@pytest.fixture
def fake(monkeypatch):
    FakeBoxd.instances.clear()
    _client._client_cache.clear()
    monkeypatch.setattr(_client, "Boxd", FakeBoxd)
    client, _ = _client.build_client(CREDS)
    return client.machines


def invoke(tool_cls, params, creds=None):
    tool = tool_cls.from_credentials(creds or CREDS)
    msgs = list(tool._invoke(params))
    texts = [m.message.text for m in msgs if m.type.value == "text"]
    jsons = [m.message.json_object for m in msgs if m.type.value == "json"]
    blobs = [m for m in msgs if m.type.value == "blob"]
    return SimpleNamespace(text="\n".join(texts), json=jsons[0] if jsons else None, blobs=blobs, msgs=msgs)
