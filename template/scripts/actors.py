#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6", "jsonschema>=4.18"]
# ///
"""Template-owned: template-actors (decision 0001). The actor manifests, .github/actors/<slug>.yml.

    uv run scripts/actors.py check [--local]      validate every manifest (scripts/checks.d/40-actors.sh)
    uv run scripts/actors.py docs [--check]       write (or check) .github/actors/README.md
    uv run scripts/actors.py new SLUG [--identity github-app]   a planned actor's manifest
    uv run scripts/actors.py labels [--dry-run]   create every actor's labels, idempotently
    uv run scripts/actors.py note SLUG            a vault actor note's skeleton, on stdout
    uv run scripts/actors.py note SLUG --check NOTE   does a vault note's front matter match?
    uv run scripts/actors.py automerge --author LOGIN --head-ref BRANCH   (actors-automerge.yml)

The manifest is the source of truth (question 3); a manifest never holds a key or token, only
the pass path of a key. `check --local` also confirms each key's pass entry exists, without
decrypting it. Secrets and variables are set by scripts/actors-setup.py.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
ACTORS = ROOT / ".github/actors"
SCHEMA = ACTORS / "actor.schema.json"
README = ACTORS / "README.md"
ANSWERS = ROOT / ".copier-answers.actors.yml"

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
APP_ONLY = ("app", "app_id", "client_id", "key", "roles", "fallback")
# A role names its secrets: <PREFIX>_APP_CLIENT_ID (a variable) and <PREFIX>_APP_PRIVATE_KEY.
ROLES = {"update": "UPDATE", "release": "RELEASE", "brain-pull": "BRAIN_PULL"}
# The names they replace, read by a shim for one release cycle (question 2).
LEGACY = {"update": "PIN_BUMP", "brain-pull": "SCOUT"}
# Statuses whose App exists, so its role, key and labels are live.
LIVE = ("created", "installed", "active")
# What a value must never look like: a PEM block or a GitHub token.
SECRET = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")

# The avatars' style blocks, by theme (the brain's Actor Conventions). The full prompt is the
# actor's line plus its theme's block.
STYLE = {
    "fantasy": (
        "Heraldic emblem in a circular wax-seal badge, deep forest green background (#0E2A1F), gold and"
        " parchment accents, illuminated-manuscript linework, flat vector with subtle engraved texture, single"
        " central figure or object, no text, no letters, high contrast, readable at 20x20 and 64x64 pixels,"
        " square 1:1, 1024x1024."
    ),
    "sci-fi": (
        "Retro-futurist mission-patch emblem in a circular badge, dark navy background (#0B1020), glowing cyan"
        " and amber accents, clean geometric flat vector, single central icon, no text, no letters, high"
        " contrast, readable at 20x20 and 64x64 pixels, square 1:1, 1024x1024."
    ),
}

POLICY = {
    "never": "a person reviews and merges",
    "auto-on-ci": "auto-merge is queued; it merges once the required checks (`ci`) pass",
    "auto-after-codeowner": "auto-merge is queued; it merges once the required checks pass and a code owner approves",
}


# ------------------------------------------------------------------ loading


def load(root: Path = ROOT) -> tuple[dict[str, dict], list[str]]:
    """slug -> manifest, and the errors met reading them."""
    actors: dict[str, dict] = {}
    errors: list[str] = []
    folder = root / ".github/actors"
    for path in sorted(folder.glob("*.yml")) + sorted(folder.glob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        if not SLUG.match(path.stem):
            errors.append(f"error: {rel}: the file name is the actor's slug: lower-case letters, digits and -")
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"error: {rel}: not valid YAML: {e}")
            continue
        if not isinstance(data, dict):
            errors.append(f"error: {rel}: a manifest is a mapping")
            continue
        if path.stem in actors:
            errors.append(f"error: {rel}: {path.stem} is declared twice")
            continue
        actors[path.stem] = data
    return actors, errors


def schema(root: Path = ROOT) -> dict:
    return json.loads((root / ".github/actors/actor.schema.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ checking


def strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)] + [k for k in value if isinstance(k, str)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


def check_one(slug: str, actor: dict, validator: Draft202012Validator) -> list[str]:
    where = f".github/actors/{slug}.yml"
    errors = []
    if any(SECRET.search(s) for s in strings(actor)):
        # Never echo the value.
        return [f"error: {where}: holds what looks like a private key or token: a manifest names only its pass path"]
    identity = actor.get("identity")
    if identity != "github-app":
        for field in APP_ONLY:
            if field in actor:
                errors.append(f"error: {where}: {field} is for github-app actors only (this one is {identity})")
    for e in sorted(validator.iter_errors(actor), key=lambda e: list(e.absolute_path)):
        if identity != "github-app" and e.validator == "false":
            continue  # reported above, more plainly
        path = ".".join(str(p) for p in e.absolute_path) or "(top level)"
        errors.append(f"error: {where}: {path}: {e.message}")
    superseded = actor.get("superseded_by")
    if isinstance(superseded, list):
        if actor.get("status") == "superseded":
            errors.append(f"error: {where}: superseded_by lists parts (a partial supersession), so its status"
                          " can't be superseded: name one successor instead")  # fmt: skip
        actors_named = [e.get("actor") for e in superseded if isinstance(e, dict)]
        if slug in actors_named:
            errors.append(f"error: {where}: superseded_by names the actor itself")
    elif isinstance(superseded, str):
        if superseded == slug:
            errors.append(f"error: {where}: superseded_by names the actor itself")
        if actor.get("status") != "superseded":
            errors.append(f"error: {where}: superseded_by names one successor, so its status must be superseded"
                          " (a list of {actor, scope} is a partial supersession)")  # fmt: skip
    perms = actor.get("permissions") or {}
    if "release" in (actor.get("roles") or []) and isinstance(perms, dict) and "workflows" in perms:
        errors.append(f"error: {where}: a release actor never gets workflows (template-base decision 0002):"
                      " it may move v* tags, so it stays narrow")  # fmt: skip
    return errors


def check_all(actors: dict[str, dict], root: Path = ROOT) -> list[str]:
    validator = Draft202012Validator(schema(root))
    errors: list[str] = []
    for slug, actor in actors.items():
        errors += check_one(slug, actor, validator)
    if errors:
        return errors
    # One App per App slug: the actors sharing it agree on its ids and key.
    apps: dict[str, tuple[str, tuple]] = {}
    for slug, a in actors.items():
        if a["identity"] != "github-app":
            continue
        ids = (a["app_id"], a["client_id"], a["key"])
        if a["app"] in apps and apps[a["app"]][1] != ids:
            errors.append(f"error: {slug} and {apps[a['app']][0]} share the App {a['app']} but differ in"
                          " app_id, client_id or key")  # fmt: skip
        apps.setdefault(a["app"], (slug, ids))
    # One App per role: a role is one pair of secrets.
    roles: dict[str, tuple[str, str]] = {}
    for slug, a in actors.items():
        if a["identity"] != "github-app" or a["status"] not in LIVE:
            continue
        for role in a.get("roles", []):
            if role in roles and roles[role][1] != a["app"]:
                errors.append(f"error: role {role} is filled by two Apps, {roles[role][1]} ({roles[role][0]}) and"
                              f" {a['app']} ({slug}): its secrets {ROLES[role]}_APP_* hold one")  # fmt: skip
            roles.setdefault(role, (slug, a["app"]))
    # A label means one thing: one colour and description wherever it's declared.
    labels: dict[str, tuple[str, dict]] = {}
    for slug, a in actors.items():
        for label in a.get("labels", []):
            name = label["name"]
            if name in labels and labels[name][1] != label:
                errors.append(f"error: label {name!r} is declared differently by {labels[name][0]} and {slug}")
            labels.setdefault(name, (slug, label))
    # A live actor's workflows exist.
    for slug, a in actors.items():
        if a["status"] != "active":
            continue
        for wf in a["trigger"].get("workflows", []):
            if not (root / ".github/workflows" / wf).is_file():
                errors.append(f"error: .github/actors/{slug}.yml: trigger.workflows names {wf}, which isn't in"
                              " .github/workflows/")  # fmt: skip
    return errors


def pass_store() -> Path:
    return Path(os.environ.get("PASSWORD_STORE_DIR") or Path.home() / ".password-store")


def check_keys(actors: dict[str, dict]) -> tuple[list[str], bool]:
    """Each live App's key exists in pass (its .gpg file), never decrypted. (errors, checked?)"""
    store = pass_store()
    if not store.is_dir():
        return [], False
    errors = []
    for slug, a in actors.items():
        if a.get("identity") == "github-app" and a.get("status") in LIVE and a.get("key"):
            if not (store / f"{a['key']}.gpg").is_file():
                errors.append(f"error: .github/actors/{slug}.yml: key {a['key']} isn't in the password store ({store})")
    return errors, True


