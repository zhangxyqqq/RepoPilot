from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from repopilot.agent import run_agent
from repopilot.config import RunConfig
from repopilot.evaluation import evaluate_benchmarks
from repopilot.llm.openai_adapter import OpenAIModel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repopilot", description="Evaluated repository coding agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the agent against a local Python repository")
    run_parser.add_argument("repository", type=Path)
    run_parser.add_argument("--issue", required=True)
    run_parser.add_argument("--model", required=True)
    run_parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "repopilot-runs",
        help="run artifact directory outside the input repository (default: system temporary directory)",
    )

    eval_parser = subparsers.add_parser("eval", help="run the synthetic benchmark suite")
    eval_parser.add_argument("--benchmarks", type=Path, default=Path("benchmarks/cases"))
    eval_parser.add_argument("--output", type=Path, default=Path("reports"))
    eval_parser.add_argument("--model", default="scripted", help="scripted or an OpenAI model name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        result, workspace = run_agent(
            RunConfig(repository=args.repository, issue=args.issue, output_dir=args.output),
            OpenAIModel(args.model),
        )
        print(json.dumps({**asdict(result), "workspace": str(workspace)}, indent=2, sort_keys=True))
        return 0 if result.success else 1

    factory = None
    if args.model != "scripted":
        factory = lambda case: OpenAIModel(args.model)
    report = evaluate_benchmarks(args.benchmarks, args.output, model_factory=factory)
    print(json.dumps(report["aggregate"], indent=2, sort_keys=True))
    print(f"JSON report: {report['report_paths']['json']}")
    print(f"Markdown report: {report['report_paths']['markdown']}")
    return 0 if report["aggregate"]["tasks_succeeded"] == report["aggregate"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
