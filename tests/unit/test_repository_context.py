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

    assert context["format"] == "python_ast_outline_v2"
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
    assert context["ranking"]["budget_pressure"] is False


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


def test_relevant_file_and_symbol_survive_budget_pressure(tmp_path: Path):
    for index in range(30):
        (tmp_path / f"aaa_distractor_{index:02}.py").write_text(
            f"def unrelated_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    (tmp_path / "zzz_checkout.py").write_text(
        "\n".join(
            [*(f"def helper_{index}():\n    return {index}\n" for index in range(20)),
             "def calculate_refund_total(order):\n    return order.total\n"]
        ),
        encoding="utf-8",
    )

    context = build_repository_context(
        tmp_path,
        tmp_path,
        issue="Refund totals are incorrect when checkout reverses an order",
        max_files=2,
        max_symbols=2,
        max_chars=500,
    )

    assert context["ranking"]["budget_pressure"] is True
    assert "zzz_checkout.py" in context["map"]
    assert "calculate_refund_total" in context["map"]
    assert "aaa_distractor_29.py" not in context["map"]


def test_import_neighbor_is_prioritized_over_unrelated_module(tmp_path: Path):
    (tmp_path / "billing.py").write_text(
        "def calculate_invoice():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        "from billing import calculate_invoice\n\ndef run():\n    return calculate_invoice()\n",
        encoding="utf-8",
    )
    (tmp_path / "unrelated.py").write_text("def run():\n    return 0\n", encoding="utf-8")

    context = build_repository_context(
        tmp_path,
        tmp_path,
        issue="calculate_invoice returns the wrong total",
        max_files=2,
    )

    assert "billing.py" in context["map"]
    assert "service.py" in context["map"]
    assert "unrelated.py" not in context["map"]


def test_large_repository_ranking_is_deterministic(tmp_path: Path):
    package = tmp_path / "src" / "largeapp"
    package.mkdir(parents=True)
    for index in range(250):
        (package / f"module_{index:03}.py").write_text(
            f"def distractor_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    (package / "retry_policy.py").write_text(
        "def compute_retry_backoff(attempt):\n    return attempt\n",
        encoding="utf-8",
    )
    (package / "malformed.py").write_text("def broken(:\n", encoding="utf-8")

    kwargs = {
        "issue": "Retry backoff is wrong after a failed attempt",
        "max_files": 8,
        "max_symbols": 8,
        "max_chars": 1_500,
    }
    first = build_repository_context(tmp_path, tmp_path, **kwargs)
    second = build_repository_context(tmp_path, tmp_path, **kwargs)

    assert first == second
    assert first["stats"]["python_files"] == 252
    assert first["stats"]["mapped_files"] <= 8
    assert first["stats"]["symbols"] <= 8
    assert len(first["map"]) <= 1_500
    assert "src/largeapp/retry_policy.py" in first["map"]
    assert "compute_retry_backoff" in first["map"]
    assert first["parse_errors"] == [
        {"path": "src/largeapp/malformed.py", "line": 1, "error": "SyntaxError"}
    ]
