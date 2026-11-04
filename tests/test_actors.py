"""Render template-base, a stack (template-python, template-typescript, template-obsidian), then
template-actors, and check the projects that result.

    uv run pytest -n 4            # every test (siblings: ../template-base, ../template-python, ...)
    uv run pytest -m docker       # also actionlint over the render, through Docker

Each render must pass its Definition of done's checks unchanged (`sh scripts/checks.sh`), with no
actors declared and with the fixtures' actors.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import BASE, FIXTURES, MATRIX, render
from stacking import GIT, ROOT, checks, commit, copy, sh, stage

WORKFLOWS = {"actors-automerge.yml", "actors-labels.yml"}
SCRIPTS = {"actors.py", "actors-setup.py", "actors-cron.sh", "actors-avatar.py", "checks.d/40-actors.sh"}


def run(project: Path, *cmd: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
    return subprocess.run(cmd, cwd=project, capture_output=True, text=True, env={**clean, **(env or {})})


def declare(project: Path, dst: Path) -> Path:
    """A copy of project with the fixtures' actors declared, their workflows stubbed, and the docs written."""
    shutil.copytree(project, dst, symlinks=True)
    shutil.copytree(FIXTURES / "actors", dst / ".github/actors", dirs_exist_ok=True)
    for manifest in (dst / ".github/actors").glob("*.yml"):
        for wf in (yaml.safe_load(manifest.read_text()).get("trigger") or {}).get("workflows", []):
            path = dst / ".github/workflows" / wf
            if not path.exists():
                path.write_text(f"name: {wf}\non: workflow_dispatch\njobs: {{}}\n")
    result = run(dst, "uv", "run", "--quiet", "scripts/actors.py", "docs")
    assert result.returncode == 0, result.stdout + result.stderr
    return commit(dst)


