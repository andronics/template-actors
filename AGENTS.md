# AGENTS.md: working on template-actors

This repository is a [Copier](https://copier.readthedocs.io/) **feature overlay**: it is applied on top of
template-base and any stack (template-python, template-typescript, template-obsidian), and adds the actor
manifests' schema, checks and scripts. These instructions are for changing **the overlay**. Why:
`docs/decisions/0001-actors-overlay.md` and template-base's decision 0001.

## Layout

| Path | What |
|---|---|
| `copier.yml` | `theme`; base's answers through `_external_data` (recorded for updates); the compatibility check |
| `template/` | `.github/actors/actor.schema.json`, `actors-*` workflows, `scripts/actors*`, `scripts/checks.d/40-actors.sh` |
| `tests/` | `test_actors.py` renders base, each stack (siblings, or `$TEMPLATE_PYTHON`, `$TEMPLATE_TYPESCRIPT`, `$TEMPLATE_OBSIDIAN`) and this overlay; `test_manifests.py` tests the schema and scripts on the fixtures' real actors |
| `docs/decisions/` | Design records; `README.md` is the index |

## The rules of a feature overlay

- **Never fill a slot or add a required job.** Only `actors-*` workflows, `actors*` scripts and `NN-actors.sh` drop-ins, each marked `Template-owned: template-actors` in its first 12 lines.
- **Never ask another template's question.** Read base's answers; record them for `copier update`.
- **Never a key or token,** in a manifest, a fixture, a test or a log: only pass paths. Never mint a token here: use template-base's `app-token` action.
- **No hard-coded owner or repository** under `template/` (a test checks).

## Rules

- Keep every render green: `uv run pytest -n 4`. Pin actions to SHAs. Never credit an AI. Never create tags or releases.
- Pins are bumped by Renovate (template-base's decision 0002, `pin-bumps.yml`): don't hand-bump one unless it's urgent.
- Asking the user something? Give your recommendation and why. Reply tersely.

## Definition of done

```sh
uv run pytest -n 4 -m ''
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12
```
