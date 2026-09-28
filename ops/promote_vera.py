#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path("/root/vera")
FORBIDDEN = (".env", ".db", ".sqlite", "backup", "backups/", "credential", "secret", ".pem", ".key")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=str(REPO), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p


def validate_main() -> str:
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


def promote(branch: str, deploy: bool) -> dict[str, object]:
    head = validate_main()
    run(["git", "fetch", "origin", branch])
    candidate = run(["git", "rev-parse", f"origin/{branch}"]).stdout.strip()
    if run(["git", "merge-base", "--is-ancestor", head, candidate], check=False).returncode != 0:
        raise RuntimeError("candidate is not a descendant of current main")
    paths = [x for x in run(["git", "diff", "--name-only", f"{head}..{candidate}"]).stdout.splitlines() if x.strip()]
    for path in paths:
        low = path.lower()
        if any(fragment in low for fragment in FORBIDDEN):
            raise RuntimeError(f"forbidden path in candidate: {path}")
    run(["git", "merge", "--ff-only", candidate])
    run(["git", "push", "origin", "main"])
    remote_after = run(["git", "rev-parse", "origin/main"]).stdout.strip()
    if remote_after != candidate:
        raise RuntimeError("origin/main SHA mismatch after push")
    result: dict[str, object] = {"status": "PROMOTED", "from": head, "to": candidate, "changed": paths}
    if deploy:
        p = subprocess.run([sys.executable, str(REPO / "ops/deploy_vera.py")], cwd=str(REPO), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result["deploy_stdout"] = p.stdout
        result["deploy_stderr"] = p.stderr
        if p.returncode != 0:
            raise RuntimeError("promotion succeeded but deploy failed; inspect deploy output")
        result["status"] = "PASS"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Fast-forward Vera Router branch to main")
    ap.add_argument("--branch", required=True)
    ap.add_argument("--deploy", action="store_true")
    args = ap.parse_args()
    print(json.dumps(promote(args.branch, args.deploy), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
