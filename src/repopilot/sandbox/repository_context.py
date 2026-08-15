from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXCLUDED_DIRECTORIES = {".git", ".pytest_cache", "__pycache__"}
STOP_WORDS = {
    "and", "are", "but", "does", "for", "from", "has", "have", "into", "not",
    "only", "should", "that", "the", "their", "then", "this", "when", "where",
    "with", "without", "incorrect", "issue", "expected", "behavior", "bug", "fix",
}


@dataclass(frozen=True)
class SymbolOutline:
    name: str
    line: str
    order: int


@dataclass(frozen=True)
class FileOutline:
    path: Path
    module: str
    role: str
    imports: tuple[str, ...]
    symbols: tuple[SymbolOutline, ...]
    order: int


def _expression(node: ast.AST | None, *, limit: int = 80) -> str:
    if node is None:
        return ""
    try:
        value = ast.unparse(node)
    except (AttributeError, ValueError):
        value = "?"
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _argument(argument: ast.arg, default: ast.AST | None = None) -> str:
    value = argument.arg
    if argument.annotation is not None:
        value += f": {_expression(argument.annotation)}"
    if default is not None:
        value += f" = {_expression(default)}"
    return value


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    default_offset = len(positional) - len(arguments.defaults)
    parts = [
        _argument(argument, arguments.defaults[index - default_offset] if index >= default_offset else None)
        for index, argument in enumerate(positional)
    ]
    if arguments.posonlyargs:
        parts.insert(len(arguments.posonlyargs), "/")
    if arguments.vararg is not None:
        parts.append("*" + _argument(arguments.vararg))
    elif arguments.kwonlyargs:
        parts.append("*")
    parts.extend(
        _argument(argument, default)
        for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
    )
    if arguments.kwarg is not None:
        parts.append("**" + _argument(arguments.kwarg))
    returns = f" -> {_expression(node.returns)}" if node.returns is not None else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(parts)}){returns}"


def _module_name(relative_path: Path) -> str:
    parts = list(relative_path.with_suffix("").parts)
    if parts and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or relative_path.stem


def _is_test_path(relative_path: Path) -> bool:
    return "tests" in relative_path.parts or relative_path.name.startswith("test_") or relative_path.name.endswith("_test.py")


def _imports(tree: ast.Module) -> list[str]:
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            separator = "" if not module or module.endswith(".") else "."
            imports.extend(f"{module}{separator}{alias.name}" for alias in node.names)
    return sorted(dict.fromkeys(imports))


def _python_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name for name in directories
            if name not in EXCLUDED_DIRECTORIES and not (Path(current) / name).is_symlink()
        )
        for name in sorted(files):
            candidate = Path(current) / name
            if candidate.suffix == ".py" and candidate.is_file() and not candidate.is_symlink():
                paths.append(candidate)
    return paths


def _issue_terms(issue: str) -> tuple[str, ...]:
    raw_terms = re.findall(r"[A-Za-z][A-Za-z0-9_]{1,}", issue)
    expanded: set[str] = set()
    for raw in raw_terms:
        lowered = raw.lower()
        expanded.add(lowered)
        expanded.update(part for part in lowered.split("_") if len(part) >= 3)
        expanded.update(
            part.lower() for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", raw)
            if len(part) >= 3
        )
    return tuple(sorted(term for term in expanded if len(term) >= 3 and term not in STOP_WORDS))


def _name_parts(value: str) -> set[str]:
    lowered = value.lower()
    return {
        lowered,
        *(part for part in re.split(r"[^a-z0-9]+", lowered) if part),
        *(part for part in lowered.replace("/", ".").split(".") if part),
    }


def _relevance(terms: tuple[str, ...], values: list[str]) -> int:
    parts: set[str] = set()
    joined = " ".join(value.lower() for value in values)
    for value in values:
        parts.update(_name_parts(value))
    score = 0
    for term in terms:
        if term in parts:
            score += 20
        elif term in joined:
            score += 5
    return score


