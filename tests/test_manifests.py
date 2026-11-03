"""The manifest schema and the actors-* scripts, on their own (no render): template/scripts/ imported or run
against a scratch repository root holding the fixtures' manifests (real actors, from the brain).

    uv run pytest tests/test_manifests.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import random
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from stacking import GIT, ROOT, sh

SCRIPTS = ROOT / "template/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.dont_write_bytecode = True  # no __pycache__ under template/

import actors  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures/actors"
FAKE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nFAKEFAKEFAKE\n-----END RSA PRIVATE KEY-----\n"


def module(name: str):  # noqa: ANN201
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A repository root: the schema, the scripts, the fixtures' manifests and their workflows."""
    root = tmp_path / "repo"
    (root / ".github/workflows").mkdir(parents=True)
    shutil.copytree(FIXTURES, root / ".github/actors")
    shutil.copy(ROOT / "template/.github/actors/actor.schema.json", root / ".github/actors/")
    shutil.copytree(SCRIPTS, root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    for wf in ("release-please.yml", "template-update.yml", "pr-title.yml"):
        (root / ".github/workflows" / wf).write_text("on: workflow_dispatch\n")
    return root


def edit(root: Path, slug: str, **changes: object) -> None:
    path = root / ".github/actors" / f"{slug}.yml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def errors(root: Path) -> list[str]:
    found, errs = actors.load(root)
    return errs or actors.check_all(found, root)


def test_the_real_actors_are_valid(root: Path) -> None:
    assert errors(root) == []


def test_schema_is_valid_json_schema() -> None:
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(actors.schema(ROOT / "template"))


@pytest.mark.parametrize(("slug", "changes", "message"), [
    ("night-scribe", {"app_id": 12}, "night-scribe.yml: app_id is for github-app actors only (this one is local)"),
    ("grafter", {"roles": ["update"]}, "grafter.yml: roles is for github-app actors only"),
    ("herald", {"fallback": "required"}, "herald.yml: fallback is for github-app actors only"),
    ("courier", {"key": None}, "courier.yml: (top level): 'key' is a required property"),
    ("courier", {"status": "zombie"}, "courier.yml: status: 'zombie' is not one of"),
    ("courier", {"identity": "robot"}, "courier.yml: identity: 'robot' is not one of"),
    ("courier", {"merge_policy": "always"}, "courier.yml: merge_policy: 'always' is not one of"),
    ("courier", {"fallback": "maybe"}, "courier.yml: fallback: 'maybe' is not one of"),
    ("courier", {"roles": ["deploy"]}, "courier.yml: roles.0: 'deploy' is not one of"),
    ("courier", {"merge_policy": "auto-on-ci", "branch_prefix": None}, "'branch_prefix' is a required property"),
    ("courier", {"theme": "steampunk"}, "courier.yml: theme: 'steampunk' is not one of"),
    ("courier", {"repos": ["x/y"]}, "Additional properties are not allowed ('repos' was unexpected)"),
    ("courier", {"permissions": {"contents": "everything"}}, "permissions.contents: 'everything' is not one of"),
    ("courier", {"acts_on": ["not a repo"]}, "acts_on.0: 'not a repo' does not match"),
    ("release-bot", {"superseded_by": None}, "release-bot.yml: (top level): 'superseded_by' is a required property"),
    ("courier", {"superseded_by": "tagwarden"}, "superseded_by names one successor, so its status must be superseded"),
    ("herald", {"status": "superseded"}, "a partial supersession), so its status can't be superseded"),
    ("release-bot", {"superseded_by": "release-bot"}, "release-bot.yml: superseded_by names the actor itself"),
    ("tagwarden", {"permissions": {"contents": "write", "workflows": "write"}}, "a release actor never gets workflows"),
    ("scout", {"roles": ["release"]}, "role release is filled by two Apps, andronics-tagwarden (chronicler) and"
                                      " andronics-scout (scout)"),
    ("chronicler", {"client_id": "Iv23liDIFFERENTxxxx"}, "share the App andronics-tagwarden but differ"),
    ("chronicler", {"labels": [{"name": "release", "color": "000000"}]}, "label 'release' is declared differently"),
    ("courier", {"trigger": {"workflows": ["missing.yml"]}}, "trigger.workflows names missing.yml, which isn't in"),
    ("courier", {"avatar": {"file": "courier.jpg"}}, "avatar.file: 'courier.jpg' does not match"),
])  # fmt: skip
def test_rejects(root: Path, slug: str, changes: dict, message: str) -> None:
    edit(root, slug, **changes)
    found = errors(root)
    assert any(message in e for e in found), found


def test_never_echoes_a_secret(root: Path) -> None:
    edit(root, "courier", key=FAKE_KEY)
    found = errors(root)
    assert found == ["error: .github/actors/courier.yml: holds what looks like a private key or token: a manifest"
                     " names only its pass path"]  # fmt: skip
    edit(root, "courier", key="actors/courier/private-key", description="token ghp_" + "a" * 36)
    assert "looks like a private key or token" in errors(root)[0] and "ghp_" not in errors(root)[0]


def test_slug_is_the_file_name(root: Path) -> None:
    (root / ".github/actors/Bad_Name.yml").write_text("name: Bad\n")
    assert any("Bad_Name.yml: the file name is the actor's slug" in e for e in errors(root))


def test_a_retired_app_may_share_a_role(root: Path) -> None:
    """Only live actors (created, installed, active) hold a role's secrets; a superseded one doesn't."""
    edit(root, "release-bot", status="superseded")
    assert errors(root) == []
    edit(root, "release-bot", status="active", superseded_by=None)
    assert any("role release is filled by two Apps" in e for e in errors(root))


# ------------------------------------------------------------------ the PR contract


@pytest.mark.parametrize(("author", "ref", "slug", "merge"), [
    ("andronics-tagwarden[bot]", "release-please--branches--main", "chronicler", None),  # the longest prefix
    ("andronics-tagwarden[bot]", "release-please--other", "tagwarden", None),
    ("andronics-tagwarden[bot]", "feature/x", None, None),  # off its branch prefix
    ("andronics-courier[bot]", "chore/template-update-base", "courier", "squash"),
    ("someone-else[bot]", "chore/x", None, None),
    ("andronics", "auto/nightly", None, None),  # a local actor queues its own
    ("dependabot[bot]", "dependabot/npm/x", None, None),  # template-base's
])  # fmt: skip
def test_automerge_finds_the_actor(root: Path, author: str, ref: str, slug: str | None, merge: str | None) -> None:
    edit(root, "courier", merge_policy="auto-on-ci")
    found, _ = actors.load(root)
    message, actor = actors.automerge(found, author, ref)
    assert (actor or {}).get("slug") == slug, message
    if actor is not None:
        assert (actor["merge_policy"] != "never") == (merge is not None)


def test_automerge_command_writes_outputs(root: Path) -> None:
    edit(root, "courier", merge_policy="auto-after-codeowner")
    result = subprocess.run([sys.executable, "scripts/actors.py", "automerge", "--author", "andronics-courier[bot]",
                             "--head-ref", "chore/template-update"], cwd=root, capture_output=True, text=True)  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["actor=courier", "labels=template-update", "merge=squash"]
    assert "::notice::courier: auto-after-codeowner, queueing auto-merge" in result.stderr


def test_docs(root: Path) -> None:
    found, _ = actors.load(root)
    text = actors.render_docs(found)
    assert "| update | `UPDATE_APP_CLIENT_ID` | `UPDATE_APP_PRIVATE_KEY` | andronics-courier | Courier |" in text
    assert "| release | `RELEASE_APP_CLIENT_ID` | `RELEASE_APP_PRIVATE_KEY` | andronics-tagwarden | Chronicler, Tagwarden |" in text
    assert "| brain-pull | `BRAIN_PULL_APP_CLIENT_ID` |" in text
    assert "| Night Scribe | `auto/nightly` | - | `auto-on-ci` | merge | - |" in text
    assert "| Herald | active | `grafter`: labelling dependency PRs |" in text
    assert "| Release Bot | superseded | `release-bot` |" not in text and "| Release Bot | superseded | `tagwarden` |" in text
    assert "PRIVATE KEY-----" not in text
    assert actors.render_docs({}).endswith("`uv run scripts/actors.py new <slug>`.\n")


def test_labels_dry_run(root: Path) -> None:
    result = subprocess.run([sys.executable, "scripts/actors.py", "labels", "--dry-run", "-R", "o/r"], cwd=root,
                            capture_output=True, text=True)  # fmt: skip
    assert result.returncode == 0, result.stdout
    lines = result.stdout.splitlines()
    assert "would run: gh label create release --force --color 5319e7 --description A Release PR -R o/r" in lines
    assert len([line for line in lines if " release " in line]) == 1  # declared twice, created once


# ------------------------------------------------------------------ vault notes


def test_note_round_trips(root: Path, tmp_path: Path) -> None:
    found, _ = actors.load(root)
    for slug, actor in found.items():
        note = tmp_path / f"{slug}.md"
        note.write_text(actors.render_note(slug, actor, "2026-11-03"))
        assert actors.check_note(slug, actor, note) == [], slug
    text = (tmp_path / "tagwarden.md").read_text()
    front = yaml.safe_load(text.split("---\n")[1])
    assert front["client_id"] == "Iv23liCMzTeOs8PtF13g" and front["permissions"][0] == "contents: write"
    assert front["tags"] == ["actors", "github-app"] and front["avatar"] == "[[tagwarden.png]]"
    assert "## 📖 Legend" in text and "Heraldic emblem" in text
    night = yaml.safe_load((tmp_path / "night-scribe.md").read_text().split("---\n")[1])
    assert "app_id" not in night and night["runs_as"].startswith("the deploy key")


def test_note_check_finds_drift(root: Path, tmp_path: Path) -> None:
    found, _ = actors.load(root)
    note = tmp_path / "courier.md"
    note.write_text(actors.render_note("courier", found["courier"], "2026-11-03").replace("Iv23lik9fo1QtYezn4Me", "Iv23liOLD"))
    assert actors.check_note("courier", found["courier"], note) == [
        "error: courier.md: client_id is 'Iv23liOLD'; the manifest says 'Iv23lik9fo1QtYezn4Me'"]  # fmt: skip


# ------------------------------------------------------------------ actors-setup


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fakes(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A fake pass (one key) and gh (logs each call, and a hash of what it reads on stdin)."""
    bin_dir, log = tmp_path / "bin", tmp_path / "gh.log"
    bin_dir.mkdir()
    store = tmp_path / "store"
    for key in ("tagwarden", "courier", "scout"):
        (store / "actors" / key).mkdir(parents=True)
        (store / "actors" / key / "private-key.gpg").write_bytes(b"x")
    stub(bin_dir, "pass", f"printf '%s' '{FAKE_KEY}'\n")
    stub(bin_dir, "gh", f"""log={log}
case "$1 $2" in
  "secret set") echo "gh $* stdin-sha256=$(sha256sum | cut -d' ' -f1)" >> "$log"; exit 0 ;;
  "repo view") echo o/r; exit 0 ;;
  "api users/o") echo Organization; exit 0 ;;
  "variable list")
    case "$*" in
      *--org*) echo '[{{"name":"RELEASE_APP_CLIENT_ID","value":"Iv23liDELETEDAPP"}}]' ;;
      *) echo '[{{"name":"SCOUT_APP_CLIENT_ID","value":"Iv23liz818GMJcZrcMSo"}},{{"name":"OTHER_APP_CLIENT_ID","value":"x"}}]' ;;
    esac; exit 0 ;;
  "secret list") echo '[]'; exit 0 ;;
