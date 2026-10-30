"""Private file handling for the comms layer.

Everything comms writes (digests, payloads, send logs, gate logs) can
hold guest words, worker numbers, or property IDs. So it is mode 600 in
a mode 700 directory, and never inside a git work tree: a path with a
.git at or above it is refused (package L10: none of it goes in the
public repo). Sources are only ever opened for reading.
"""
import json
import os
from pathlib import Path


class RepoPathError(ValueError):
    """A private comms file was aimed at a path inside a git work tree."""


def inside_git_tree(path) -> bool:
    p = Path(os.path.expanduser(str(path))).resolve()
    return any((parent / ".git").exists() for parent in (p, *p.parents))


def check_private_path(path) -> str:
    expanded = os.path.expanduser(str(path))
    if inside_git_tree(expanded):
        raise RepoPathError(f"{path} is inside a git work tree; private comms "
                            "files stay outside the repo")
    return expanded


def ensure_private_dir(path) -> None:
    check_private_path(path)
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def write_private(path, text: str) -> None:
    check_private_path(path)
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def append_jsonl(path, row: dict) -> None:
    check_private_path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path) -> tuple[list[dict], int]:
    """(rows, unreadable line count). A missing file is ([], 0)."""
    rows, bad = [], 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                else:
                    bad += 1
    except FileNotFoundError:
        pass
    return rows, bad
