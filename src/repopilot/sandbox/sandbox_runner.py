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

from repopilot.retrieval import RetrievalConfig, select_context


WORKSPACE = Path(os.environ.get("REPOPILOT_WORKSPACE", "/workspace"))
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
    retrieval_config = RetrievalConfig(**dict(args.get("_retrieval") or {"strategy": "structural"}))
    retrieval_result = select_context(root, WORKSPACE, str(args.get("_issue", "")), retrieval_config)
    retrieval_artifact = retrieval_result.artifact()
    legacy = retrieval_result.metadata.get("legacy_context")
    repository_context = dict(legacy) if isinstance(legacy, dict) else {
        "format": "repository_retrieval_v1",
        "map": retrieval_result.context,
        "stats": {
            "python_files": retrieval_result.scanned_files,
            "mapped_files": retrieval_result.selected_files,
            "symbols": retrieval_result.selected_chunks,
            "parse_errors": 0,
        },
        "parse_errors": [],
        "truncated": retrieval_result.truncated,
        "ranking": {},
    }
    retrieval_artifact.pop("context", None)
    repository_context["retrieval"] = retrieval_artifact
    return {
        "files": paths[:500],
        "count": len(paths),
        "truncated": truncated,
        "repository_context": repository_context,
    }


def search_code(args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query")
    if not isinstance(query, str) or not query or len(query) > 200:
        raise ValueError("query must be a non-empty string of at most 200 characters")
    root = safe_path(str(args.get("path", ".")))
    if not root.is_dir():
        raise ValueError("search path must be a directory")
    #llm这次想用普通文本搜索还是regular expression（正则表达式）搜索?
    '''
    use_regex = False
    → literal search

    use_regex = True
    → regex search
    '''
    use_regex = bool(args.get("regex", False))
    '''
    这个也不是“把 query 语义拆成不同块”。
    完全没有语义分析。
    它只是把 query 变成 Python re 可以拿来搜索的 pattern
    '''
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
            #这一行里有没有匹配 query/pattern？
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
    #这里的1,400是默认参数值
    start = int(args.get("start_line", 1))
    end = int(args.get("end_line", 400))
    if start < 1 or end < start or end - start + 1 > 400:
        raise ValueError("line range must contain between 1 and 400 lines")
    lines = path.read_text(encoding="utf-8").splitlines()
    #遍历用户想读的行号范围，然后把对应文本取出来。
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
    '''
    假设patch是:
        *** Begin Patch
        *** Update File: calculator.py
        @@
        -    if b <= 0:
        +    if b == 0:
        *** End Patch
    parse_patch_envelope() 大概会整理成：
        value = "calculator.py"
        hunks = [
            [
                "-    if b <= 0:",
                "+    if b == 0:"
            ]
        ]
    '''
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
            '''
                " "  = 上下文，修改前后都保留
                "-"  = 旧内容，要删掉
                "+"  = 新内容，要加进去
            '''
            before = [line[1:] for line in hunk if line[0] in {" ", "-"}]
            after = [line[1:] for line in hunk if line[0] in {" ", "+"}]
            if not before:
                raise ValueError(f"patch hunk for {value} has no matching context")
            matches = [
                index
                for index in range(len(lines) - len(before) + 1)
                if lines[index : index + len(before)] == before
            ]
            #我不仅要找到位置，还必须唯一定位。
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
     #“哦，这是 RepoPilot 支持的 apply-patch envelope 格式，那交给 apply_patch_envelope()。否则就默认按 Git diff 走。”
    if patch.startswith("*** Begin Patch\n"):
        return apply_patch_envelope(patch)

    patch, paths, ignored_paths = filter_git_patch(patch)
    patch_paths(patch)
    # First check whether the patch applies without modifying the worktree.
    check = subprocess.run(
        git_command("apply", "--check", "--whitespace=nowarn", "-"),
        cwd=WORKSPACE,
        input=patch,
        text=True,
        capture_output=True,
        timeout=10,
    )
    # Reject the patch without changing files when the dry run fails.
    if check.returncode != 0:
        raise ValueError(f"patch check failed: {check.stderr.strip()}")
    # Apply only after the dry run succeeds in the isolated worktree.
    applied = subprocess.run(
        git_command("apply", "--whitespace=nowarn", "-"),
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
    allowed_commands = set(ALLOWED_TEST_COMMANDS)
    trusted_raw = os.environ.get("REPOPILOT_TRUSTED_TEST_COMMAND")
    if trusted_raw:
        try:
            trusted = json.loads(trusted_raw)
        except json.JSONDecodeError:
            trusted = None
        if isinstance(trusted, list) and trusted and all(isinstance(item, str) for item in trusted):
            allowed_commands.add(tuple(trusted))
    if command not in allowed_commands:
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


def git_command(*arguments: str) -> list[str]:
    # Linux bind mounts retain the host/controller UID, unlike Docker Desktop.
    # Trust only the controller-selected staged workspace, for this command;
    # never disable ownership checks globally or forward host Git configuration.
    return ["git", "-c", f"safe.directory={WORKSPACE}", *arguments]


def git_diff(args: dict[str, Any]) -> dict[str, Any]:
    subprocess.run(git_command("add", "-N", "."), cwd=WORKSPACE, capture_output=True, timeout=10)
    diff = subprocess.run(
        git_command("diff", "--no-ext-diff", "--unified=3", "HEAD", "--", "."),
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout
    names = subprocess.run(
        git_command("diff", "--name-only", "HEAD", "--", "."),
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
        git_command("init", "-q", "-b", "main"),
        git_command("config", "user.name", "RepoPilot"),
        git_command("config", "user.email", "repopilot@invalid.local"),
        git_command("add", "."),
        git_command("commit", "-q", "-m", "sandbox baseline"),
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
    #sys.argv 的意思是python程序启动时,从命令行收到的参数列表
    #这里就是我要求启动这个程序的时候一共必须有三个argv,而且第二个东西必须是合法的工具名
    #因为sys.argv[0]是程序/脚本本身,真正穿进去的是第一个参数从[1]开始
    '''
    argv[0] = sandbox_runner.py
    argv[1] = tool name
    argv[2] = arguments
    '''
    if len(sys.argv) != 3 or sys.argv[1] not in TOOLS:
        print(json.dumps({"ok": False, "error": "invalid sandbox runner invocation"}))
        return 2
    try:
        arguments = json.loads(sys.argv[2])
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        #总文件的开关
        result = TOOLS[sys.argv[1]](arguments)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
