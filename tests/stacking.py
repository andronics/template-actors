"""Render stacked projects for the tests: base, then one stack overlay, from tagged copies.

Copier records each template's `_commit`, and overlays check base's against the version they
need, so a test renders from a throwaway copy of a template's working tree (uncommitted
changes included), committed and tagged there. The template repositories get no tags.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Rendered projects must pick their own interpreter and venv, as a fresh checkout would.
for _var in ("UV_PYTHON", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
    os.environ.pop(_var, None)

from copier import run_copy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GIT = ("git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
       "-c", "core.hooksPath=/dev/null")  # fmt: skip
IGNORE = shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", "graphify-out", ".graphify")


def sh(*cmd: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    assert result.returncode == 0, f"{' '.join(cmd)} failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout


def stage(src: Path, dst: Path, tag: str = "v1.0.0") -> Path:
    """A git copy of src's working tree, committed and tagged `tag`."""
    shutil.copytree(src, dst, ignore=IGNORE)
    sh("git", "init", "-q", "-b", "main", cwd=dst)
    sh("git", "add", "-A", cwd=dst)
    sh(*GIT, "commit", "-qm", "stage", cwd=dst)
    sh("git", "tag", tag, cwd=dst)
    return dst


def copy(template: Path, dst: Path, data: dict[str, object], **kwargs: object) -> None:
    run_copy(str(template), dst, data=data, defaults=True, unsafe=True, quiet=True, skip_tasks=True, **kwargs)


def commit(dst: Path) -> Path:
    """A rendered project as a fresh repository with one commit (the checks read git)."""
    if not (dst / ".git").exists():
        sh("git", "init", "-q", "-b", "main", cwd=dst)
    sh("git", "add", "-A", cwd=dst)
    sh(*GIT, "commit", "-qm", "render", "--allow-empty", cwd=dst)
    return dst


def checks(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", "scripts/checks.sh"], cwd=project, capture_output=True, text=True)


def sibling(name: str, env: str) -> Path | None:
    """Another template's checkout: $env, or ../<name> next to this repository."""
    path = Path(os.environ.get(env) or ROOT.parent / name)
    return path if (path / "copier.yml").is_file() else None