# ------------------------------------------------------------------ docs


def cell(values: list[str] | str | None) -> str:
    if not values:
        return "-"
    if isinstance(values, str):
        return values
    return ", ".join(values)


def render_docs(actors: dict[str, dict]) -> str:
    lines = [
        "# Actors",
        "",
        "<!-- Generated by `uv run scripts/actors.py docs` from .github/actors/*.yml (template-actors):"
        " edit the manifests, never this file. -->",
        "",
        "The identities this repository's automation acts as. Each has a manifest here,"
        " `.github/actors/<slug>.yml`, checked against `actor.schema.json` by `scripts/checks.sh`."
        " A manifest names a key's pass path, never the key.",
        "",
    ]
    if not actors:
        return "\n".join(lines + ["No actors are declared yet: `uv run scripts/actors.py new <slug>`.", ""])
    lines += [
        "| Actor | Identity | App | Roles | Status | Description |",
        "|---|---|---|---|---|---|",
    ]
    for slug, a in actors.items():
        name = f"{a.get('emoji', '')} {a['name']}".strip()
        lines.append(f"| {name} (`{slug}`) | {a['identity']} | {cell(a.get('app'))} | {cell(a.get('roles'))}"
                     f" | {a['status']} | {a['description']} |")  # fmt: skip
    lines += [
        "",
        "## The PR contract",
        "",
        "Every actor's PR has a Conventional Commit title (template-base's `pr-title.yml`), comes from a"
        " branch starting with its branch prefix, carries its labels, and is merged by its merge policy:",
        "",
    ]
    lines += [f"- `{p}`: {text}." for p, text in POLICY.items()]
    lines += [
        "",
        "`actors-automerge.yml` queues auto-merge for a `github-app` actor whose policy"
        " is `auto-on-ci` or `auto-after-codeowner`, on a PR from its own branch in this repository. A local"
        " actor queues its own (`scripts/actors-cron.sh`), Dependabot's is template-base's, and a"
        " `github-actions` actor's PRs start no workflow, so none is queued for them. The policy is"
        " declared, not enforced: the `main` ruleset decides what a merge needs.",
        "",
        "| Actor | Branch prefix | Labels | Merge policy | Merge method | Fallback without its App |",
        "|---|---|---|---|---|---|",
    ]
    for slug, a in actors.items():
        labels = [f"`{label['name']}`" for label in a.get("labels", [])]
        prefix = f"`{a['branch_prefix']}`" if a.get("branch_prefix") else "-"
        lines.append(f"| {a['name']} | {prefix} | {cell(labels)} | `{a['merge_policy']}`"
                     f" | {a.get('merge_method', 'squash')} | {cell(a.get('fallback'))} |")  # fmt: skip
    lines += [
        "",
        "A `required` fallback fails or skips without its App's variable; `github-token` falls back to"
        " `github.token`, whose PRs start no CI and which can't push a workflow change (it opens an issue"
        " instead).",
        "",
        "## Secrets and variables",
        "",
        "Named by role, not by actor (decision 0001): a workflow needs a role, and which actor fills it"
        " differs by owner. `uv run scripts/actors-setup.py` sets them from the manifests and pass"
        " (`--dry-run` first).",
        "",
        "| Role | Variable | Secret | App | Actors | Key (pass) |",
        "|---|---|---|---|---|---|",
    ]
    for role, prefix in ROLES.items():
        fills = [(s, a) for s, a in actors.items() if role in a.get("roles", []) and a["status"] in LIVE]
        if not fills:
            continue
        app = fills[0][1]
        lines.append(f"| {role} | `{prefix}_APP_CLIENT_ID` | `{prefix}_APP_PRIVATE_KEY` | {app['app']}"
                     f" | {cell([a['name'] for _, a in fills])} | `{app['key']}` |")  # fmt: skip
    lines += [
        "",
        "Least privilege: a release actor never has `workflows` (template-base decision 0002); only the"
        " update actor does. Each workflow mints its token with the permissions it needs, never all of the"
        " App's.",
    ]
    gone = [(s, a) for s, a in actors.items() if a.get("superseded_by")]
    if gone:
        lines += ["", "## Superseded", "", "| Actor | Status | Superseded by |", "|---|---|---|"]
        for _, a in gone:
            by = a["superseded_by"]
            text = f"`{by}`" if isinstance(by, str) else "; ".join(f"`{e['actor']}`: {e['scope']}" for e in by)
            lines.append(f"| {a['name']} | {a['status']} | {text} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ vault notes


def note_frontmatter(slug: str, a: dict, today: str) -> dict:
    """A vault actor note's properties (the brain's Actor Conventions), from the manifest."""
    fm: dict[str, object] = {"kind": "actor", "status": a["status"], "identity": a["identity"]}
    if a["identity"] != "github-app":
        fm["runs_as"] = a.get("runs_as", "")
    fm["role"] = a["description"]
    if a["identity"] == "github-app":
        fm.update({"app": a["app"], "app_id": a["app_id"], "client_id": a["client_id"], "key": a["key"]})
    fm["permissions"] = [f"{k}: {v}" for k, v in a["permissions"].items()]
    fm["acts_on"] = list(a["acts_on"])
    if a.get("superseded_by"):
        fm["superseded_by"] = a["superseded_by"]
    fm["avatar"] = f"[[{(a.get('avatar') or {}).get('file', slug + '.png')}]]"
    fm.update({"created": today, "updated": today, "tags": ["actors", a["identity"]]})
    return fm


# The properties a note must agree with its manifest on (the rest are the note's own).
COMPARED = ("status", "identity", "runs_as", "role", "app", "app_id", "client_id", "key", "permissions",
            "acts_on", "superseded_by", "avatar")  # fmt: skip


def render_note(slug: str, a: dict, today: str) -> str:
    fm = yaml.safe_dump(note_frontmatter(slug, a, today), sort_keys=False, allow_unicode=True, width=1000)
    prompt = (a.get("avatar") or {}).get("prompt") or "One line: the actor's central figure or object, and what it stands for."
    roles = ", ".join(a.get("roles", [])) or "none"
    policy = f"`{a['merge_policy']}`" + (f", from branches `{a['branch_prefix']}*`" if a.get("branch_prefix") else "")
    return f"""---
{fm}---

*One italic line: the legend in a sentence.*

<!-- Generated by template-actors' scripts/actors.py note {slug} from .github/actors/{slug}.yml, the source
of truth: change the manifest, then regenerate or check (actors.py note {slug} --check <this note>). -->

## 📖 Legend
Who it is in the story, and how it went from weak to strong.

## 🎯 Role
{a['description']}

Trigger: {json.dumps(a['trigger'])}. Roles: {roles}. Merge policy: {policy}.

## 🔐 Identity and keys
{"Key: pass `" + a["key"] + "` (never the key). Without its App: `" + a["fallback"] + "`." if a["identity"] == "github-app" else "Runs as: " + str(a.get("runs_as", "")) + "."}

## 🌍 Where it acts
{", ".join(a["acts_on"]) or "Nowhere yet."}

## 🎨 Avatar
Prompt: "{prompt}"

Style ({a['theme']}): {STYLE[a['theme']]}

## 🔗 Related
* [[🎭 Actors]]

## 📜 Change log
| Date | Change |
|---|---|
| {today} | Generated from the manifest. |
"""


def check_note(slug: str, a: dict, note: Path) -> list[str]:
    text = note.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return [f"error: {note}: no front matter"]
    have = yaml.safe_load(match.group(1)) or {}
    want = note_frontmatter(slug, a, "")
    errors = []
    for key in COMPARED:
        if key not in want and key not in have:
            continue
        mine, theirs = want.get(key), have.get(key)
        if key == "permissions" and isinstance(theirs, list):
            theirs = [re.sub(r"\s+", " ", str(p)).strip() for p in theirs]
            mine, theirs = sorted(mine or []), sorted(theirs)
        if key == "acts_on" and isinstance(theirs, list):
            mine, theirs = sorted(mine or []), sorted(theirs)
        if mine != theirs:
            errors.append(f"error: {note.name}: {key} is {theirs!r}; the manifest says {mine!r}")
    return errors


# ------------------------------------------------------------------ auto-merge


def automerge(actors: dict[str, dict], author: str, head_ref: str) -> tuple[str, dict | None]:
    """The actor whose PR this is, if its policy merges automatically: (message, actor or None)."""
    candidates = []
    for slug, a in actors.items():
        if a["status"] not in ("installed", "active"):
            continue
        # A github-actions actor's PRs (GITHUB_TOKEN's) start no workflow, this one included.
        if a["identity"] == "github-app" and author == f"{a['app']}[bot]":
            candidates.append((slug, a))
    if not candidates:
        return f"{author} is no declared github-app actor here", None
    matching = [(s, a) for s, a in candidates if a.get("branch_prefix") and head_ref.startswith(a["branch_prefix"])]
    if not matching:
        names = ", ".join(s for s, _ in candidates)
        return f"{head_ref} doesn't start with the branch prefix of {names} (the PR contract): not auto-merged", None
    slug, a = max(matching, key=lambda sa: len(sa[1]["branch_prefix"]))
    if a["merge_policy"] == "never":
        return f"{slug}'s merge policy is never: a person merges", {"slug": slug, **a}
    return f"{slug}: {a['merge_policy']}, queueing auto-merge", {"slug": slug, **a}


# ------------------------------------------------------------------ commands


def answers_theme() -> str:
    try:
        return str((yaml.safe_load(ANSWERS.read_text(encoding="utf-8")) or {}).get("theme") or "fantasy")
    except OSError:
        return "fantasy"


def cmd_check(args: argparse.Namespace) -> int:
    actors, errors = load()
    errors += check_all(actors) if not errors else []
    keys_checked = False
    if args.local and not errors:
        key_errors, keys_checked = check_keys(actors)
        errors += key_errors
    if not errors and actors:
        current = README.read_text(encoding="utf-8") if README.is_file() else ""
        if current != render_docs(actors):
            errors.append("error: .github/actors/README.md is out of date: uv run scripts/actors.py docs")
    if errors:
        print("\n".join(errors))
        return 1
    note = "" if not args.local else (", pass paths checked" if keys_checked else ", no password store")
    print(f"40-actors OK ({len(actors)} actors{note})")
    return 0


def cmd_docs(args: argparse.Namespace) -> int:
    actors, errors = load()
    errors += check_all(actors) if not errors else []
    if errors:
        print("\n".join(errors))
        return 1
    text = render_docs(actors)
    if args.check:
        ok = README.is_file() and README.read_text(encoding="utf-8") == text
        print(".github/actors/README.md is up to date" if ok else "error: .github/actors/README.md is out of date")
        return 0 if ok else 1
    README.write_text(text, encoding="utf-8")
    print(f"wrote {README.relative_to(ROOT)}")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    if not SLUG.match(args.slug):
        print("error: a slug is lower-case letters, digits and -")
        return 1
    path = ACTORS / f"{args.slug}.yml"
    if path.exists():
        print(f"error: {path.relative_to(ROOT)} exists")
        return 1
    name = args.slug.replace("-", " ").title()
    lines = [
        "# yaml-language-server: $schema=actor.schema.json",
        f"name: {name}",
        f"description: What {name} does, in one line",
        f"theme: {answers_theme()}",
        "status: planned",
        f"identity: {args.identity}",
    ]
    if args.identity == "github-app":
        lines += [
            f"app: {args.slug}",
            "app_id: 1  # from the App's settings page (public)",
            "client_id: Iv23xxxxxxxxxxxxxxxx  # from the App's settings page (public)",
            f"key: actors/{args.slug}/private-key  # a pass path, never the key",
            "roles: []  # update, release, brain-pull",
            "fallback: required  # or github-token",
        ]
    else:
        lines.append("runs_as: whose rights it uses")
    lines += [
        "permissions:",
        "  contents: read",
        "acts_on: []",
        "trigger:",
        "  workflows: []",
        "merge_policy: never  # auto-on-ci, auto-after-codeowner (then set branch_prefix)",
        "labels: []",
        "avatar:",
        f"  file: {args.slug}.png",
        "  prompt: The actor's central figure or object, and what it stands for",
    ]
    ACTORS.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}: fill it in, then uv run scripts/actors.py docs")
    return 0


