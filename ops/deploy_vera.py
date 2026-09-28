#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

REPO = Path("/root/vera")
MARKER = Path("/root/.vera_deployed_sha")
WEBROOT = Path("/var/www/sveroy.ru")
RUNTIME_LINKS = {
    Path("/root/vera_bot.py"): REPO / "vera_bot.py",
    Path("/root/vera_max_bot.py"): REPO / "vera_max_bot.py",
    Path("/root/vera_monitoring/vera_watchdog.py"): REPO / "vera_monitoring/vera_watchdog.py",
}


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=str(cwd or REPO), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p


def repo_head() -> str:
    if run(["git", "branch", "--show-current"]).stdout.strip() != "main":
        raise RuntimeError("canonical checkout must be on main")
    if run(["git", "status", "--porcelain"]).stdout.strip():
        raise RuntimeError("canonical checkout is dirty")
    run(["git", "fetch", "origin", "main"])
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    remote = run(["git", "rev-parse", "origin/main"]).stdout.strip()
    if head != remote:
        raise RuntimeError("local main != origin/main")
    return head


def deployed_base(head: str) -> str | None:
    if not MARKER.exists():
        return None
    value = MARKER.read_text(encoding="utf-8").strip()
    if not value or value == head:
        return value or None
    ok = run(["git", "cat-file", "-e", f"{value}^{{commit}}"], check=False)
    return value if ok.returncode == 0 else None


def changed_paths(base: str | None, head: str) -> list[str]:
    if not base:
        return ["vera_bot.py", "vera_max_bot.py", "vera_monitoring/vera_watchdog.py", "landing/"]
    if base == head:
        return []
    out = run(["git", "diff", "--name-only", f"{base}..{head}"]).stdout.splitlines()
    return [x.strip() for x in out if x.strip()]


def assert_runtime_links() -> None:
    for runtime, source in RUNTIME_LINKS.items():
        if not runtime.is_symlink():
            raise RuntimeError(f"runtime path is not canonical symlink: {runtime}")
        if runtime.resolve() != source.resolve():
            raise RuntimeError(f"runtime symlink target mismatch: {runtime}")


def heartbeat_age(path: str) -> float:
    return time.time() - os.stat(path).st_mtime


def health() -> dict[str, object]:
    services = {}
    for svc in ("vera-bot.service", "vera-max.service"):
        state = run(["systemctl", "is-active", svc], cwd=Path("/root")).stdout.strip()
        if state != "active":
            raise RuntimeError(f"{svc} is {state}")
        services[svc] = state
    max_health = run(["curl", "-fsS", "http://127.0.0.1:8080/health"], cwd=Path("/root")).stdout.strip()
    tg_age = heartbeat_age("/tmp/vera_telegram.heartbeat")
    max_age = heartbeat_age("/tmp/vera_max.heartbeat")
    if tg_age > 180 or max_age > 180:
        raise RuntimeError(f"stale heartbeat tg={tg_age:.1f}s max={max_age:.1f}s")
    public = run(["curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "https://sveroy.ru/"], cwd=Path("/root")).stdout.strip()
    if public != "200":
        raise RuntimeError(f"public smoke HTTP {public}")
    return {"services": services, "max_health": max_health, "tg_heartbeat_age": round(tg_age, 1), "max_heartbeat_age": round(max_age, 1), "public_http": public}


def deploy(check_only: bool = False) -> dict[str, object]:
    head = repo_head()
    assert_runtime_links()
    base = deployed_base(head)
    changed = changed_paths(base, head)
    restarts: list[str] = []
    actions: list[str] = []
    if check_only:
        return {"head": head, "base": base, "changed": changed, "health": health(), "check_only": True}
    if any(p == "landing" or p.startswith("landing/") for p in changed):
        run(["rsync", "-a", f"{REPO / 'landing'}/", f"{WEBROOT}/"], cwd=Path("/root"))
        actions.append("landing_synced")
    if "vera_bot.py" in changed:
        restarts.append("vera-bot.service")
    if "vera_max_bot.py" in changed:
        restarts.append("vera-max.service")
    if "vera_monitoring/vera_watchdog.py" in changed:
        actions.append("watchdog_code_updated_no_manual_restart")
    for svc in restarts:
        run(["systemctl", "restart", svc], cwd=Path("/root"))
        actions.append(f"restarted:{svc}")
    if restarts:
        time.sleep(3)
    checks = health()
    MARKER.write_text(head + "\n", encoding="utf-8")
    return {"head": head, "base": base, "changed": changed, "actions": actions, "health": checks}


def main() -> int:
    ap = argparse.ArgumentParser(description="Controlled Vera deploy from canonical GitHub main")
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    result = deploy(check_only=args.check_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
