# template-actors

A **feature overlay** for repositories that are, or declare, automation actors: GitHub Apps, workflow tokens,
GitHub's own bots and local scheduled jobs. It goes on top of
[template-base](https://github.com/andronics/template-base) and any stack overlay
([template-python](https://github.com/andronics/template-python),
[template-typescript](https://github.com/andronics/template-typescript) or
[template-obsidian](https://github.com/andronics/template-obsidian)):

```sh
uvx copier copy --trust gh:andronics/template-base my-project
uvx copier copy --trust gh:andronics/template-obsidian my-project   # or any stack
uvx copier copy --trust gh:andronics/template-actors my-project
```

It asks one question, `theme` (`fantasy` or `sci-fi`), and reads everything else from base's answers. It needs
template-base `v2.0.0` or later.

## What it adds

| Path | What |
|---|---|
| `.github/actors/actor.schema.json` | The manifest's JSON Schema. Each actor is `.github/actors/<slug>.yml`, the project's own, and the source of truth |
| `scripts/checks.d/40-actors.sh` | Validates the manifests in the Definition of done: the schema, `app_*` fields only on `github-app` actors, one App per role, no secret values, the docs current; locally, that each key's pass entry exists (never decrypted) |
| `scripts/actors.py` | `check`, `docs` (the PR contract and secret names in `.github/actors/README.md`), `new`, `labels`, `note` (a vault actor note from a manifest, or `--check` one) |
| `scripts/actors-setup.py` | Sets `<ROLE>_APP_CLIENT_ID` and pipes `pass show <key>` into `<ROLE>_APP_PRIVATE_KEY` for each role (`UPDATE`, `RELEASE`, `BRAIN_PULL`), creates the labels, and warns about stale org-level overrides. `--dry-run` first |
| `actors-automerge.yml` | Queues auto-merge on an App actor's PR when its manifest says `auto-on-ci` or `auto-after-codeowner` |
| `actors-labels.yml` | Creates the declared labels, idempotently, when the manifests change |
| `scripts/actors-cron.sh` | The local-cron actor kit: commit as a bot, test-merge with `git merge-tree`, push with a deploy key, propose through a disposable PR branch with auto-merge, log; `--crontab` prints its line |
| `scripts/actors-avatar.py` | The avatar spec and post-processing: PNG, 1024², 256 colours, under 1 MB, legible at 20 to 64 px |

A manifest, for an App that cuts releases:

```yaml
# yaml-language-server: $schema=actor.schema.json
name: Tagwarden
description: Cuts releases and is the only actor allowed to create v* tags
theme: fantasy
status: active                     # planned, created, installed, active, retired, superseded, deleted
identity: github-app               # github-app, github-actions, github-native, local
app: my-tagwarden                  # one App may serve several actors
app_id: 1234567                    # public
client_id: Iv23lixxxxxxxxxxxxxx    # public
key: actors/tagwarden/private-key  # a pass path, never the key
roles: [release]                   # update, release, brain-pull: names its secrets
fallback: github-token             # or required
permissions: {contents: write, pull_requests: write, issues: write}
acts_on: [my-org/my-project]
trigger: {workflows: [release-please.yml], events: [push]}
merge_policy: never                # auto-on-ci, auto-after-codeowner
branch_prefix: release-please--
labels: [{name: release, color: 5319e7}]
avatar: {file: tagwarden.png, prompt: A lantern-bearing warden holding a great iron key}
```

## Setup notes

- Creating and installing the Apps, their rulesets and "Allow auto-merge" stay manual, for the owner.
- `actors-automerge.yml` uses `GITHUB_TOKEN` until template-base's `app-token` action lands, so a merge it
  queued starts no workflow on `main`.

## Where this came from

Designed in the brain's idea note **🤹 Template Actors** (2026-11-02, all questions answered 2026-11-03), from
the brain's actor notes and the five template repositories.
[`docs/decisions/0001`](docs/decisions/0001-actors-overlay.md) records what is binding.

## License

[Unlicense](LICENSE).
