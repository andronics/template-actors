# Design records

Why template-actors is the way it is. Copy [0000-template.md](0000-template.md) to `NNNN-short-title.md`
and add a line below. **Read this index first.** The split itself is template-base's decision 0001; the design
is the brain's idea note **🤹 Template Actors**.

| # | Title | Status | Decision in one line |
|---|---|---|---|
| [0001](0001-actors-overlay.md) | The actors feature overlay | Proposed | One manifest per actor, `.github/actors/<slug>.yml`, as the source of truth; role-keyed secrets set from pass by `actors-setup.py`; a declared merge policy queued by `actors-automerge.yml`; the local-cron kit and the avatar spec; no token mint of its own (template-base's) |
