from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from repopilot.sandbox.repository_context import build_repository_context
except ModuleNotFoundError:
    from repository_context import build_repository_context


WORKSPACE = Path("/workspace")
MAX_TEXT_BYTES = 1_000_000
MAX_RESULTS = 200
ALLOWED_TEST_COMMANDS = {("python", "-m", "pytest", "-q")}
DENIED_PARTS = {".git", ".env", ".ssh", ".aws", ".azure", ".gnupg"}


def bounded(text: str, limit: int = 20_000) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[truncated]", True


def safe_path(value: str, *, must_exist: bool = True) -> Path:
    pure = PurePosixPath(value or ".")
    if pure.is_absolute() or ".." in pure.parts or any(part in DENIED_PARTS for part in pure.parts):
        raise ValueError("path must remain inside the repository workspace")
    candidate = WORKSPACE.joinpath(*pure.parts)
    resolved = candidate.resolve(strict=must_exist)
    if resolved != WORKSPACE and WORKSPACE not in resolved.parents:
        raise ValueError("resolved path escapes the repository workspace")
    return resolved


def repository_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if name not in {".git", "__pycache__", ".pytest_cache"}
            and not (Path(current) / name).is_symlink()
        )
        for name in sorted(names):
            candidate = Path(current) / name
            if not candidate.is_symlink() and candidate.is_file():
                files.append(candidate)
    return files


def list_files(args: dict[str, Any]) -> dict[str, Any]:
    root = safe_path(str(args.get("path", ".")))
    if not root.is_dir():
        raise ValueError("list_files path must be a directory")
    paths = [str(path.relative_to(WORKSPACE)) for path in repository_files(root)]
    truncated = len(paths) > 500
    return {
        "files": paths[:500],
        "count": len(paths),
        "truncated": truncated,
        "repository_context": build_repository_context(
            root,
            WORKSPACE,
            issue=str(args.get("_issue", "")),
        ),
    }


def search_code(args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query")
    if not isinstance(query, str) or not query or len(query) > 200:
        raise ValueError("query must be a non-empty string of at most 200 characters")
    root = safe_path(str(args.get("path", ".")))
    if not root.is_dir():
        raise ValueError("search path must be a directory")
    use_regex = bool(args.get("regex", False))
    pattern = re.compile(query if use_regex else re.escape(query))
    matches: list[dict[str, Any]] = []
    for path in repository_files(root):
        if path.stat().st_size > MAX_TEXT_BYTES:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            if pattern.search(line):
                matches.append(
                    {"path": str(path.relative_to(WORKSPACE)), "line": number, "text": line[:500]}
                )
                if len(matches) >= MAX_RESULTS:
                    return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


def read_file(args: dict[str, Any]) -> dict[str, Any]:
    value = args.get("path")
    if not isinstance(value, str):
        raise ValueError("path is required")
    path = safe_path(value)
    if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
        raise ValueError("file is missing or exceeds the read limit")
    start = int(args.get("start_line", 1))
    end = int(args.get("end_line", 400))
    if start < 1 or end < start or end - start + 1 > 400:
        raise ValueError("line range must contain between 1 and 400 lines")
    lines = path.read_text(encoding="utf-8").splitlines()
    selected = [f"{number}: {lines[number - 1]}" for number in range(start, min(end, len(lines)) + 1)]
    content, truncated = bounded("\n".join(selected))
    return {"path": value, "start_line": start, "end_line": min(end, len(lines)), "content": content, "truncated": truncated}


def patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    if "new file mode 120000" in patch or "old mode 120000" in patch:
        raise ValueError("symbolic-link patches are not permitted")
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            match = re.fullmatch(r"diff --git a/(.+) b/(.+)", line)
            if not match or match.group(1) != match.group(2):
                raise ValueError("renames and malformed diff paths are not supported")
            value = match.group(1)
            safe_path(value, must_exist=False)
            paths.append(value)
        elif line.startswith(("--- ", "+++ ")):
            value = line[4:]
            if value == "/dev/null":
                continue
            if value.startswith(("a/", "b/")):
                value = value[2:]
            safe_path(value, must_exist=False)
    if not paths:
        raise ValueError("patch must contain at least one diff --git header")
    return sorted(set(paths))


def is_protected_test_path(value: str) -> bool:
    path = PurePosixPath(value)
    return "tests" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")


def filter_git_patch(patch: str) -> tuple[str, list[str], list[str]]:
    sections = re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
    if sections and sections[0].strip():
        raise ValueError("patch content before the first diff header is not supported")
    kept: list[str] = []
    paths: list[str] = []
    ignored: list[str] = []
    for section in sections:
        if not section.strip():
            continue
        header = section.splitlines()[0]
        match = re.fullmatch(r"diff --git a/(.+) b/(.+)", header)
        if not match or match.group(1) != match.group(2):
            raise ValueError("renames and malformed diff paths are not supported")
        value = match.group(1)
        safe_path(value, must_exist=False)
        if is_protected_test_path(value):
            ignored.append(value)
        else:
            kept.append(section)
            paths.append(value)
    if not kept:
        raise ValueError("patch contains no editable production files; test files are protected")
    return "".join(kept), sorted(set(paths)), sorted(set(ignored))


def parse_patch_envelope(patch: str) -> list[tuple[str, list[list[str]]]]:
    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("invalid apply-patch envelope")
    sections: list[tuple[str, list[list[str]]]] = []
    current_path: str | None = None
    current_hunks: list[list[str]] = []
    current_hunk: list[str] | None = None
    for line in lines[1:-1]:
        if line.startswith("*** Update File: "):
            if current_path is not None:
                if current_hunk is not None:
                    current_hunks.append(current_hunk)
                sections.append((current_path, current_hunks))
            current_path = line.removeprefix("*** Update File: ")
            safe_path(current_path)
            current_hunks = []
            current_hunk = None
        elif line.startswith("@@"):
            if current_path is None:
                raise ValueError("patch hunk appeared before an Update File header")
            if current_hunk is not None:
                current_hunks.append(current_hunk)
            current_hunk = []
        elif line.startswith("*** "):
            raise ValueError("only Update File sections are supported")
        else:
            if current_hunk is None or not line.startswith((" ", "+", "-")):
                raise ValueError("malformed apply-patch hunk")
            current_hunk.append(line)
    if current_path is not None:
        if current_hunk is not None:
            current_hunks.append(current_hunk)
        sections.append((current_path, current_hunks))
    if not sections or any(not hunks for _, hunks in sections):
        raise ValueError("patch envelope must contain at least one update hunk")
    return sections


def apply_patch_envelope(patch: str) -> dict[str, Any]:
    updates: dict[Path, str] = {}
    changed_paths: list[str] = []
    ignored_paths: list[str] = []
    for value, hunks in parse_patch_envelope(patch):
        if is_protected_test_path(value):
            ignored_paths.append(value)
            continue
        path = safe_path(value)
        if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
            raise ValueError(f"updated file is missing or too large: {value}")
        original = updates.get(path, path.read_text(encoding="utf-8"))
        lines = original.splitlines()
        for hunk in hunks:
            before = [line[1:] for line in hunk if line[0] in {" ", "-"}]
            after = [line[1:] for line in hunk if line[0] in {" ", "+"}]
            if not before:
                raise ValueError(f"patch hunk for {value} has no matching context")
            matches = [
                index
                for index in range(len(lines) - len(before) + 1)
                if lines[index : index + len(before)] == before
            ]
            if len(matches) != 1:
                raise ValueError(f"patch context for {value} matched {len(matches)} locations")
            index = matches[0]
            lines[index : index + len(before)] = after
        updated = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
        if updated == original:
            raise ValueError(f"patch made no change to {value}")
        updates[path] = updated
        if value not in changed_paths:
            changed_paths.append(value)
    if not updates:
        raise ValueError("patch contains no editable production files; test files are protected")
    for path, content in updates.items():
        path.write_text(content, encoding="utf-8")
    return {
        "changed": True,
        "format": "apply_patch_envelope",
        "paths": sorted(set(changed_paths)),
        "ignored_paths": sorted(set(ignored_paths)),
    }


def apply_patch(args: dict[str, Any]) -> dict[str, Any]:
    patch = args.get("patch")
    if not isinstance(patch, str) or not patch or len(patch) > 50_000:
        raise ValueError("patch must be a non-empty unified diff of at most 50,000 characters")
    if patch.startswith("*** Begin Patch\n"):
        return apply_patch_envelope(patch)
    patch, paths, ignored_paths = filter_git_patch(patch)
    patch_paths(patch)
    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        cwd=WORKSPACE,
        input=patch,
        text=True,
        capture_output=True,
        timeout=10,
    )
    if check.returncode != 0:
        raise ValueError(f"patch check failed: {check.stderr.strip()}")
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=WORKSPACE,
        input=patch,
        text=True,
        capture_output=True,
        timeout=10,
    )
    if applied.returncode != 0:
        raise ValueError(f"patch application failed: {applied.stderr.strip()}")
    return {
        "changed": True,
        "format": "git_diff",
        "paths": paths,
        "ignored_paths": ignored_paths,
    }