esac
echo "gh $*" >> "$log"
""")  # fmt: skip
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PASSWORD_STORE_DIR": str(store)}
    return env, log


def setup(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "scripts/actors-setup.py", *args], cwd=root, env=env,
                          capture_output=True, text=True)  # fmt: skip


def test_setup_dry_run_changes_nothing(root: Path, fakes: tuple[dict[str, str], Path]) -> None:
    env, log = fakes
    result = setup(root, env, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "would set o/r variable RELEASE_APP_CLIENT_ID=Iv23liCMzTeOs8PtF13g (release)" in out
    assert "would run pass show actors/courier/private-key | gh secret set UPDATE_APP_PRIVATE_KEY -R o/r" in out
    assert "would set o/r variable BRAIN_PULL_APP_CLIENT_ID=Iv23liz818GMJcZrcMSo (brain-pull)" in out
    assert "PIN_BUMP" not in out.split("warning")[0]
    assert "warning: org o has RELEASE_APP_CLIENT_ID=Iv23liDELETEDAPP, but the manifests say Iv23liCMzTeOs8PtF13g" in out
    assert "warning: o/r has the legacy SCOUT_APP_CLIENT_ID" in out
    assert "warning: o/r has OTHER_APP_CLIENT_ID, which no live actor here fills" in out
    assert "would run: gh label create release" in out
    written = log.read_text() if log.exists() else ""
    assert "secret set" not in written and "variable set" not in written and "label create" not in written
    assert "FAKE" not in out + result.stderr


def test_setup_pipes_the_key_and_never_prints_it(root: Path, fakes: tuple[dict[str, str], Path]) -> None:
    env, log = fakes
    result = setup(root, env, "--legacy", "--no-labels")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAKE" not in result.stdout + result.stderr and "BEGIN" not in result.stdout + result.stderr
    calls = log.read_text().splitlines()
    digest = hashlib.sha256(FAKE_KEY.encode()).hexdigest()
    for name in ("RELEASE", "UPDATE", "BRAIN_PULL", "PIN_BUMP", "SCOUT"):
        assert f"gh secret set {name}_APP_PRIVATE_KEY -R o/r stdin-sha256={digest}" in calls, calls
    assert "gh variable set UPDATE_APP_CLIENT_ID --body Iv23lik9fo1QtYezn4Me -R o/r" in calls
    assert "gh variable set PIN_BUMP_APP_CLIENT_ID --body Iv23lik9fo1QtYezn4Me -R o/r" in calls
    assert not any("--body" in c and "BEGIN" in c for c in calls)


def test_setup_refuses_a_missing_key(root: Path, fakes: tuple[dict[str, str], Path]) -> None:
    env, log = fakes
    shutil.rmtree(Path(env["PASSWORD_STORE_DIR"]) / "actors/scout")
    result = setup(root, env, "--no-labels")
    assert result.returncode == 1
    assert "error: actors/scout/private-key isn't in the password store: BRAIN_PULL_APP_PRIVATE_KEY not set" in result.stdout
    assert "BRAIN_PULL_APP_PRIVATE_KEY" not in [c.split()[3] for c in log.read_text().splitlines() if "secret set" in c]


def test_setup_refuses_invalid_manifests(root: Path, fakes: tuple[dict[str, str], Path]) -> None:
    env, log = fakes
    edit(root, "courier", status="zombie")
    result = setup(root, env)
    assert result.returncode == 1 and "fix the manifests first" in result.stdout
    assert not log.exists()


# ------------------------------------------------------------------ the local-cron kit


@pytest.fixture
def checkout(tmp_path: Path) -> tuple[Path, Path, dict[str, str], Path]:
    """origin (bare, with main and live), a clone on live, and a fake gh that logs its calls."""
    origin, work, seed = tmp_path / "origin.git", tmp_path / "work", tmp_path / "seed"
    sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    sh("git", "clone", "-q", str(origin), str(seed), cwd=tmp_path)
    (seed / "note.md").write_text("one\n")
    sh("git", "add", "-A", cwd=seed)
    sh(*GIT, "commit", "-qm", "seed", cwd=seed)
    sh("git", "push", "-q", "origin", "HEAD:main", "HEAD:live", cwd=seed)
    sh("git", "clone", "-q", "-b", "live", str(origin), str(work), cwd=tmp_path)
    bin_dir, log = tmp_path / "bin", tmp_path / "gh.log"
    bin_dir.mkdir()
    stub(bin_dir, "gh", f'echo "gh $*" >> {log}\n')
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "XDG_STATE_HOME": str(tmp_path / "state")}
    return origin, work, env, log


KIT = ["--work-branch", "live", "--pr-branch", "auto/nightly", "--name", "test-cron", "--email", "cron@example.org",
       "--label", "automerge"]  # fmt: skip


def kit(work: Path, env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPTS / "actors-cron.sh"), "--dir", str(work), *KIT, *extra], env=env,
                          capture_output=True, text=True)  # fmt: skip


def test_cron_commits_pushes_and_proposes(checkout: tuple[Path, Path, dict[str, str], Path], tmp_path: Path) -> None:
    origin, work, env, log = checkout
    (work / "note.md").write_text("two\n")
    result = kit(work, env, "--deploy-key", str(tmp_path / "no-such-key"))
    assert result.returncode == 0, (tmp_path / "state/actors-cron/test-cron.log").read_text()
    author = sh("git", "log", "-1", "--format=%an <%ae>", "live", cwd=origin).strip()
    assert author == "test-cron <cron@example.org>"
    assert sh("git", "rev-parse", "auto/nightly", cwd=origin) == sh("git", "rev-parse", "live", cwd=origin)
    calls = log.read_text()
    assert "gh pr create --base main --head auto/nightly --title chore: test-cron's commits from live" in calls
    assert "--label automerge" in calls and "gh pr merge auto/nightly --auto --merge" in calls
    line = (tmp_path / "state/actors-cron/test-cron.log").read_text().splitlines()[-1]
    assert line[:4].isdigit() and line.endswith("test-cron: proposed 1 commit(s) from live in auto/nightly")


def test_cron_refuses_mid_operation(checkout: tuple[Path, Path, dict[str, str], Path], tmp_path: Path) -> None:
    origin, work, env, log = checkout
    (work / ".git/MERGE_HEAD").write_text("0" * 40 + "\n")
    (work / "note.md").write_text("two\n")
    assert kit(work, env).returncode == 1
    assert "a git operation is in progress: nothing done" in (tmp_path / "state/actors-cron/test-cron.log").read_text()
    assert "two" not in sh("git", "show", "live:note.md", cwd=origin) and not log.exists()


def test_cron_refuses_another_branch(checkout: tuple[Path, Path, dict[str, str], Path], tmp_path: Path) -> None:
    _, work, env, _ = checkout
    sh("git", "checkout", "-q", "-b", "other", cwd=work)
    assert kit(work, env).returncode == 1
    assert "is on other, not live: nothing done" in (tmp_path / "state/actors-cron/test-cron.log").read_text()


def test_cron_conflict_is_proposed_not_merged(checkout: tuple[Path, Path, dict[str, str], Path],
                                              tmp_path: Path) -> None:  # fmt: skip
    origin, work, env, log = checkout
    other = tmp_path / "other"
    sh("git", "clone", "-q", str(origin), str(other), cwd=tmp_path)
    (other / "note.md").write_text("main's\n")
    sh(*GIT, "commit", "-qam", "main", cwd=other)
    sh("git", "push", "-q", "origin", "main", cwd=other)
    (work / "note.md").write_text("live's\n")
    assert kit(work, env).returncode == 1
    text = (tmp_path / "state/actors-cron/test-cron.log").read_text()
    assert "live conflicts with origin/main in: note.md" in text
    assert (work / "note.md").read_text() == "live's\n"  # no markers
    assert "gh pr merge auto/nightly --auto --merge" in log.read_text()


def test_cron_prints_its_crontab_line() -> None:
    result = subprocess.run(["bash", str(SCRIPTS / "actors-cron.sh"), "--crontab", "--schedule", "45 2 * * *",
                             "--dir", "/srv/repo", *KIT], capture_output=True, text=True)  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("45 2 * * * /") and "actors-cron.sh --dir /srv/repo --work-branch live" in result.stdout
    assert "--crontab" not in result.stdout and "--schedule" not in result.stdout


# ------------------------------------------------------------------ avatars


def test_avatar_to_the_spec(tmp_path: Path) -> None:
    from PIL import Image

    avatar = module("actors-avatar")
    rng = random.Random(1)
    src = tmp_path / "raw.png"
    # Noise is the worst case: 256 colours of it don't fit in 1 MB, so it takes fewer. A dark left half and a
    # light right half keep it legible at 20 px.
    w, h = 1500, 1200
    noise = bytes(rng.randrange(128) + (128 if (i // 3) % w >= w // 2 else 0) for i in range(w * h * 3))
    Image.frombytes("RGB", (w, h), noise).save(src)
    out = tmp_path / "out.png"
    assert avatar.process(src, out, tmp_path / "preview") == []
    with Image.open(out) as image:
        assert image.size == (1024, 1024) and image.mode == "P"
    assert out.stat().st_size < 1024 * 1024
    assert sorted(p.name for p in (tmp_path / "preview").iterdir()) == ["out-20.png", "out-32.png", "out-64.png"]


def test_avatar_check_rejects(tmp_path: Path) -> None:
    from PIL import Image

    avatar = module("actors-avatar")
    flat = tmp_path / "flat.png"
    Image.new("RGB", (1024, 1024), (14, 42, 31)).save(flat)
    found = avatar.check(flat)
    assert any("not a palette image" in e for e in found) and any("nearly flat" in e for e in found)
    jpg = tmp_path / "x.jpg"
    Image.new("RGB", (64, 64), (1, 2, 3)).save(jpg)
    found = avatar.check(jpg)
    assert any("JPEG, not PNG" in e for e in found) and any("64x64, not 1024x1024" in e for e in found)


def test_fixture_json_round_trip() -> None:
    """The schema file is plain JSON a project's editor can load (yaml-language-server)."""
    assert json.loads((ROOT / "template/.github/actors/actor.schema.json").read_text())["title"] == "Actor manifest"
