from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import anyio

from repopilot.agent import run_agent
from repopilot.config import RunConfig
from repopilot.evaluation import (
    EvaluationProfileError,
    RetrievalBenchmarkError,
    evaluate_benchmarks,
    evaluate_retrieval,
    load_evaluation_profile,
    run_reliability_evaluation,
    validate_real_world_references,
)
from repopilot.evaluation.faults import FaultScheduleError
from repopilot.llm import ProviderConfig, create_model
from repopilot.mcp import McpToolAdapter, sanitize_mcp_value, serve_stdio
from repopilot.session import RunSession
from repopilot.trajectory import TraceValidationError, write_trace_summary


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
    eval_parser.add_argument(
        "--profile",
        type=Path,
        help="validated JSON evaluation profile (default: canonical controlled profile)",
    )
    _add_provider_arguments(eval_parser)

    real_parser = subparsers.add_parser(
        "real-validate",
        help="validate pinned real-world task checkouts and reference-patch integrity",
    )
    real_parser.add_argument("--tasks", type=Path, default=Path("benchmarks/real_world"))
    real_parser.add_argument("--output", type=Path, default=Path("reports/real-world-reference"))

    trace_parser = subparsers.add_parser(
        "trace-summary",
        help="validate and summarize a V1 or V2 trajectory without network or Docker",
    )
    trace_parser.add_argument("trajectory", type=Path)
    trace_parser.add_argument(
        "--output",
        type=Path,
        help="summary directory (default: trace-summary beside the trajectory)",
    )

    mcp_parser = subparsers.add_parser(
        "mcp-serve",
        help="serve the six sandboxed repository tools over local stdio MCP",
    )
    mcp_parser.add_argument("repository", type=Path)
    mcp_parser.add_argument("--issue", required=True)
    mcp_parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "repopilot-mcp-runs",
        help="run artifact directory outside the input repository",
    )
    reliability_parser = subparsers.add_parser(
        "reliability",
        help="run the deterministic fault-injection and recovery matrix",
    )
    reliability_parser.add_argument("--benchmarks", type=Path, default=Path("benchmarks/cases"))
    reliability_parser.add_argument("--profile", type=Path)
    reliability_parser.add_argument("--schedule", type=Path)
    reliability_parser.add_argument("--output", type=Path, default=Path("reports/reliability"))
    retrieval_parser = subparsers.add_parser(
        "retrieval-eval",
        help="run the deterministic offline repository-localization benchmark",
    )
    retrieval_parser.add_argument("--corpus", type=Path, default=Path("benchmarks/retrieval/cases.v1.json"))
    retrieval_parser.add_argument("--profile", type=Path, default=Path("configs/evaluation/retrieval.json"))
    retrieval_parser.add_argument("--output", type=Path, default=Path("reports/retrieval"))
    retrieval_parser.add_argument(
        "--strategies",
        nargs="+",
        choices=("structural", "lexical", "semantic", "hybrid"),
        default=("structural",),
    )
    return parser


async def _serve_mcp_command(args: argparse.Namespace) -> None:
    run_id = f"mcp-{uuid.uuid4().hex}"
    session = RunSession.start(
        RunConfig(repository=args.repository, issue=str(sanitize_mcp_value(args.issue)), output_dir=args.output),
        run_id=run_id,
        model_metadata={"provider": "mcp", "model": "external-client", "deterministic": False},
        expose_source_path=False,
        transport="mcp_stdio",
    )
    try:
        await serve_stdio(McpToolAdapter(session.tools, recorder=session.recorder))
    finally:
        session.finish_transport()
        session.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "retrieval-eval":
        try:
            report = evaluate_retrieval(
                args.corpus,
                args.profile,
                args.output,
                strategies=tuple(args.strategies),
            )
        except (RetrievalBenchmarkError, ValueError) as exc:
            parser.error(str(exc))
        summary = {strategy: value["aggregate"] for strategy, value in report["strategies"].items()}
        print(json.dumps(summary, indent=2, sort_keys=True))
        print(f"JSON report: {report['report_paths']['json']}")
        print(f"Markdown report: {report['report_paths']['markdown']}")
        return 0
    if args.command == "reliability":
        try:
            report = run_reliability_evaluation(
                args.benchmarks,
                args.output,
                profile_path=args.profile,
                schedule_path=args.schedule,
            )
        except (EvaluationProfileError, FaultScheduleError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(report["aggregate"], indent=2, sort_keys=True))
        print(f"JSON report: {report['report_paths']['json']}")
        print(f"Markdown report: {report['report_paths']['markdown']}")
        return 0 if report["profile_acceptance"]["passed"] and report["aggregate"]["scenarios_passed"] == report["aggregate"]["scenarios"] else 1
    if args.command == "mcp-serve":
        anyio.run(_serve_mcp_command, args)
        return 0
    if args.command == "trace-summary":
        trajectory = args.trajectory.resolve()
        output = (args.output or trajectory.parent / "trace-summary").resolve()
        run_path = trajectory.with_name("run.json")
        try:
            artifact, json_path, markdown_path = write_trace_summary(
                trajectory,
                output,
                run_path=run_path if run_path.exists() else None,
            )
        except (OSError, json.JSONDecodeError, TraceValidationError) as exc:
            print(f"trace-summary failed: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(artifact["trace"], indent=2, sort_keys=True))
        print(f"JSON summary: {json_path}")
        print(f"Markdown summary: {markdown_path}")
        return 0
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

    try:
        profile = load_evaluation_profile(args.profile)
    except EvaluationProfileError as exc:
        parser.error(str(exc))
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
    report = evaluate_benchmarks(args.benchmarks, args.output, model_factory=factory, profile=profile)
    print(json.dumps(report["aggregate"], indent=2, sort_keys=True))
    print(f"JSON report: {report['report_paths']['json']}")
    print(f"Markdown report: {report['report_paths']['markdown']}")
    return 0 if report["aggregate"]["tasks_succeeded"] == report["aggregate"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