def run_tests(args: dict[str, Any]) -> dict[str, Any]:
    command = tuple(args.get("command", ()))
    if command not in ALLOWED_TEST_COMMANDS:
        raise ValueError("test command is not allowlisted")
    timeout = min(max(int(args.get("timeout_seconds", 30)), 1), 120)
    try:
        completed = subprocess.run(
            list(command),
            cwd=WORKSPACE,
            text=True,
            capture_output=True,
            timeout=timeout,
            env={"PATH": os.environ.get("PATH", ""), "HOME": "/home/repopilot", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        output, truncated = bounded(completed.stdout + completed.stderr)
        return {
            "passed": completed.returncode == 0,
            "exit_code": completed.returncode,
            "output": output,
            "truncated": truncated,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or "") + (exc.stderr or "")
        output, truncated = bounded(raw if isinstance(raw, str) else raw.decode(errors="replace"))
        return {"passed": False, "exit_code": None, "output": output, "truncated": truncated, "timed_out": True}


def git_diff(args: dict[str, Any]) -> dict[str, Any]:
    subprocess.run(["git", "add", "-N", "."], cwd=WORKSPACE, capture_output=True, timeout=10)
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=3", "HEAD", "--", "."],
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout
    names = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "."],
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout.splitlines()
    content, truncated = bounded(diff, 50_000)
    return {"diff": content, "changed_files": names, "truncated": truncated}


def init_repo(args: dict[str, Any]) -> dict[str, Any]:
    commands = [
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.name", "RepoPilot"],
        ["git", "config", "user.email", "repopilot@invalid.local"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "sandbox baseline"],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=WORKSPACE, text=True, capture_output=True, timeout=20)
        if completed.returncode != 0:
            raise ValueError(f"repository initialization failed: {completed.stderr.strip()}")
    return {"initialized": True}


TOOLS = {
    "list_files": list_files,
    "search_code": search_code,
    "read_file": read_file,
    "apply_patch": apply_patch,
    "run_tests": run_tests,
    "git_diff": git_diff,
    "_init_repo": init_repo,
}


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in TOOLS:
        print(json.dumps({"ok": False, "error": "invalid sandbox runner invocation"}))
        return 2
    try:
        arguments = json.loads(sys.argv[2])
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        result = TOOLS[sys.argv[1]](arguments)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