def _parse_outline(path: Path, relative: Path, order: int) -> FileOutline:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative), type_comments=True)
    symbols: list[SymbolOutline] = []
    symbol_order = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(SymbolOutline(node.name, f"  {_signature(node)} @L{node.lineno}", symbol_order))
            symbol_order += 1
        elif isinstance(node, ast.ClassDef):
            bases = f"({', '.join(_expression(base) for base in node.bases)})" if node.bases else ""
            symbols.append(SymbolOutline(node.name, f"  class {node.name}{bases} @L{node.lineno}", symbol_order))
            symbol_order += 1
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(SymbolOutline(
                        f"{node.name}.{member.name}", f"    {_signature(member)} @L{member.lineno}", symbol_order
                    ))
                    symbol_order += 1
    return FileOutline(
        path=relative,
        module=_module_name(relative),
        role="test" if _is_test_path(relative) else "source",
        imports=tuple(_imports(tree)),
        symbols=tuple(symbols),
        order=order,
    )


def build_repository_context(
    root: Path,
    workspace: Path,
    *,
    issue: str = "",
    max_files: int = 100,
    max_symbols: int = 300,
    max_chars: int = 8_000,
    max_file_bytes: int = 1_000_000,
    max_scan_files: int = 5_000,
) -> dict[str, Any]:
    """Build a bounded, issue-ranked Python outline using the standard-library AST."""

    python_files = _python_files(root)
    outlines: list[FileOutline] = []
    parse_errors: list[dict[str, Any]] = []
    parse_error_count = 0
    truncated = len(python_files) > max_scan_files
    for order, path in enumerate(python_files[:max_scan_files]):
        relative = path.relative_to(workspace)
        if path.stat().st_size > max_file_bytes:
            parse_error_count += 1
            if len(parse_errors) < 20:
                parse_errors.append({"path": str(relative), "error": "file exceeds AST size limit"})
            continue
        try:
            outlines.append(_parse_outline(path, relative, order))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            parse_error_count += 1
            if len(parse_errors) < 20:
                parse_errors.append({
                    "path": str(relative), "line": getattr(exc, "lineno", None), "error": type(exc).__name__
                })

    estimated_chars = sum(
        len(str(item.path)) + len(item.module) + sum(len(symbol.line) for symbol in item.symbols) + 40
        for item in outlines
    )
    total_symbols = sum(len(item.symbols) for item in outlines)
    pressure = len(outlines) > max_files or total_symbols > max_symbols or estimated_chars > max_chars
    terms = _issue_terms(issue)
    file_scores = {
        item.path: _relevance(terms, [str(item.path), item.module, *(symbol.name for symbol in item.symbols)])
        + (1 if item.role == "source" else 0)
        for item in outlines
    }
    directly_relevant_modules = {
        item.module for item in outlines if file_scores[item.path] > (1 if item.role == "source" else 0)
    }
    for item in outlines:
        if any(
            imported.lstrip(".") == module or imported.lstrip(".").startswith(module + ".")
            for imported in item.imports for module in directly_relevant_modules
        ):
            file_scores[item.path] += 3

    ranked = sorted(outlines, key=lambda item: (-file_scores[item.path], str(item.path))) if pressure else outlines
    if len(ranked) > max_files:
        truncated = True
    selected = ranked[:max_files]
    output_lines: list[str] = []
    output_chars = 0
    mapped_files = 0
    symbol_count = 0

    def append(line: str) -> bool:
        nonlocal output_chars, truncated
        added = len(line) + (1 if output_lines else 0)
        if output_chars + added > max_chars:
            truncated = True
            return False
        output_lines.append(line)
        output_chars += added
        return True

    exhausted = False
    for item in selected:
        if not append(f"{item.path} [module={item.module}, role={item.role}]"):
            break
        mapped_files += 1
        if item.imports and not append(f"  imports: {', '.join(item.imports[:16])}"):
            break
        if len(item.imports) > 16:
            truncated = True
        symbols = (
            sorted(item.symbols, key=lambda symbol: (-_relevance(terms, [symbol.name, symbol.line]), symbol.order))
            if pressure else item.symbols
        )
        for symbol in symbols:
            if symbol_count >= max_symbols or not append(symbol.line):
                truncated = True
                exhausted = True
                break
            symbol_count += 1
        if exhausted:
            break

    if mapped_files < len(outlines) or symbol_count < total_symbols:
        truncated = True
    return {
        "format": "python_ast_outline_v2",
        "map": "\n".join(output_lines),
        "stats": {
            "python_files": len(python_files), "mapped_files": mapped_files,
            "symbols": symbol_count, "parse_errors": parse_error_count,
        },
        "parse_errors": parse_errors,
        "truncated": truncated,
        "ranking": {"issue_terms": list(terms), "budget_pressure": pressure},
    }
