#!/usr/bin/env python3
"""Keep the instabox box awake when T3 Code needs it, from a GitHub Actions job.

GitHub's cron runs late or not at all, so instead of waking on a schedule this
job stays running (up to ~5h40m, then starts its own successor) and checks the
box every minute. Reading the box's state (instabox get/logs) never wakes it.

Inputs (repo variables, set by `t3-wake sync` on the box, read at job start):
  WAKE_AT   earliest T3 Code limit reset (ISO time) or "none"
  T3_BUSY   "1" while agents are working

  - T3_BUSY=1: hold an activity lease so a turn waiting on the model API is not
    idle-paused, and wake the box again after any pause it took by itself.
  - From WAKE_AT - LEAD_MINUTES: keep the box awake (waking it after its
    cooldown if needed) and make sure T3 Code and T3 Connect work.
  - From WAKE_AT + 5 min: every 10 min, `t3-wake check` nudges any thread T3
    Code did not resume by itself.
A box you paused yourself (instabox suspend) is never woken.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BOX = os.environ["BOX"]
REPO = os.environ["GITHUB_REPOSITORY"]
API = os.environ.get("INSTABOX_API_URL", "https://api.instabox.sh")
LEAD = int(os.environ.get("LEAD_MINUTES", "20")) * 60
WAKE_AT = os.environ.get("WAKE_AT", "").strip()
BUSY = os.environ.get("T3_BUSY", "").strip() == "1"
TEST = os.environ.get("TEST", "").strip() == "true"

RUN_FOR = 5 * 3600 + 40 * 60  # then hand over to a new run (job limit is 6 h)
POLL = 60
LEASE = "t3-agents"
LEASE_TTL = 600
LEASE_EVERY = 240
GRACE = 5 * 60
CHECK_EVERY = 10 * 60
STALE = 6 * 3600


def log(msg):
    print(f"[{dt.datetime.now(dt.timezone.utc):%H:%M:%S}Z] {msg}", flush=True)


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def ib(*args, timeout=120):
    """Run the instabox CLI with --json; return (ok, parsed output)."""
    try:
        r = subprocess.run(["instabox", *args, "--json"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {"error": {"code": "timeout"}}
    try:
        out = json.loads(r.stdout or "{}")
    except ValueError:
        out = {"error": {"code": "bad_output", "message": (r.stdout + r.stderr)[-300:]}}
    return r.returncode == 0 and "error" not in out, out


def err(out):
    e = out.get("error") or {}
    return e.get("code", "?"), (e.get("message") or "")[:200]


def box_exec(*cmd, timeout=300):
    """Run a command in the box; print its output. Returns the exit code."""
    r = subprocess.run(["instabox", "exec", BOX, "--timeout", "240", "--", *cmd],
                       capture_output=True, text=True, timeout=timeout)
    for line in (r.stdout + r.stderr).strip().splitlines():
        print("    " + line)
    return r.returncode


def last_suspend_trigger():
    ok, out = ib("logs", BOX)
    if not ok:
        return None
    ops = out.get("operations") or []
    ops = sorted(ops, key=lambda o: o.get("createdAt") or "")
    for op in reversed(ops):
        if op.get("type") in ("suspend", "stop"):
            return op.get("trigger") or "user"
        if op.get("type") in ("resume", "start"):
            return None
    return None


def lease(method):
    machine_id = box_id()
    if not machine_id:
        return False
    body = json.dumps({"ttlSeconds": LEASE_TTL}).encode() if method == "PUT" else None
    req = urllib.request.Request(
        f"{API}/v1/machines/{machine_id}/activity-leases/{LEASE}", data=body, method=method,
        headers={"X-API-Key": os.environ["INSTABOX_API_KEY"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            log(f"lease {method} ok (HTTP {r.status})" + (f", held {LEASE_TTL}s" if method == "PUT" else ""))
            return True
    except urllib.error.HTTPError as e:
        if method == "DELETE" and e.code == 404:
            log("no lease to release")
            return True
        try:
            code = json.loads(e.read() or b"{}").get("error", {}).get("code", "")
        except ValueError:
            code = ""
        log(f"lease {method} failed: HTTP {e.code} {code}")
    except Exception as e:  # network
        log(f"lease {method} failed: {type(e).__name__}")
    return False


_box_id = None
_woke = False


def box_id():
    global _box_id
    if not _box_id:
        ok, out = ib("get", BOX)
        _box_id = out.get("id") if ok else None
    return _box_id


def ensure_awake():
    """Return True when the box is running (waking it if it paused itself)."""
    ok, m = ib("get", BOX)
    if not ok:
        log(f"get failed: {err(m)}")
        return False
    state = m.get("state")
    if state == "running":
        return True
    cd = m.get("cooldownUntil")
    if cd and parse(cd) > utcnow():
        log(f"box {state}; in cooldown until {cd} (runtime cap), waking after it")
        return False
    trig = last_suspend_trigger()
    if trig == "user":
        log(f"box {state} by you (instabox suspend/stop); not waking it")
        return False
    log(f"box {state} (trigger: {trig}); waking")
    ok, out = ib("wake", BOX)
    if not ok:
        code, msg = err(out)
        log(f"wake failed: {code} {msg}")
        if code not in ("machine_cooldown", "operation_conflict") and trig not in ("idle", "max_run", None):
            log("trying resume")
            ok, out = ib("resume", BOX, timeout=180)
            if not ok:
                log(f"resume failed: {err(out)}")
    if ok:
        log("box running")
        global _woke
        _woke = True
    return ok


def main():
    global _woke
    start = time.time()
    last_lease = last_check = 0.0
    ensured = False
    if not BUSY:
        lease("DELETE")  # agents stopped: let the box idle-pause again

    if TEST:
        log("test run: waking the box and printing T3 status")
        if ensure_awake():
            box_exec(".local/bin/t3-wake", "ensure")
            box_exec("sh", "-c", '.local/bin/t3-wake status | sed -E "s/^([^ ]+)  .*/\\1  waiting thread/" | head -5;'
                     " systemctl --user is-active t3code.service t3-wake-sync.timer")
            ensured = True

    log(f"WAKE_AT={WAKE_AT or 'none'} T3_BUSY={'1' if BUSY else '0'}")
    while time.time() - start < RUN_FOR:
        reset = None
        if WAKE_AT and WAKE_AT != "none":
            try:
                reset = parse(WAKE_AT)
            except ValueError:
                log(f"bad WAKE_AT {WAKE_AT!r}")
        if reset and utcnow() > reset + dt.timedelta(seconds=STALE):
            reset = None
        if not BUSY and not reset:
            log("nothing waiting and no agents working; done")
            return

        due = reset is not None and utcnow() >= reset - dt.timedelta(seconds=LEAD)
        if BUSY or due:
            if ensure_awake():
                if _woke:  # it was paused: processes may need a check
                    ensured, _woke = False, False
                if not ensured:
                    log("checking T3 Code and T3 Connect")
                    ensured = box_exec(".local/bin/t3-wake", "ensure") == 0
                if BUSY and time.time() - last_lease >= LEASE_EVERY:
                    if lease("PUT"):
                        last_lease = time.time()
                if reset and utcnow() >= reset + dt.timedelta(seconds=GRACE) and time.time() - last_check >= CHECK_EVERY:
                    log("reset passed; nudging threads T3 Code did not resume")
                    box_exec(".local/bin/t3-wake", "check")
                    last_check = time.time()
            else:
                ensured = False
        else:
            wait = (reset - dt.timedelta(seconds=LEAD) - utcnow()).total_seconds()
            if int(time.time() - start) % 1800 < POLL:
                log(f"reset at {WAKE_AT}; waking in {int(wait // 60)} min")
        time.sleep(POLL)

    log("handing over to a new run")
    subprocess.run(["gh", "workflow", "run", "wake.yml", "-R", REPO], check=False)


if __name__ == "__main__":
    sys.exit(main())
