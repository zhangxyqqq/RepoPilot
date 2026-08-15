from types import SimpleNamespace

from repopilot.tools import ToolRegistry


class RecordingSandbox:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def invoke(self, name, arguments):
        self.calls.append((name, arguments))
        return {"ok": True, "result": {}, "latency_ms": 0.1}


def test_registry_rejects_missing_required_argument_before_sandbox_execution():
    sandbox = RecordingSandbox()
    registry = ToolRegistry(sandbox)  # type: ignore[arg-type]

    result = registry.call("read_file", {})

    assert result.ok is False
    assert result.error == "invalid tool arguments: missing required property: path"
    assert sandbox.calls == []


def test_registry_rejects_unknown_or_wrong_typed_arguments_before_execution():
    sandbox = RecordingSandbox()
    registry = ToolRegistry(sandbox)  # type: ignore[arg-type]

    unknown = registry.call("run_tests", {"command": "pytest"})
    wrong_type = registry.call("read_file", {"path": "app.py", "start_line": True})

    assert unknown.error == "invalid tool arguments: unknown property: command"
    assert wrong_type.error == "invalid tool arguments: start_line must be an integer"
    assert sandbox.calls == []


def test_registry_accepts_schema_valid_arguments():
    sandbox = RecordingSandbox()
    registry = ToolRegistry(sandbox)  # type: ignore[arg-type]

    result = registry.call("search_code", {"query": "target", "regex": False})

    assert result.ok is True
    assert sandbox.calls == [("search_code", {"query": "target", "regex": False})]