def cmd_labels(args: argparse.Namespace) -> int:
    actors, errors = load()
    errors += check_all(actors) if not errors else []
    if errors:
        print("\n".join(errors))
        return 1
    seen: dict[str, dict] = {}
    for a in actors.values():
        if a["status"] in ("retired", "superseded", "deleted"):
            continue
        for label in a.get("labels", []):
            seen.setdefault(label["name"], label)
    for name, label in seen.items():
        cmd = ["gh", "label", "create", name, "--force"]
        if label.get("color"):
            cmd += ["--color", label["color"]]
        if label.get("description"):
            cmd += ["--description", label["description"]]
        if args.repo:
            cmd += ["-R", args.repo]
        if args.dry_run:
            print("would run: " + " ".join(cmd))
            continue
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"error: label {name}: {result.stderr.strip()}")
            return 1
        print(f"label {name}: ok")
    if not seen:
        print("no labels declared")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    actors, errors = load()
    errors += check_all(actors) if not errors else []
    if errors:
        print("\n".join(errors))
        return 1
    if args.slug not in actors:
        print(f"error: no manifest .github/actors/{args.slug}.yml")
        return 1
    actor = actors[args.slug]
    if args.check:
        note_errors = check_note(args.slug, actor, Path(args.check))
        print("\n".join(note_errors) if note_errors else f"{Path(args.check).name} matches {args.slug}.yml")
        return 1 if note_errors else 0
    sys.stdout.write(render_note(args.slug, actor, datetime.date.today().isoformat()))
    return 0


