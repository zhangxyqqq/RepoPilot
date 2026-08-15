from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from repopilot.agent import run_agent
from repopilot.config import RunConfig
from repopilot.evaluation import evaluate_benchmarks, validate_real_world_references
from repopilot.llm import ProviderConfig, create_model


def _add_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=("openai", "openai_compatible", "deepseek"),
        default="openai",
        help="model endpoint provider (default: openai)",
    )
    parser.add_argument(
        "--base-url",
        help="OpenAI-compatible API root, for example http://127.0.0.1:8000/v1",
    )
    parser.add_argument(
        "--api-key-env",
        help="optional endpoint-specific API key environment variable",
    )


def _provider_config(args: argparse.Namespace) -> ProviderConfig:
    return ProviderConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repopilot", description="Evaluated repository coding agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the agent against a local Python repository")
    run_parser.add_argument("repository", type=Path)
    run_parser.add_argument("--issue", required=True)
    run_parser.add_argument("--model", required=True)
    _add_provider_arguments(run_parser)
    run_parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "repopilot-runs",
        help="run artifact directory outside the input repository (default: system temporary directory)",
    )

    eval_parser = subparsers.add_parser("eval", help="run the synthetic benchmark suite")
    eval_parser.add_argument("--benchmarks", type=Path, default=Path("benchmarks/cases"))
    eval_parser.add_argument("--output", type=Path, default=Path("reports"))
    eval_parser.add_argument("--model", default="scripted", help="scripted or an endpoint model name")
    _add_provider_arguments(eval_parser)

    real_parser = subparsers.add_parser(
        "real-validate",
        help="validate pinned real-world task checkouts and reference-patch integrity",
    )
    real_parser.add_argument("--tasks", type=Path, default=Path("benchmarks/real_world"))
    real_parser.add_argument("--output", type=Path, default=Path("reports/real-world-reference"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "real-validate":
        report = validate_real_world_references(args.tasks, args.output)
        print(json.dumps(report["aggregate"], indent=2, sort_keys=True))
        print(f"JSON report: {report['report_paths']['json']}")
        print(f"Markdown report: {report['report_paths']['markdown']}")
        return 0 if report["aggregate"]["reference_integrity_passed"] == report["aggregate"]["cases"] else 1
    if args.command == "run":
        try:
            model = create_model(_provider_config(args))
        except ValueError as exc:
            parser.error(str(exc))
        result, workspace = run_agent(
            RunConfig(repository=args.repository, issue=args.issue, output_dir=args.output),
            model,
        )
        print(json.dumps({**asdict(result), "workspace": str(workspace)}, indent=2, sort_keys=True))
        return 0 if result.success else 1

    factory = None
    if args.model == "scripted":
        if args.provider != "openai" or args.base_url is not None or args.api_key_env is not None:
            parser.error("provider options cannot be used with the scripted model")
    else:
        try:
            provider_config = _provider_config(args)
            model = create_model(provider_config)
        except ValueError as exc:
            parser.error(str(exc))
        factory = lambda case: model
    report = evaluate_benchmarks(args.benchmarks, args.output, model_factory=factory)
    print(json.dumps(report["aggregate"], indent=2, sort_keys=True))
    print(f"JSON report: {report['report_paths']['json']}")
    print(f"Markdown report: {report['report_paths']['markdown']}")
    return 0 if report["aggregate"]["tasks_succeeded"] == report["aggregate"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
