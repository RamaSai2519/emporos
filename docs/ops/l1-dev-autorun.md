# Track L Dev without anyone watching (EM-240)

`emporos research dev-autorun l1` does one firing of the whole Dev run and exits. A user timer fires
it every 30 minutes; the state file and the call journal make every firing resume where the last
stopped. It runs on the DEV machine only (not EC2), calls OpenAI `gpt-4o-mini-2024-07-18` and no
broker, and never calls gpt-4o (the arbiter variant is not in its list).

## What one firing does, in order

1. **extraction**: `research extraction-progress` (2024 material tier, D1 names). Done when nothing is
   pending. While pending: if another `extract-filing-text` is alive it waits; otherwise it extracts a
   slice (540 attachments, about 27 minutes) itself and re-checks. It never leaves a process behind.
2. **freeze-events**: `research freeze-events dev-2024-v1` (refuses an existing name; skipped if
   `dev-2024-v1/SNAPSHOT.json` exists).
3. **smoke**: six real events through triage on OPENAI_API_KEY. A failure halts the whole run.
4. **v1_t60, v2_t75, v3_nopanel_t60, v4_posture_t60, v5_posture_t75**, each its own
   `run-llm-variant` process: report, trades and daily CSVs, summary JSON and one line in
   `docs/research/profit/screens.jsonl`. The daily 9,000,000-token cap makes the run PAUSE (the
   firing sleeps past 00:00 UTC and carries on; the journal counts tokens per UTC day). More than 2%
   stage errors in a variant: retried on the next two firings, then the run HALTS (never skipped).
   The USD 25 runaway guard halts the run too.
5. **table**: the five variants side by side.

`~/.cache/emporos/track-l/STATUS.md` has one line per event with a UTC time; `state.json` beside it
is the state; `logs/` has each step's output. A halt stays until `"halted"` is set back to `null` in
`state.json` after the cause is fixed.

## Set up (once)

```bash
cd ~/Projects/emporos
git worktree add ../emporos-l1run -b l1-dev-run master        # pinned code; never edit it
mkdir -p ~/.config/emporos && umask 077
grep '^OPENAI_API_KEY=' ~/Projects/emporos/.env > ~/.config/emporos/l1-autorun.env
mkdir -p ~/.config/systemd/user
cp infra/systemd/user/emporos-l1-autorun.{service,timer} ~/.config/systemd/user/
```

## Enable, verify, disable

```bash
systemctl --user daemon-reload
systemctl --user enable --now emporos-l1-autorun.timer
systemctl --user list-timers emporos-l1-autorun.timer        # next / last firing
tail -n 20 ~/.cache/emporos/track-l/STATUS.md                # progress, UTC
journalctl --user -u emporos-l1-autorun -n 50 --no-pager     # the last firing's output

systemctl --user disable --now emporos-l1-autorun.timer      # stop firing
systemctl --user stop emporos-l1-autorun.service             # stop a firing in progress (resumes later)
```

`Persistent=true` makes a firing missed while the machine was off run at the next start. A user
timer only runs while the operator is logged in unless lingering is on: **`loginctl enable-linger
$USER`** (the operator's call) lets it run with nobody logged in.
