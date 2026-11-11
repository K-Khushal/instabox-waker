# instabox-waker

Wakes the free instabox box `box-8889b2` shortly before T3 Code's rate-limited
threads reset, so T3 Code's own auto-resume can carry on.

- `box/t3-wake`: runs on the box (systemd user timer every 5 min). Reads T3
  Code's database read-only and sets the repo variable `WAKE_AT` to the
  earliest reset, enabling the workflow.
  While agents are working it sets `WAKE_AT=busy`, so a run-cap or idle pause
  mid-task is woken again.
- `.github/workflows/wake.yml`: every 5 min while enabled, wakes the box from
  `WAKE_AT - 20 min` (or at once when `busy`), then runs `t3-wake ensure`
  (T3 Code server answering, T3 Connect tunnel connected, instabox's T3 server
  up; restarts T3 Code if not). From 5 min after the reset, any thread T3 Code
  has not resumed by itself gets a "continue" message (`t3 thread send`). Disables itself when nothing is waiting.
- Manual test: Actions > wake box > Run workflow, tick "test".

Needs the repo secret `INSTABOX_API_KEY`.
