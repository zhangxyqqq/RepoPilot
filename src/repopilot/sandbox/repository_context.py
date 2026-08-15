from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any


EXCLUDED_DIRECTORIES = {".git", ".pytest_cache", "__pycache__"}


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
    return (
        "tests" in relative_path.parts
        or relative_path.name.startswith("test_")
        or relative_path.name.endswith("_test.py")
    )


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
            name
            for name in directories
            if name not in EXCLUDED_DIRECTORIES and not (Path(current) / name).is_symlink()
        )
        for name in sorted(files):
            candidate = Path(current) / name
            if candidate.suffix == ".py" and candidate.is_file() and not candidate.is_symlink():
                paths.append(candidate)
    return paths


def build_repository_context(
    root: Path,
    workspace: Path,
    *,
    max_files: int = 100,
    max_symbols: int = 300,
    max_chars: int = 8_000,
    max_file_bytes: int = 1_000_000,
) -> dict[str, Any]:
    """Build a bounded, read-only Python outline using the standard-library AST."""

    python_files = _python_files(root)
    output_lines: list[str] = []
    output_chars = 0
    mapped_files = 0
    symbol_count = 0
    parse_error_count = 0
    parse_errors: list[dict[str, Any]] = []
    truncated = len(python_files) > max_files

    def append(line: str) -> bool:
        nonlocal output_chars, truncated
        added = len(line) + (1 if output_lines else 0)
        if output_chars + added > max_chars:
            truncated = True
            return False
        output_lines.append(line)
        output_chars += added
        return True

    for path in python_files[:max_files]:
        relative = path.relative_to(workspace)
        if path.stat().st_size > max_file_bytes:
            parse_error_count += 1
            if len(parse_errors) < 20:
                parse_errors.append({"path": str(relative), "error": "file exceeds AST size limit"})
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(relative), type_comments=True)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            parse_error_count += 1
            if len(parse_errors) < 20:
                parse_errors.append(
                    {
                        "path": str(relative),
                        "line": getattr(exc, "lineno", None),
                        "error": type(exc).__name__,
                    }
                )
            continue

        role = "test" if _is_test_path(relative) else "source"
        if not append(f"{relative} [module={_module_name(relative)}, role={role}]"):
            break
        mapped_files += 1
        imports = _imports(tree)
        if imports and not append(f"  imports: {', '.join(imports[:16])}"):
            break
        if len(imports) > 16:
            truncated = True

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if symbol_count >= max_symbols or not append(f"  {_signature(node)} @L{node.lineno}"):
                    truncated = True
                    break
                symbol_count += 1
            elif isinstance(node, ast.ClassDef):
                bases = f"({', '.join(_expression(base) for base in node.bases)})" if node.bases else ""
                if symbol_count >= max_symbols or not append(f"  class {node.name}{bases} @L{node.lineno}"):
                    truncated = True
                    break
                symbol_count += 1
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if symbol_count >= max_symbols or not append(
                            f"    {_signature(member)} @L{member.lineno}"
                        ):
                            truncated = True
                            break
                        symbol_count += 1
            if truncated and (symbol_count >= max_symbols or output_chars >= max_chars):
                break
        if truncated and (symbol_count >= max_symbols or output_chars >= max_chars):
            break

    return {
        "format": "python_ast_outline_v1",
        "map": "\n".join(output_lines),
        "stats": {
            "python_files": len(python_files),
            "mapped_files": mapped_files,
            "symbols": symbol_count,
            "parse_errors": parse_error_count,
        },
        "parse_errors": parse_errors,
        "truncated": truncated,
    }