def cmd_automerge(args: argparse.Namespace) -> int:
    actors, errors = load()
    errors += check_all(actors) if not errors else []
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    message, actor = automerge(actors, args.author, args.head_ref)
    print(f"::notice::{message}", file=sys.stderr)
    if actor is not None:
        print(f"actor={actor['slug']}")
        print("labels=" + ",".join(label["name"] for label in actor.get("labels", [])))
        if actor["merge_policy"] != "never":
            print(f"merge={actor.get('merge_method', 'squash')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="validate every manifest")
    p.add_argument("--local", action="store_true", help="also confirm each live key's pass entry exists")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("docs", help="write .github/actors/README.md")
    p.add_argument("--check", action="store_true")
    p.set_defaults(func=cmd_docs)
    p = sub.add_parser("new", help="a planned actor's manifest")
    p.add_argument("slug")
    p.add_argument("--identity", default="github-app", choices=["github-app", "github-actions", "github-native", "local"])
    p.set_defaults(func=cmd_new)
    p = sub.add_parser("labels", help="create every actor's labels (gh label create --force)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-R", "--repo", help="OWNER/NAME (default: this checkout's)")
    p.set_defaults(func=cmd_labels)
    p = sub.add_parser("note", help="a vault actor note's skeleton, or --check one")
    p.add_argument("slug")
    p.add_argument("--check", metavar="NOTE")
    p.set_defaults(func=cmd_note)
    p = sub.add_parser("automerge", help="for actors-automerge.yml: GITHUB_OUTPUT lines")
    p.add_argument("--author", required=True)
    p.add_argument("--head-ref", required=True)
    p.set_defaults(func=cmd_automerge)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
