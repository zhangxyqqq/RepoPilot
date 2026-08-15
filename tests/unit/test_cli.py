from pathlib import Path

import pytest

from repopilot.cli import _provider_config, build_parser, main


def test_run_cli_keeps_openai_as_backward_compatible_default():
    args = build_parser().parse_args(
        ["run", "repository", "--issue", "Fix it", "--model", "gpt-model"]
    )

    assert args.repository == Path("repository")
    assert _provider_config(args).provider == "openai"
    assert _provider_config(args).base_url is None


def test_run_cli_accepts_openai_compatible_endpoint_configuration():
    args = build_parser().parse_args(
        [
            "run",
            "repository",
            "--issue",
            "Fix it",
            "--provider",
            "openai_compatible",
            "--base-url",
            "http://127.0.0.1:8000/v1",
            "--api-key-env",
            "LOCAL_MODEL_API_KEY",
            "--model",
            "local-code-model",
        ]
    )

    config = _provider_config(args)
    assert config.provider == "openai_compatible"
    assert config.base_url == "http://127.0.0.1:8000/v1"
    assert config.api_key_env == "LOCAL_MODEL_API_KEY"
    assert config.model == "local-code-model"


def test_run_cli_accepts_deepseek_without_key_cli_argument():
    args = build_parser().parse_args(
        [
            "run",
            "repository",
            "--issue",
            "Fix it",
            "--provider",
            "deepseek",
            "--model",
            "deepseek-v4-pro",
        ]
    )

    config = _provider_config(args)
    assert config.provider == "deepseek"
    assert config.base_url is None
    assert config.api_key_env is None


def test_eval_rejects_compatible_provider_without_base_url():
    with pytest.raises(SystemExit, match="2"):
        main(["eval", "--provider", "openai_compatible", "--model", "local-code-model"])


def test_real_world_reference_validation_cli_has_separate_paths():
    args = build_parser().parse_args(
        [
            "real-validate",
            "--tasks",
            "benchmarks/real_world",
            "--output",
            "reports/real-world-reference",
        ]
    )

    assert args.command == "real-validate"
    assert args.tasks == Path("benchmarks/real_world")
    assert args.output == Path("reports/real-world-reference")
