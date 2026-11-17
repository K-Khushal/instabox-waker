# instabox-waker

Keeps the free instabox box `box-8889b2` available for T3 Code: awake while
agents work, and woken shortly before rate-limited threads reset so T3 Code's
own auto-resume can carry on.

- `box/t3-wake`: runs on the box (systemd user timer every 5 min). Reads T3
  Code's database read-only and sets two repo variables: `WAKE_AT` (earliest
  limit reset, or `none`) and `T3_BUSY` (`1` while agents are working). When
  either changes it starts the watchdog workflow.
- `.github/workflows/wake.yml` + `.github/watchdog.py`: the watchdog run stays
  up (handing over to a new run every ~5h40m) and checks the box every minute
  without waking it:
  - `T3_BUSY=1`: holds an instabox activity lease, so an agent waiting on the
    model API is not idle-paused, and wakes the box after any pause it took by
    itself (after the cooldown, for a runtime-cap pause).
  - From `WAKE_AT - 20 min`: keeps the box awake and runs `t3-wake ensure`
    (T3 Code server and T3 Connect tunnel; restarts T3 Code if needed).
    instabox's own T3 server (port 3774) is left stopped to save memory.
    From 5 min after the reset, every 10 min, `t3-wake check`
    sends "continue" to any thread T3 Code has not resumed.
  - A box paused by hand (`instabox suspend`) is left alone.
  The 15-minute schedule is only a backup that starts a watchdog if none runs
  (GitHub's cron is often hours late).
- `box/t3-idle-restart`: one-off tidy-up. Waits until no agent has worked for
  5 minutes, then restarts T3 Code (frees its memory) and empties swap.
  Start it with `systemd-run --user --unit t3-idle-restart ~/.local/bin/t3-idle-restart`.
- Manual test: Actions > wake box > Run workflow, tick "test".

Needs the repo secret `INSTABOX_API_KEY`.

Limits of the free plan that this cannot change: each run is capped at 120 min
(extended while the box is really in use, up to 480), and a runtime-cap pause
has a cooldown (5-20 min, longer when back to back) during which nothing can
wake the box.
