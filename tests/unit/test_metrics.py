from repopilot.evaluation.metrics import localization_metrics, parse_pytest_counts


def test_localization_uses_best_allowed_fix_set():
    metrics = localization_metrics(["src/fix.py", "README.md"], [["src/fix.py"], ["src/alternative.py"]])
    assert metrics == {"precision": 0.5, "recall": 1.0, "f1": 2 / 3}


def test_pytest_summary_is_parsed():
    assert parse_pytest_counts("2 failed, 7 passed, 1 error in 0.2s") == {
        "passed": 7,
        "failed": 2,
        "errors": 1,
    }
