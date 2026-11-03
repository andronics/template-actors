# 0001: The actors feature overlay

Status: Proposed
Date: 2026-11-03
Authors: Stephen Cox

## Context

An actor is an identity an automation acts as: a GitHub App, a workflow's `GITHUB_TOKEN`, a bot GitHub runs
itself (Dependabot), or a local scheduled job. Until now an actor existed only as a note in the brain's vault
(`actors/`, with the conventions in **📐 Actor Conventions**), and each repository copied its workflow steps by
hand. That left nine near-identical token mints, fallbacks that differ by actor, secrets named by job
(`PIN_BUMP_APP_*` used by an actor that bumps no pins), merge policies nobody declared, no `superseded` or
`deleted` status, ad hoc labels and a drifting avatar spec.

The idea note **🤹 Template Actors** (in the brain's `vault/ideas/`) designed the fix, and Stephen answered its 11
open questions on 2026-11-03, accepting the coordinators' recommendations. It has two parts: a reusable
mint-token composite action in template-base (its own record, built in parallel), and this feature overlay,
following template-base decision 0001.

## Decision

### Shape (questions 1 and 6)

- **A feature overlay**, like template-container: `.copier-answers.actors.yml`, `actors-*` workflows, the
  `scripts/checks.d/40-actors.sh` drop-in, `actors*` scripts, each marked `Template-owned: template-actors`.
  It fills no slot and adds no required check.
- **Any stack.** It reads only base's answers (recorded as `base_answers`, template-base 0001) and names no
  stack: base's contract check already requires exactly one. It needs template-base v2.0.0 or later, and
  fails the copy with the reason otherwise.
- **Scope:** GitHub actors and local scheduled actors. Claude Code coordinators and hooks such as the
  Boundary Warden are not actors, for now.
- **Asks one question,** `theme` (`fantasy` or `sci-fi`, question 11): the default for new manifests. Each
  manifest records its own.

### The manifest is the source of truth (questions 3, 4, 5, 9, 11)

One file per actor, `.github/actors/<slug>.yml`, the project's own; the overlay ships its JSON Schema,
`.github/actors/actor.schema.json`. Vault actor notes are generated from it (`actors.py note`) and checked
against it (`actors.py note <slug> --check <note>`).

| Field | Values | Notes |
|---|---|---|
| `name`, `emoji`, `description` | text | `description` is the vault note's `role` line |
| `theme` | `fantasy`, `sci-fi` | the avatar's style block (question 11) |
| `status` | `planned`, `created`, `installed`, `active`, `retired`, `superseded`, `deleted` | question 5 |
| `identity` | `github-app`, `github-actions`, `github-native`, `local` | |
| `runs_as` | text | every identity but `github-app` |
| `app`, `app_id`, `client_id`, `key` | slug, integer, `Iv…`, a pass path | `github-app` only; ids are public; `key` is never the key |
| `roles` | `update`, `release`, `brain-pull` | `github-app` only; names its secrets |
| `fallback` | `required`, `github-token` | `github-app` only, per actor (question 9) |
| `permissions` | `scope: read\|write\|admin` | least needed |
| `acts_on` | `owner/name` list | never `repos:` |
| `trigger` | `workflows`, `events`, `schedule`, `command` | at least one |
| `merge_policy` | `never`, `auto-on-ci`, `auto-after-codeowner` | question 8 |
| `merge_method` | `squash` (default), `merge`, `rebase` | a live branch merges |
| `labels` | `{name, color, description}` | |
| `branch_prefix` | text | required unless `merge_policy: never` |
| `avatar` | `{file: <slug>.png, prompt}` | the prompt without its style block |
| `superseded_by` | a slug, or a list of `{actor, scope}` | whole, or partial |

**Shared Apps** (question 4): one App may serve several actors (Chronicler on Tagwarden's). Each names the same
`app`, and the check fails if they disagree on its ids or key.

**Supersession** (question 5): `superseded_by: <slug>` means wholly replaced, and the status must be
`superseded`. A list of `{actor, scope}` is a partial supersession: each successor took over the part named,
and the actor keeps its own status (it can't be `superseded`). The vault's check-vault learns the same in
template-obsidian, a separate change.

### Secrets are named by role (question 2)

`update` → `UPDATE_APP_CLIENT_ID` (a variable) and `UPDATE_APP_PRIVATE_KEY` (a secret), replacing
`PIN_BUMP_APP_*`; `release` → `RELEASE_APP_*`, unchanged; `brain-pull` → `BRAIN_PULL_APP_*`, replacing
`SCOUT_APP_*`. A workflow needs a role, and which actor fills it differs by owner (Courier or Ansible,
Tagwarden or Stardate), so actor-keyed names would make every workflow a per-owner answer. One App per role:
the check fails when two live Apps fill one role. The legacy names keep working for one release cycle through
the shim in template-base's mint action; `actors-setup.py --legacy` keeps them current until then.

### What it renders

- **`scripts/actors.py`** (PEP 723: pyyaml, jsonschema): `check [--local]`, `docs [--check]`, `new`, `labels`,
  `note`, `automerge`.
- **`scripts/checks.d/40-actors.sh`:** the schema and enums; the rules a schema can't say (`app_*`, `roles`
  and `fallback` only on `github-app` actors, one App per role, shared Apps agree, labels agree, a release
  actor never has `workflows` (template-base 0002), an active actor's workflows exist, nothing that looks like
  a private key or token); `.github/actors/README.md` up to date; locally, each live key's pass entry exists
  (its `.gpg` file, never decrypted); and avatars kept in `.github/actors/` meet the spec.
- **`scripts/actors-setup.py`:** for each live `github-app` actor and role, `gh variable set
  <ROLE>_APP_CLIENT_ID` and `pass show <key> | gh secret set <ROLE>_APP_PRIVATE_KEY`, through a pipe it never
  reads; `--dry-run`; `-R` for other repositories; warns about a stale org-level variable or secret with a
  role's name, and legacy or unknown `*_APP_*` names; then creates the labels.
- **The PR contract,** rendered as documentation in `.github/actors/README.md` (`actors.py docs`): a
  Conventional Commit title (base's `pr-title.yml`), the actor's branch prefix, its labels and its merge
  policy. **`actors-labels.yml`** creates the labels idempotently (`gh label create --force`) when the
  manifests change on `main`. **`actors-automerge.yml`** queues auto-merge (`gh pr merge --auto`) on a PR a
  `github-app` actor opened from its own branch in this repository, when its policy is `auto-on-ci` or
  `auto-after-codeowner`, and adds its labels. It reads the base branch's manifests, so a PR can't grant
  itself a policy, and never approves. A local actor queues its own; Dependabot's is base's.
- **`scripts/actors-cron.sh`,** the local-cron kit, generalised from the brain's `tools/brain-commit.sh`
  (Night Scribe): it refuses to run off its work branch or mid-git-operation, commits as a bot identity, test-
  merges the base branch with `git merge-tree` and merges only when clean, pushes with a deploy key through
  `core.sshCommand`, proposes through a disposable PR branch with auto-merge, logs with timestamps, and
  prints its crontab line (`--crontab`). Local actors keep the deploy key and the owner's `gh` login in v1
  (question 7).
- **`scripts/actors-avatar.py`:** the avatar spec (question 10) and its post-processing: PNG, 1024², at most 256
  colours, under 1 MB, legible at 20 to 64 px (a 20 px copy's contrast). Generating the image stays manual.

### What it doesn't

Keys or tokens (only pass paths), vault notes, App creation or installation, rulesets, "Allow Actions to create
PRs", the pass layout, hard-coded owners or repositories, and anything template-base owns (base-ci, pr-title,
template-update, Dependabot auto-merge, CODEOWNERS, gitleaks). **No token mint of its own:** where an actor
workflow needs an App token it calls template-base's `.github/actions/app-token` (template-base decision 0004).
`actors-automerge.yml` queues with the `update` role's App (`UPDATE_APP_*`, legacy `PIN_BUMP_APP_*`), so the
merge commit starts `main`'s workflows, and falls back to `GITHUB_TOKEN` without it.

## Alternatives considered

- **Actor-keyed secret names** (`COURIER_APP_*`): every rendered workflow would need a per-owner answer.
  *Rejected* (question 2).
- **The vault note as the source of truth:** a repository's CI can't read the brain. *Rejected* (question 3).
- **The merge policy enforced through rulesets:** rulesets are Stephen's, set by hand. *Rejected*: declared,
  documented and queued; the ruleset decides what a merge needs.
- **The auto-merge workflow rendered per actor by Copier:** Copier can't see the project's manifests. It reads
  them at run time instead.
- **One fallback for all actors:** Courier must not fall back (its PRs need CI), Scout may. *Rejected*
  (question 9).

## Consequences

- A repository declares its actors in `.github/actors/`; `sh scripts/checks.sh` keeps them valid, and
  `actors-setup.py` sets their secrets from pass.
- `auto-on-ci` and `auto-after-codeowner` queue the same auto-merge: the difference is the `main` ruleset's
  required review, which the manifest declares and the README documents but nothing checks.
- Without an `update` App, `actors-automerge.yml` queues with `GITHUB_TOKEN`, so the merge starts no workflow
  on `main` (as with base's Dependabot auto-merge).
- `actors-automerge.yml` pins template-base's action to the merge commit of template-base#48 until its first
  release (v2.1.0); Renovate moves it.
- The repository needs "Allow auto-merge" for `actors-automerge.yml`, and the Apps installed for `actors-setup`'s
  secrets to be any use.

## Open questions

- **auto-on-ci versus a required review.** Where `main` requires a code owner's review, `auto-on-ci` can't merge
  unreviewed. Recommendation: leave it to each repository's ruleset (Night Scribe's brain has no required
  review); revisit if an actor needs a bypass.
- **Which App queues auto-merge.** Built with the `update` role's App (it has `contents: write` and
  `pull_requests: write`, and the merge must start `main`'s workflows). Recommendation: keep it; the
  alternative, each actor's own App, would need a role per actor.
