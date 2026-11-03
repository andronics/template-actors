from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from stacking import ROOT, commit, copy, sh, sibling, stage  # noqa: E402

# Whether this run is itself in CI, read before the GITHUB_* variables are removed below.
RUNNER_CI = os.environ.get("CI") == "true" or "GITHUB_ACTIONS" in os.environ

# Each render gets a venv: link it to uv's cache instead of copying every package into it
# (hardlinked instead when pytest_configure moves the renders onto the cache's filesystem).
USER_LINK_MODE = "UV_LINK_MODE" in os.environ
os.environ.setdefault("UV_LINK_MODE", "symlink")
# A rendered project's checks must behave as on a developer's machine or in its own CI, not as
# a step of this repository's run: GITHUB_ACTIONS turns check-wiki.py's summary into ::warning
# annotations, and on this repository's Release PR GITHUB_HEAD_REF (release-please--...) puts the
# project's checks in their strict Release-PR mode. So no GITHUB_* variable reaches them. CI=true
# stays, so a missing tool fails as it would in the project's CI.
for _var in [v for v in os.environ if v.startswith("GITHUB_")]:
    del os.environ[_var]

# The base version this overlay needs, and its tests render (staged copies are tagged with it).
BASE_VERSION = "v2.0.0"
BASE = {"project_name": "demo", "description": "A demo project", "github_owner": "metalayerlab"}

# Stack answers for each render: (stack, answers). This overlay names no stack: any will do.
# Each renders when that stack is checked out next to this repository (or $TEMPLATE_<STACK>).
MATRIX: dict[str, tuple[str, dict[str, object]]] = {
    "python": ("python", {"kind": "cli"}),
    "typescript": ("typescript", {"kind": "cli"}),
    "obsidian": ("obsidian", {"vault_dir": "vault"}),
}

FIXTURES = Path(__file__).parent / "fixtures"


def staged(name: str, env: str, factory: pytest.TempPathFactory) -> Path | None:
    found = sibling(name, env)
    return stage(found, factory.mktemp("src") / name) if found else None


def on_tmpfs(path: Path) -> bool:
    """Whether path is on a tmpfs (RAM): its longest mount point in /proc/mounts."""
    try:
        mounts = [line.split()[1:3] for line in Path("/proc/mounts").read_text().splitlines()]
    except OSError:
        return False
    fstype = max(((m, t) for m, t in mounts if path.resolve().is_relative_to(m)), key=lambda mt: len(mt[0]))[1]
    return fstype == "tmpfs"


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Locally, keep the renders off a tmpfs /tmp: they hold every variant's project, and a tmpfs
    is RAM (and often quota'd). Without --basetemp, the temp root (pytest's numbered basetemps,
    Copier's clones, the projects' own temp files) becomes ~/.cache/template-actors-pytest-tmp.
    It sets TMPDIR, not --basetemp: pytest's numbered dirs under it let two runs (worktrees) share
    it without deleting each other's. CI's runners keep their default."""
    if config.option.basetemp or RUNNER_CI or not on_tmpfs(Path(tempfile.gettempdir())):
        return
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "template-actors-pytest-tmp"
    root.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(root)
    tempfile.tempdir = None  # gettempdir() re-reads TMPDIR
    # On uv's cache's filesystem, hardlinked venvs take no more disk than symlinked ones, and they
    # survive a `uv cache prune` or `uv cache clean` during the run.
    cache = Path(subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True).stdout.strip())
    if not USER_LINK_MODE and cache.is_dir() and cache.stat().st_dev == root.stat().st_dev:
        os.environ["UV_LINK_MODE"] = "hardlink"


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    """-n auto runs at most 4 workers locally: each holds the renders of its variants. CI keeps xdist's count."""
    return None if RUNNER_CI else min(4, os.cpu_count() or 1)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """One xdist worker per variant (--dist loadgroup), so each variant renders once per run, not once per worker."""
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is not None and "variant" in callspec.params:
            item.add_marker(pytest.mark.xdist_group(callspec.params["variant"]))


@pytest.fixture(scope="session")
def base_src(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """template-base: $TEMPLATE_BASE, or ../template-base."""
    found = sibling("template-base", "TEMPLATE_BASE")
    src = stage(found, tmp_path_factory.mktemp("src") / "template-base", tag=BASE_VERSION) if found else None
    if src is None:
        pytest.fail("template-base must be checked out next to this repository, or set TEMPLATE_BASE")
    return src


@pytest.fixture(scope="session")
def stacks(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path | None]:
    """The stacks next to this repository ($TEMPLATE_PYTHON, $TEMPLATE_TYPESCRIPT, $TEMPLATE_OBSIDIAN)."""
    return {s: staged(f"template-{s}", f"TEMPLATE_{s.upper()}", tmp_path_factory) for s in ("python", "typescript", "obsidian")}


@pytest.fixture(scope="session")
def actors_src(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return stage(ROOT, tmp_path_factory.mktemp("src") / "template-actors")


def render(base_src: Path, stack_src: Path | None, actors_src: Path | None, dst: Path, stack: dict,
           actors: dict | None = None, bootstrap: bool = True) -> Path:  # fmt: skip
    if stack_src is None:
        pytest.skip("the stack overlay isn't checked out next to this repository")
    copy(base_src, dst, BASE)
    copy(stack_src, dst, stack)
    if actors_src is not None:
        copy(actors_src, dst, actors or {})
    if bootstrap:
        sh("sh", "scripts/bootstrap.sh", cwd=dst)
    return commit(dst)


@pytest.fixture(scope="session", params=list(MATRIX))
def variant(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(scope="session")
def project(variant: str, base_src: Path, stacks: dict[str, Path | None], actors_src: Path,
            tmp_path_factory: pytest.TempPathFactory) -> Path:  # fmt: skip
    stack, answers = MATRIX[variant]
    return render(base_src, stacks[stack], actors_src, tmp_path_factory.mktemp(variant) / "demo", answers)
