#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6", "jsonschema>=4.18"]
# ///
"""Template-owned: template-actors (decision 0001). Set each role's App secret and variable from the
manifests and pass, and create the actors' labels:

    uv run scripts/actors-setup.py --dry-run          what it would do, and what looks stale
    uv run scripts/actors-setup.py                    this checkout's repository
    uv run scripts/actors-setup.py -R OWNER/NAME ...  other repositories (repeat -R)
    uv run scripts/actors-setup.py --legacy           also the legacy names, for one release cycle

For every live (created, installed, active) github-app actor, each role it fills becomes
`gh variable set <ROLE>_APP_CLIENT_ID` (its client_id, public) and `pass show <key> | gh secret
set <ROLE>_APP_PRIVATE_KEY`. The key goes from pass to gh through a pipe: this script never
reads, prints or writes it. Roles: update -> UPDATE_APP_*, release -> RELEASE_APP_*,
brain-pull -> BRAIN_PULL_APP_* (question 2); --legacy adds PIN_BUMP_APP_* and SCOUT_APP_*.

It warns about what it can't fix: an org-level variable or secret with a role's name, which
every repository without its own inherits (a stale one points at a deleted App), and legacy or
unknown *_APP_* names left on the repository.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from actors import LEGACY, LIVE, ROLES, check_all, load, pass_store  # noqa: E402


def gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def plan(actors: dict[str, dict], legacy: bool) -> list[tuple[str, str, str, str]]:
    """(role, name prefix, client_id, pass key), one per role and name."""
    rows = []
    seen = set()
    for _slug, a in actors.items():
        if a["identity"] != "github-app" or a["status"] not in LIVE:
            continue
        for role in a.get("roles", []):
            prefixes = [ROLES[role]] + ([LEGACY[role]] if legacy and role in LEGACY else [])
            for prefix in prefixes:
                if prefix not in seen:
                    seen.add(prefix)
                    rows.append((role, prefix, a["client_id"], a["key"]))
    return rows


def set_secret(key: str, name: str, repo: str) -> bool:
    """pass show KEY | gh secret set NAME: the key stays in the pipe."""
    if not (pass_store() / f"{key}.gpg").is_file():
        print(f"error: {key} isn't in the password store: {name} not set")
        return False
    with subprocess.Popen(["pass", "show", key], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as source:
        setter = subprocess.run(["gh", "secret", "set", name, "-R", repo], stdin=source.stdout,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)  # fmt: skip
        assert source.stdout is not None
        source.stdout.close()
        source.wait()
    if source.returncode != 0 or setter.returncode != 0:
        print(f"error: {name} on {repo}: pass exited {source.returncode}, gh exited {setter.returncode}"
              f" {setter.stderr.strip()}")  # fmt: skip
        return False
    return True


def names(kind: str, scope: list[str]) -> dict[str, str]:
    """Variable or secret name -> value ('' for a secret), at a repo (-R) or an org (--org)."""
    fields = "name,value" if kind == "variable" else "name"
    result = gh(kind, "list", *scope, "--json", fields)
    if result.returncode != 0:
        return {}
    return {row["name"]: row.get("value", "") for row in json.loads(result.stdout or "[]")}


def stale(repo: str, rows: list[tuple[str, str, str, str]], legacy: bool) -> list[str]:
    warnings = []
    wanted = {prefix: client for _, prefix, client, _ in rows}
    legacy_prefixes = set(LEGACY.values())
    owner = repo.split("/")[0]
    org = gh("api", f"users/{owner}", "--jq", ".type")
    if org.returncode == 0 and org.stdout.strip() == "Organization":
        variables, secrets = names("variable", ["--org", owner]), names("secret", ["--org", owner])
        for name in sorted(set(variables) | set(secrets)):
            if not name.endswith(("_APP_CLIENT_ID", "_APP_PRIVATE_KEY")):
                continue
            prefix = name.rsplit("_APP_", 1)[0]
            if prefix in wanted and name in variables and variables[name] != wanted[prefix]:
                warnings.append(f"warning: org {owner} has {name}={variables[name]}, but the manifests say"
                                f" {wanted[prefix]}: a stale org-level override (remove it, or set it right)")  # fmt: skip
            elif prefix in legacy_prefixes:
                warnings.append(f"warning: org {owner} has the legacy {name}: drop it after the transition")
            elif prefix not in wanted:
                warnings.append(f"warning: org {owner} has {name}, which no live actor here fills: stale?")
    variables, secrets = names("variable", ["-R", repo]), names("secret", ["-R", repo])
    for name in sorted(set(variables) | set(secrets)):
        if not name.endswith(("_APP_CLIENT_ID", "_APP_PRIVATE_KEY")):
            continue
        prefix = name.rsplit("_APP_", 1)[0]
        if prefix in wanted:
            continue
        if prefix in legacy_prefixes:
            warnings.append(f"warning: {repo} has the legacy {name}: kept for one release cycle, then"
                            " remove it (or run with --legacy to keep it current)")  # fmt: skip
        else:
            warnings.append(f"warning: {repo} has {name}, which no live actor here fills: stale?")
    return warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print what it would do; change nothing")
    parser.add_argument("-R", "--repo", action="append", help="OWNER/NAME (default: this checkout's)")
    parser.add_argument("--legacy", action="store_true", help="also set PIN_BUMP_APP_* and SCOUT_APP_*")
    parser.add_argument("--no-labels", action="store_true", help="skip creating the labels")
    args = parser.parse_args(argv)

    actors, errors = load()
    errors += check_all(actors) if not errors else []
    if errors:
        print("\n".join(errors) + "\nerror: fix the manifests first (uv run scripts/actors.py check)")
        return 1
    repos = args.repo
    if not repos:
        here = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
        if here.returncode != 0:
            print("error: can't tell this checkout's repository: pass -R OWNER/NAME")
            return 1
        repos = [here.stdout.strip()]
    rows = plan(actors, args.legacy)
    if not rows:
        print("no live github-app actor fills a role: nothing to set")

    ok = True
    for repo in repos:
        for role, prefix, client, key in rows:
            variable, secret = f"{prefix}_APP_CLIENT_ID", f"{prefix}_APP_PRIVATE_KEY"
            if args.dry_run:
                print(f"would set {repo} variable {variable}={client} ({role})")
                print(f"would run pass show {key} | gh secret set {secret} -R {repo}")
                continue
            result = gh("variable", "set", variable, "--body", client, "-R", repo)
            if result.returncode != 0:
                print(f"error: {variable} on {repo}: {result.stderr.strip()}")
                ok = False
                continue
            if set_secret(key, secret, repo):
                print(f"{repo}: {variable} and {secret} set ({role})")
            else:
                ok = False
        for warning in stale(repo, rows, args.legacy):
            print(warning)
        if not args.no_labels:
            cmd = [sys.executable, str(Path(__file__).with_name("actors.py")), "labels", "-R", repo]
            ok = subprocess.run(cmd + (["--dry-run"] if args.dry_run else [])).returncode == 0 and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
