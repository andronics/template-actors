#!/bin/bash
# Template-owned: template-actors (decision 0001). The local-cron actor kit: a scheduled job that
# commits whatever changed in a checkout, on its work branch, as a bot identity; merges the base
# branch in when that merge is clean; pushes with a deploy key; and proposes the work branch to
# the base branch through a disposable PR branch that merges itself (gh pr merge --auto) once the
# required checks pass. Safe to run by hand any time. It never switches branch, and a conflict
# never puts markers in a file: the PR shows it instead.
#
#   scripts/actors-cron.sh --dir PATH --work-branch live --pr-branch auto/nightly \
#       --name my-cron --email my-cron@host --deploy-key ~/.ssh/my-deploy [--label automerge]
#   scripts/actors-cron.sh --crontab ...the same options...   # prints its crontab line
#
# The actor's manifest (.github/actors/<slug>.yml) declares it: identity local, merge_policy
# auto-on-ci, branch_prefix its --pr-branch, trigger.schedule its cron line.
set -euo pipefail
export PATH="${PATH:-}:/usr/local/bin:/usr/bin:/bin"   # cron's PATH is minimal

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

DIR="" WORK="" BASE=main PR_BRANCH="" NAME="" EMAIL="" KEY="" LOG="" SCHEDULE="45 2 * * *"
TITLE="" BODY="" METHOD=merge COMMIT_ALL=1 CRONTAB=0 LABELS=()
ARGS=("$@")
while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR=$2; shift 2 ;;
        --work-branch) WORK=$2; shift 2 ;;
        --base) BASE=$2; shift 2 ;;
        --pr-branch) PR_BRANCH=$2; shift 2 ;;
        --name) NAME=$2; shift 2 ;;
        --email) EMAIL=$2; shift 2 ;;
        --deploy-key) KEY=$2; shift 2 ;;
        --log) LOG=$2; shift 2 ;;
        --title) TITLE=$2; shift 2 ;;
        --body) BODY=$2; shift 2 ;;
        --label) LABELS+=("$2"); shift 2 ;;
        --merge-method) METHOD=$2; shift 2 ;;
        --no-commit) COMMIT_ALL=0; shift ;;
        --schedule) SCHEDULE=$2; shift 2 ;;
        --crontab) CRONTAB=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "error: unknown option $1" >&2; usage 2 >&2 ;;
    esac
done
for pair in DIR:dir WORK:work-branch PR_BRANCH:pr-branch NAME:name EMAIL:email; do
    var=${pair%%:*}
    if [ -z "${!var}" ]; then echo "error: --${pair#*:} is required" >&2; exit 2; fi
done
case "$METHOD" in merge|squash|rebase) ;; *) echo "error: --merge-method is merge, squash or rebase" >&2; exit 2 ;; esac
if [ "$WORK" = "$BASE" ] || [ "$PR_BRANCH" = "$BASE" ] || [ "$PR_BRANCH" = "$WORK" ]; then
    echo "error: the work branch, the PR branch and the base branch must differ" >&2; exit 2
fi
LOG=${LOG:-${XDG_STATE_HOME:-$HOME/.local/state}/actors-cron/$NAME.log}
TITLE=${TITLE:-"chore: $NAME's commits from $WORK"}
BODY=${BODY:-"$NAME's commits of what changed on \`$WORK\` (scripts/actors-cron.sh, template-actors). Merges itself once the required checks pass."}

if [ "$CRONTAB" = 1 ]; then
    # Its own options, minus --crontab and --schedule, each quoted for the shell cron runs.
    line=""
    skip=0
    for arg in "${ARGS[@]}"; do
        if [ "$skip" = 1 ]; then skip=0; continue; fi
        case "$arg" in --crontab) continue ;; --schedule) skip=1; continue ;; esac
        line+=" $(printf '%q' "$arg")"
    done
    echo "$SCHEDULE $(printf '%q' "$(cd "$(dirname "$0")" && pwd)/$(basename "$0")")$line >/dev/null 2>&1"
    exit 0
fi

DIR=$(cd "$DIR" && pwd)
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
say() { echo "$(date -Iseconds) $NAME: $*"; }
cd "$DIR"
GIT=(git -c "user.name=$NAME" -c "user.email=$EMAIL")
if [ -n "$KEY" ]; then
    GIT+=(-c "core.sshCommand=ssh -i $(printf '%q' "$KEY") -o IdentitiesOnly=yes")
fi

# Only on the work branch, and never in the middle of another git operation (a hand merge, an
# editor's git plugin, a session): the next run tries again.
head=$(git symbolic-ref --short -q HEAD || echo "a detached HEAD")
if [ "$head" != "$WORK" ]; then say "error: $DIR is on $head, not $WORK: nothing done"; exit 1; fi
gitdir=$(git rev-parse --git-dir)
for f in index.lock MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG rebase-merge rebase-apply; do
    if [ -e "$gitdir/$f" ]; then say "error: $gitdir/$f: a git operation is in progress: nothing done"; exit 1; fi
done

"${GIT[@]}" fetch -q --prune origin
if [ "$COMMIT_ALL" = 1 ]; then
    git add -A
    "${GIT[@]}" commit -q -m "chore: $NAME $(date -Iseconds)" || true   # nothing new; still propose earlier commits
fi

# The base branch's changes into the work branch. merge-tree tries the merge without touching
# the files; only a clean merge is made for real. On a conflict the work branch is still pushed
# and proposed: the PR shows it.
conflict=""
rc=0; out=$(git merge-tree --write-tree --name-only --no-messages HEAD "origin/$BASE") || rc=$?
case "$rc" in
    0) "${GIT[@]}" merge -q --no-edit "origin/$BASE" ;;
    1) conflict=$(printf '%s\n' "$out" | tail -n +2 | sort -u | paste -sd ' ')
       say "error: $WORK conflicts with origin/$BASE in: $conflict; run 'git merge origin/$BASE' in $DIR" ;;
    *) say "error: git merge-tree failed ($rc)"; exit 1 ;;
esac

"${GIT[@]}" push -q origin "$WORK"
if [ -z "$(git rev-list "origin/$BASE..HEAD")" ]; then
    say "nothing to propose"
    exit 0
fi

# The PR branch is a disposable copy of the work branch. If it has commits the work branch
# hasn't (an edit made on GitHub), stop rather than overwrite them.
if git rev-parse -q --verify "refs/remotes/origin/$PR_BRANCH" >/dev/null \
        && ! git merge-base --is-ancestor "origin/$PR_BRANCH" HEAD; then
    say "error: $PR_BRANCH has commits $WORK hasn't: merge origin/$PR_BRANCH into $WORK by hand"
    exit 1
fi
"${GIT[@]}" push -q origin "HEAD:refs/heads/$PR_BRANCH"   # a fast-forward: checked above
if [ -z "$(gh pr list --head "$PR_BRANCH" --state open --json number --jq '.[0].number // empty')" ]; then
    label_args=()
    for label in "${LABELS[@]}"; do label_args+=(--label "$label"); done
    gh pr create --base "$BASE" --head "$PR_BRANCH" --title "$TITLE" --body "$BODY" "${label_args[@]}" >/dev/null
fi
gh pr merge "$PR_BRANCH" --auto "--$METHOD"
say "proposed $(git rev-list --count "origin/$BASE..HEAD") commit(s) from $WORK in $PR_BRANCH"
[ -z "$conflict" ] || exit 1
