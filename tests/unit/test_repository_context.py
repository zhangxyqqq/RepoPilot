from pathlib import Path

from repopilot.sandbox.repository_context import build_repository_context


def test_repository_context_maps_python_structure_and_parse_errors(tmp_path: Path):
    package = tmp_path / "src" / "shop"
    package.mkdir(parents=True)
    (package / "models.py").write_text(
        "class Receipt:\n    pass\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        """from .models import Receipt
import decimal

class Checkout(object):
    def run(self, total: decimal.Decimal, /, *, currency: str = "EUR") -> Receipt:
        return Receipt()

async def load_checkout(identifier: int) -> Checkout:
    return Checkout()
""",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_service.py").write_text(
        "def test_checkout():\n    assert True\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("def nope(:\n", encoding="utf-8")

    context = build_repository_context(tmp_path, tmp_path)

    assert context["format"] == "python_ast_outline_v1"
    assert "src/shop/service.py [module=shop.service, role=source]" in context["map"]
    assert "imports: .models.Receipt, decimal" in context["map"]
    assert "class Checkout(object) @L4" in context["map"]
    assert "def run(self, total: decimal.Decimal, /, *, currency: str = 'EUR') -> Receipt @L5" in context["map"]
    assert "async def load_checkout(identifier: int) -> Checkout @L8" in context["map"]
    assert "tests/test_service.py [module=tests.test_service, role=test]" in context["map"]
    assert context["stats"] == {
        "python_files": 4,
        "mapped_files": 3,
        "symbols": 5,
        "parse_errors": 1,
    }
    assert context["parse_errors"] == [
        {"path": "broken.py", "line": 1, "error": "SyntaxError"}
    ]
    assert context["truncated"] is False


def test_repository_context_reports_bounded_output(tmp_path: Path):
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text(
            f"def function_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    context = build_repository_context(tmp_path, tmp_path, max_files=1)

    assert context["stats"]["python_files"] == 3
    assert context["stats"]["mapped_files"] == 1
    assert context["truncated"] is True
    assert "module_0.py" in context["map"]
    assert "module_1.py" not in context["map"]
