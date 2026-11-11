# instabox-waker

Wakes the free instabox box `box-8889b2` shortly before T3 Code's rate-limited
threads reset, so T3 Code's own auto-resume can carry on.

- `box/t3-wake`: runs on the box (systemd user timer every 5 min). Reads T3
  Code's database read-only and sets the repo variable `WAKE_AT` to the
  earliest reset, enabling the workflow.
- `.github/workflows/wake.yml`: every 5 min while enabled, wakes the box from
  `WAKE_AT - 20 min`; after the reset, checks T3 Code resumed (restarts it if
  not); disables itself when nothing is waiting.

Needs the repo secret `INSTABOX_API_KEY`.