@pytest.fixture(scope="session")
def declared(project: Path, variant: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return declare(project, tmp_path_factory.mktemp(f"{variant}-declared") / "demo")


def test_renders_once_per_variant(request: pytest.FixtureRequest, variant: str) -> None:
    """Under xdist, each variant's tests share one worker, so it renders once (conftest.py, --dist loadgroup)."""
    if os.environ.get("PYTEST_XDIST_WORKER") and request.config.getoption("dist") == "loadgroup":
        assert request.node.nodeid.endswith(f"@{variant}"), "xdist isn't grouping by variant"


def test_checks_pass(project: Path) -> None:
    """No actors yet: the drop-in passes, with one line."""
    result = checks(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == "00-base OK"
    assert any(line.startswith("40-actors OK (0 actors") for line in result.stdout.splitlines()), result.stdout


def test_checks_pass_with_actors(declared: Path) -> None:
    result = run(declared, "sh", "scripts/checks.sh", env={"CI": "true"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "40-actors OK (8 actors)" in result.stdout


def test_checks_fail_on_a_bad_manifest(declared: Path, tmp_path: Path) -> None:
    project = tmp_path / "p"
    shutil.copytree(declared, project, symlinks=True)
    night = project / ".github/actors/night-scribe.yml"
    night.write_text(night.read_text() + "app_id: 12\n")
    result = run(project, "sh", "scripts/checks.sh", env={"CI": "true"})
    assert result.returncode != 0
    assert "night-scribe.yml: app_id is for github-app actors only (this one is local)" in result.stdout


def test_docs_must_be_current(declared: Path, tmp_path: Path) -> None:
    project = tmp_path / "p"
    shutil.copytree(declared, project, symlinks=True)
    courier = project / ".github/actors/courier.yml"
    courier.write_text(courier.read_text().replace("merge_policy: never", "merge_policy: auto-after-codeowner"))
    result = run(project, "sh", "scripts/checks.sh", env={"CI": "true"})
    assert result.returncode != 0 and "README.md is out of date" in result.stdout
    assert run(project, "uv", "run", "--quiet", "scripts/actors.py", "docs").returncode == 0
    assert run(project, "sh", "scripts/checks.sh", env={"CI": "true"}).returncode == 0


def test_local_check_reads_pass_paths_without_decrypting(declared: Path, tmp_path: Path) -> None:
    """--local (outside CI) confirms each live App's key has a .gpg file; it never runs pass or gpg."""
    store = tmp_path / "store"
    for key in ("actors/tagwarden/private-key", "actors/courier/private-key"):
        (store / f"{key}.gpg").parent.mkdir(parents=True, exist_ok=True)
        (store / f"{key}.gpg").write_bytes(b"not really encrypted")
    env = {"PASSWORD_STORE_DIR": str(store), "CI": ""}
    result = run(declared, "uv", "run", "--quiet", "scripts/actors.py", "check", "--local", env=env)
    assert result.returncode == 1
    assert result.stdout.strip() == ("error: .github/actors/scout.yml: key actors/scout/private-key isn't in the"
                                     f" password store ({store})")  # fmt: skip
    (store / "actors/scout").mkdir()
    (store / "actors/scout/private-key.gpg").write_bytes(b"x")
    result = run(declared, "uv", "run", "--quiet", "scripts/actors.py", "check", "--local", env=env)
    assert result.returncode == 0 and "pass paths checked" in result.stdout, result.stdout


def test_never_asks_others_questions() -> None:
    """One answer, one place: base's answers are read, never asked (template-base decision 0001)."""
    config = yaml.safe_load((ROOT / "copier.yml").read_text())
    asked = {k for k, v in config.items()
             if not k.startswith("_") and v.get("when") is not False and v.get("ask") is not False}  # fmt: skip
    assert asked == {"theme"}
    assert config["theme"]["choices"].values() and set(config["theme"]["choices"].values()) == {"fantasy", "sci-fi"}
    assert config["_answers_file"] == ".copier-answers.actors.yml"
    assert config["_external_data"] == {"base": ".copier-answers.base.yml"}


def test_files_by_name(project: Path) -> None:
    """Only prefixed workflows and drop-ins, no slot (a feature overlay), and the schema."""
    template = ROOT / "template"
    rendered = {p.relative_to(template).as_posix().removesuffix(".jinja") for p in template.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts}
    assert rendered == {"[[ _copier_conf.answers_file ]]", ".github/actors/actor.schema.json"} | {
        f".github/workflows/{w}" for w in WORKFLOWS} | {f"scripts/{s}" for s in SCRIPTS}  # fmt: skip
    assert {p.name for p in (project / ".github/workflows").glob("actors-*")} == WORKFLOWS
    assert (project / ".github/actors/actor.schema.json").is_file()
    answers = yaml.safe_load((project / ".copier-answers.actors.yml").read_text())
    assert answers["theme"] == "fantasy" and answers["base_answers"]["project_name"] == "demo"


def test_marked(project: Path) -> None:
    for rel in [f".github/workflows/{w}" for w in WORKFLOWS] + [f"scripts/{s}" for s in SCRIPTS]:
        head = "\n".join((project / rel).read_text().splitlines()[:12])
        assert "Template-owned: template-actors" in head, rel


def test_no_owner_or_repo_hard_coded() -> None:
    """Owners and repositories come from the manifests and Copier answers, never the template."""
    for path in (ROOT / "template").rglob("*"):
        if path.is_file():
            text = path.read_text()
            # The template repositories themselves (andronics/template-*) are sources, not targets.
            assert not re.search(r"\b(andronics|metalayerlab)/(?!template-)", text), path


def test_new_uses_the_theme(base_src: Path, stacks: dict, actors_src: Path, tmp_path: Path) -> None:
    project = render(base_src, stacks["python"], actors_src, tmp_path / "p", {"kind": "cli"}, {"theme": "sci-fi"},
                     bootstrap=False)  # fmt: skip
    result = run(project, "uv", "run", "--quiet", "scripts/actors.py", "new", "stardate")
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = yaml.safe_load((project / ".github/actors/stardate.yml").read_text())
    assert manifest["theme"] == "sci-fi" and manifest["status"] == "planned"
    # A planned skeleton is valid as written, once the docs are regenerated.
    assert run(project, "uv", "run", "--quiet", "scripts/actors.py", "docs").returncode == 0
    result = run(project, "uv", "run", "--quiet", "scripts/actors.py", "check")
    assert result.returncode == 0, result.stdout


@pytest.mark.docker
def test_actionlint(project: Path) -> None:
    cmd = ("docker", "run", "--rm", "-v", f"{project}:/repo", "-w", "/repo", "rhysd/actionlint:1.7.12", "-color")
    result = run(project, *cmd)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("case", "error"), [
    ("no base", "needs template-base first"),
    ("old base", "needs template-base v2.0.0 or later; this project has v1.0.0"),
])  # fmt: skip
def test_needs_base(case: str, error: str, base_src: Path, actors_src: Path, tmp_path: Path) -> None:
    dst = tmp_path / "p"
    if case == "old base":
        copy(stage(base_src, tmp_path / "old-base", tag="v1.0.0"), dst, BASE)
    with pytest.raises(Exception, match=re.escape(error)):
        copy(actors_src, dst, {})


def test_update_keeps_answers_and_manifests(base_src: Path, stacks: dict, actors_src: Path, tmp_path: Path) -> None:
    """`copier update` replays both versions without base's answers file; the recorded copy keeps them, and
    the project's manifests (not the template's) are left alone."""
    project = render(base_src, stacks["obsidian"] or stacks["python"], actors_src, tmp_path / "p",
                     MATRIX["obsidian" if stacks["obsidian"] else "python"][1], {"theme": "sci-fi"},
                     bootstrap=False)  # fmt: skip
    shutil.copy(FIXTURES / "actors/courier.yml", project / ".github/actors/courier.yml")
    commit(project)
    newer = tmp_path / "newer"
    shutil.copytree(actors_src, newer)
    labels = newer / "template/.github/workflows/actors-labels.yml"
    labels.write_text(labels.read_text().replace("on demand.", "on demand (newer)."))
    sh(*GIT, "commit", "-qam", "newer", cwd=newer)
    sh("git", "tag", "v1.0.1", cwd=newer)
    answers = project / ".copier-answers.actors.yml"
    answers.write_text(answers.read_text().replace(str(actors_src), str(newer)))
    commit(project)
    sh("uvx", "--from", "copier>=9.19,<10", "copier", "update", "-a", ".copier-answers.actors.yml", "--trust",
       "--skip-tasks", "--defaults", "--conflict", "inline", "--vcs-ref", "v1.0.1", cwd=project)  # fmt: skip
    changed = sorted(line[3:] for line in sh("git", "status", "--porcelain", cwd=project).splitlines())
    assert changed == [".copier-answers.actors.yml", ".github/workflows/actors-labels.yml"]
    assert yaml.safe_load(answers.read_text())["theme"] == "sci-fi"
    assert "<<<<<<<" not in sh("git", "diff", cwd=project)


# Outside the Basic Multilingual Plane (💡), CJK, a quote and a backslash: `tojson` wrote the emoji
# as a surrogate pair, which YAML reads back as two characters.
UNICODE = 'Ideas 💡 in 漢字, with a "quote" and a back\\slash'


def test_unicode_description(base_src: Path, stacks: dict, actors_src: Path, tmp_path: Path) -> None:
    """Any Unicode in base's description renders: the recorded base answers match base's file (no
    "stale" error), and the project passes its checks."""
    stack = "obsidian" if stacks["obsidian"] else "python"
    project = render(base_src, stacks[stack], actors_src, tmp_path / "demo", MATRIX[stack][1],
                     base={"description": UNICODE})  # fmt: skip
    answers = yaml.safe_load((project / ".copier-answers.actors.yml").read_text())
    assert answers["base_answers"]["description"] == UNICODE
    result = checks(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(line.startswith("40-actors OK (0 actors") for line in result.stdout.splitlines()), result.stdout
