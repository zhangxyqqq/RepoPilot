from pathlib import Path

from repopilot.sandbox import sandbox_runner


def test_apply_patch_envelope_is_normalized_and_applied(monkeypatch, tmp_path: Path):
    module = tmp_path / "module.py"
    module.write_text("def value():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(sandbox_runner, "WORKSPACE", tmp_path)
    patch = """*** Begin Patch
*** Update File: module.py
@@
-    return 1
+    return 2
*** End Patch"""

    result = sandbox_runner.apply_patch({"patch": patch})

    assert result["format"] == "apply_patch_envelope"
    assert result["paths"] == ["module.py"]
    assert module.read_text(encoding="utf-8") == "def value():\n    return 2\n"


def test_public_test_files_are_ignored_when_production_changes_exist(monkeypatch, tmp_path: Path):
    module = tmp_path / "module.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    public_test = tests / "test_module.py"
    public_test.write_text("assert True\n", encoding="utf-8")
    monkeypatch.setattr(sandbox_runner, "WORKSPACE", tmp_path)
    patch = """*** Begin Patch
*** Update File: module.py
@@
-VALUE = 1
+VALUE = 2
*** Update File: tests/test_module.py
@@
-assert True
+assert False
*** End Patch"""

    result = sandbox_runner.apply_patch({"patch": patch})

    assert result["paths"] == ["module.py"]
    assert result["ignored_paths"] == ["tests/test_module.py"]
    assert module.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert public_test.read_text(encoding="utf-8") == "assert True\n"
