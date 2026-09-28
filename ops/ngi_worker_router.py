#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT = "vera"
REPO = Path("/root/vera")
CONFIG = REPO / ".ngi/workers.yaml"
JOBS_ROOT = Path("/root/ngi_worker_jobs")
WORKTREE_ROOT = Path("/root/ngi_worker_worktrees/vera")
STATE_ROOT = Path("/root/ngi_worker_state")
STATE_FILE = STATE_ROOT / "vera.json"
FORBIDDEN_FRAGMENTS = (
    ".env", "node_modules", "dist/", "backup", "backups/", "private_media",
    "secret", "credentials", ".pem", ".key",
)
QUOTA_RE = re.compile(
    r"quota|rate[ -]?limit|usage limit|credit|insufficient|billing|payment required|exceeded",
    re.IGNORECASE,
)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(
    cmd: list[str],
    cwd: Path | None = None,
    *,
    check: bool = True,
    capture: bool = True,
    timeout: int | None = None,
    stdin_devnull: bool = False,
) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdin=subprocess.DEVNULL if stdin_devnull else None,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
        timeout=timeout,
    )
    if check and p.returncode != 0:
        raise RuntimeError(
            f"command failed ({p.returncode}): {' '.join(map(shlex.quote, cmd))}\n"
            f"stdout={p.stdout or ''}\nstderr={p.stderr or ''}"
        )
    return p


def load_config() -> dict[str, Any]:
    if not CONFIG.is_file():
        raise RuntimeError(f"worker config missing: {CONFIG}")
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if data.get("project") != "Vera":
        raise RuntimeError("unexpected project in workers.yaml")
    return data


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"workers": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"workers": {}}


def save_state(data: dict[str, Any]) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def worker_runtime_state(name: str) -> dict[str, Any]:
    state = load_state()
    return dict(state.get("workers", {}).get(name, {}))


def set_worker_state(name: str, status: str, reason: str = "") -> None:
    state = load_state()
    state.setdefault("workers", {})[name] = {
        "status": status,
        "reason": reason,
        "updated_at": now_iso(),
    }
    save_state(state)


def repo_preflight(repo: Path) -> str:
    if run(["git", "branch", "--show-current"], repo).stdout.strip() != "main":
        raise RuntimeError(f"{repo}: canonical checkout is not on main")
    if run(["git", "status", "--porcelain"], repo).stdout.strip():
        raise RuntimeError(f"{repo}: canonical checkout is dirty")
    run(["git", "fetch", "origin", "main"], repo)
    head = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    remote = run(["git", "rev-parse", "origin/main"], repo).stdout.strip()
    if head != remote:
        raise RuntimeError(f"{repo}: local main != origin/main")
    return remote


def changed_paths(repo: Path) -> list[str]:
    parts: list[str] = []
    for cmd in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        out = run(cmd, repo).stdout.splitlines()
        parts.extend(x.strip() for x in out if x.strip())
    return sorted(set(parts))


def validate_paths(paths: list[str]) -> None:
    for path in paths:
        lowered = path.lower()
        if any(fragment in lowered for fragment in FORBIDDEN_FRAGMENTS):
            raise RuntimeError(f"forbidden path changed: {path}")


def create_job(task: str, preflight: str = "") -> dict[str, Any]:
    base = repo_preflight(REPO)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"JOB_{stamp}"
    branch = f"router/{job_id}"
    job_dir = JOBS_ROOT / job_id
    worktree = WORKTREE_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", "-b", branch, str(worktree), "origin/main"], REPO)
    meta = {
        "job_id": job_id,
        "project": PROJECT,
        "branch": branch,
        "task": task,
        "preflight": preflight.strip(),
        "base_sha": base,
        "worktree": str(worktree),
        "worker": "kimi_code",
        "repair_attempts": 0,
        "status": "CREATED",
        "created_at": now_iso(),
    }
    (job_dir / "job.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (job_dir / "task.txt").write_text(task.rstrip() + "\n", encoding="utf-8")
    if preflight.strip():
        (job_dir / "preflight.txt").write_text(preflight.rstrip() + "\n", encoding="utf-8")
    return meta

def load_job(job_id_or_path: str) -> tuple[Path, dict[str, Any]]:
    job_id = Path(job_id_or_path).name
    job_dir = JOBS_ROOT / job_id
    meta_file = job_dir / "job.json"
    if not meta_file.is_file():
        raise RuntimeError(f"job not found: {job_id}")
    return job_dir, json.loads(meta_file.read_text(encoding="utf-8"))


def write_job(job_dir: Path, meta: dict[str, Any]) -> None:
    (job_dir / "job.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def worker_prompt(meta: dict[str, Any], task: str, worker_name: str, repair: str | None = None) -> str:
    repair_block = ""
    if repair:
        repair_block = f"\nREPAIR REQUEST (one focused repair only):\n{repair}\n"

    preflight = str(meta.get("preflight") or "").strip()
    preflight_block = (
        f"\nCOORDINATOR TECHNICAL PREFLIGHT (authoritative starting scope):\n{preflight}\n"
        if preflight
        else "\nCOORDINATOR TECHNICAL PREFLIGHT: not supplied. Stay narrow and inspect only files directly required by the JOB.\n"
    )

    return f"""You are the implementation worker ({worker_name}) for Vera JOB {meta['job_id']}.

Read the compact project rules first: AGENTS.md, .ngi/project.yaml, .ngi/rules.md, .ngi/decisions.md, .ngi/workers.yaml, and docs/VERA_PROJECT_STATUS.md.
Do NOT read the whole roadmap. Read only a specifically relevant roadmap section if the JOB or preflight points to it.

JOB worktree: {meta['worktree']}
Base SHA: {meta['base_sha']}

JOB:
{task}
{preflight_block}
{repair_block}
Context-budget rules for this worker run:
- Start with files, symbols, line ranges and contracts named in COORDINATOR TECHNICAL PREFLIGHT.
- Do not run broad repository inventory commands, repo-wide recursive grep, or list hundreds of files/tests unless a specific missing contract blocks implementation.
- Do not inspect unrelated bot modules, landing code, backups, historical BotFlow jobs, reports, or generated assets unless the JOB or preflight explicitly requires them.
- If one missing dependency is needed, inspect only that directly adjacent file and explain why.
- Prefer exact symbol/file lookup over broad exploration.
- Do not re-derive decisions already stated in the preflight or .ngi/decisions.md.

Rules for this worker run:
- Work only inside the JOB worktree.
- Do not edit production checkouts, /var/www/html, services, databases, env/secrets, or other projects.
- One JOB = one scoped change. No broad audit/refactor/full suite.
- Reuse existing architecture; inspect the relevant implementation before editing.
- Run focused tests needed by the acceptance criteria. Do not deploy, restart services, merge main, or push Git.
- Do not create commits; Router packages the verified worker output into the JOB branch.
- If blocked by missing product decision/external access or a technical impossibility, stop safely.

At the END of your response include exactly one terminal marker on its own line:
WORKER_PASS
or
WORKER_BLOCKED
Before the marker give a compact report: exact files changed, focused tests, and any blocker.
"""

def _parse_available_after(value: Any) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def worker_eligibility(name: str, cfg: dict[str, Any], *, now: dt.datetime | None = None) -> tuple[bool, str]:
    worker = dict(cfg.get("workers", {}).get(name, {}))
    if not worker or not worker.get("enabled", False):
        return False, "disabled"
    if not worker.get("automatic", False):
        return False, "manual_only"
    exe = worker.get("executable")
    if not exe or not Path(str(exe)).is_file():
        return False, "executable_missing"
    runtime = worker_runtime_state(name)
    if runtime.get("status") == "paused":
        return False, runtime.get("reason") or "paused"
    available_after = _parse_available_after(worker.get("available_after"))
    current = now or dt.datetime.now(dt.timezone.utc)
    if available_after and current < available_after:
        return False, f"available_after:{available_after.isoformat()}"
    return True, "available"


def worker_chain(cfg: dict[str, Any]) -> list[str]:
    policy = cfg.get("policy", {})
    names = [str(policy.get("primary_worker") or "kimi_code")]
    names.extend(str(x) for x in (policy.get("fallback_order") or []))
    out: list[str] = []
    for name in names:
        if name not in out:
            out.append(name)
    return out


def _classify_worker_output(name: str, p: subprocess.CompletedProcess[str], final_text: str, combined: str) -> tuple[str, str]:
    if p.returncode != 0:
        if QUOTA_RE.search(combined):
            set_worker_state(name, "paused", "quota_or_credit_failure")
            return "WORKER_BLOCKED_QUOTA", combined
        return "WORKER_BLOCKED_EXECUTION", combined
    if re.search(r"(?m)^\s*WORKER_BLOCKED\s*$", final_text):
        return "WORKER_BLOCKED", combined
    if re.search(r"(?m)^\s*WORKER_PASS\s*$", final_text):
        return "WORKER_PASS", combined
    return "WORKER_BLOCKED_PROTOCOL", combined


def run_worker(job_dir: Path, meta: dict[str, Any], worker_name: str, *, repair: str | None = None) -> tuple[str, str]:
    cfg = load_config()
    eligible, reason = worker_eligibility(worker_name, cfg)
    if not eligible:
        return "WORKER_UNAVAILABLE", reason

    worker = cfg["workers"][worker_name]
    exe = str(worker["executable"])
    prompt = worker_prompt(meta, meta["task"], worker_name, repair)
    suffix = f"{worker_name}_repair_{meta.get('repair_attempts', 0)}" if repair else worker_name
    (job_dir / f"{suffix}_prompt.txt").write_text(prompt, encoding="utf-8")

    meta["status"] = "WORKER_RUNNING"
    meta["worker"] = worker_name
    meta["worker_started_at"] = now_iso()
    write_job(job_dir, meta)

    timeout_seconds = int(worker.get("timeout_seconds") or 1800)
    try:
        if worker_name == "kimi_code":
            cmd = [exe, "--prompt", prompt, "--output-format", "text"]
            p = run(cmd, Path(meta["worktree"]), check=False, timeout=timeout_seconds, stdin_devnull=True)
            stdout = p.stdout or ""
            stderr = p.stderr or ""
            final_text = stdout
        elif worker_name == "codex":
            last = job_dir / f"{suffix}_last_message.txt"
            # Headless Codex uses the normal workspace-write sandbox. The JOB
            # worktree itself is the outer isolation boundary.
            cmd = [
                exe, "exec", "--sandbox", "workspace-write",
                "-c", 'approval_policy="never"',
                "--cd", meta["worktree"],
                "--add-dir", str(job_dir), "--skip-git-repo-check", "--ephemeral",
                "--output-last-message", str(last), prompt,
            ]
            p = run(cmd, Path(meta["worktree"]), check=False, timeout=timeout_seconds, stdin_devnull=True)
            stdout = p.stdout or ""
            stderr = p.stderr or ""
            final_text = last.read_text(encoding="utf-8", errors="replace") if last.exists() else stdout
        elif worker_name == "claude_code":
            cmd = [
                exe, "-p", prompt,
                "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep", "Bash",
                "--output-format", "text", "--permission-mode", "acceptEdits",
                "--no-session-persistence",
            ]
            p = run(cmd, Path(meta["worktree"]), check=False, timeout=timeout_seconds, stdin_devnull=True)
            stdout = p.stdout or ""
            stderr = p.stderr or ""
            final_text = stdout
        else:
            return "WORKER_UNAVAILABLE", f"unsupported_worker:{worker_name}"
    except subprocess.TimeoutExpired:
        timeout_text = f"{worker_name} timed out after {timeout_seconds}s"
        (job_dir / f"{suffix}_stderr.log").write_text(timeout_text + "\n", encoding="utf-8")
        return "WORKER_BLOCKED_EXECUTION", timeout_text

    (job_dir / f"{suffix}_stdout.log").write_text(stdout, encoding="utf-8")
    (job_dir / f"{suffix}_stderr.log").write_text(stderr, encoding="utf-8")
    if final_text != stdout:
        (job_dir / f"{suffix}_final.txt").write_text(final_text, encoding="utf-8")
    combined = final_text + "\n" + stdout + "\n" + stderr
    return _classify_worker_output(worker_name, p, final_text, combined)


def _diagnostic_snapshot(job_dir: Path, meta: dict[str, Any], worker_name: str) -> None:
    wt = Path(meta["worktree"])
    diff = run(["git", "diff"], wt, check=False).stdout or ""
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], wt, check=False).stdout or ""
    (job_dir / f"failed_{worker_name}.diff").write_text(diff, encoding="utf-8")
    (job_dir / f"failed_{worker_name}_status.txt").write_text(status, encoding="utf-8")


def _reset_job_worktrees(meta: dict[str, Any], *, bases: dict[str, str] | None = None) -> None:
    wt = Path(meta["worktree"])
    base = str((bases or {}).get("repo") or meta["base_sha"])
    run(["git", "reset", "--hard", base], wt)
    run(["git", "clean", "-fd"], wt)
    if run(["git", "status", "--porcelain", "--untracked-files=all"], wt).stdout.strip():
        raise RuntimeError("worktree is not clean after fallback reset")
    if run(["git", "rev-parse", "HEAD"], wt).stdout.strip() != base:
        raise RuntimeError("worktree base mismatch after fallback reset")


def run_worker_chain(
    job_dir: Path,
    meta: dict[str, Any],
    *,
    repair: str | None = None,
    chain: list[str] | None = None,
    reset_bases: dict[str, str] | None = None,
) -> tuple[str, str]:
    cfg = load_config()
    attempts: list[dict[str, str]] = []
    names = chain or worker_chain(cfg)

    for name in names:
        eligible, reason = worker_eligibility(name, cfg)
        if not eligible:
            attempts.append({"worker": name, "status": "SKIPPED", "reason": reason})
            continue

        status, combined = run_worker(job_dir, meta, name, repair=repair)
        attempts.append({"worker": name, "status": status, "reason": ""})
        meta.setdefault("worker_attempts", []).append({"worker": name, "status": status, "at": now_iso()})
        write_job(job_dir, meta)

        if status == "WORKER_PASS":
            meta["worker"] = name
            meta["worker_attempt_summary"] = attempts
            write_job(job_dir, meta)
            return status, combined

        # A real blocker is not solved by blindly spending another coding agent.
        if status == "WORKER_BLOCKED":
            meta["worker_attempt_summary"] = attempts
            write_job(job_dir, meta)
            return status, combined

        # Protocol output needs cheap inspection/recovery, not a second model.
        if status == "WORKER_BLOCKED_PROTOCOL":
            meta["worker_attempt_summary"] = attempts
            write_job(job_dir, meta)
            return status, combined

        if status not in {"WORKER_BLOCKED_QUOTA", "WORKER_BLOCKED_EXECUTION", "WORKER_UNAVAILABLE"}:
            meta["worker_attempt_summary"] = attempts
            write_job(job_dir, meta)
            return status, combined

        _diagnostic_snapshot(job_dir, meta, name)
        _reset_job_worktrees(meta, bases=reset_bases)

    meta["worker_attempt_summary"] = attempts
    write_job(job_dir, meta)
    return "WORKER_BLOCKED_NO_AVAILABLE_WORKER", json.dumps(attempts, ensure_ascii=False)

def package_repo(repo: Path, branch: str, job_id: str, job_dir: Path) -> str | None:
    paths = changed_paths(repo)
    (job_dir / "changed_paths.txt").write_text("\n".join(paths) + ("\n" if paths else ""), encoding="utf-8")
    if not paths:
        return None
    validate_paths(paths)
    for path in paths:
        run(["git", "add", "-A", "--", path], repo)
    run(["git", "commit", "-m", f"worker: {job_id} Vera change"], repo)
    if run(["git", "status", "--porcelain"], repo).stdout.strip():
        raise RuntimeError("worktree dirty after commit")
    sha = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    run(["git", "push", "-u", "origin", f"HEAD:{branch}"], repo)
    remote = run(["git", "ls-remote", "--heads", "origin", branch], repo).stdout.strip().split()
    if not remote or remote[0] != sha:
        raise RuntimeError("remote SHA mismatch")
    return sha


def package(job_dir: Path, meta: dict[str, Any]) -> dict[str, Any]:
    sha = package_repo(Path(meta["worktree"]), meta["branch"], meta["job_id"], job_dir)
    return {"sha": sha}


def finalize_worker_run(job_dir: Path, meta: dict[str, Any], status: str, combined: str) -> int:
    meta["worker_status"] = status
    meta["worker_finished_at"] = now_iso()
    if status != "WORKER_PASS":
        meta["status"] = status
        write_job(job_dir, meta)
        print(json.dumps({"job_id": meta["job_id"], "status": status, "branch": meta["branch"]}, ensure_ascii=False))
        return 2
    try:
        packaged = package(job_dir, meta)
    except Exception as exc:
        meta["status"] = "WORKER_BLOCKED_PACKAGING"
        meta["exact_reason"] = str(exc)
        write_job(job_dir, meta)
        print(json.dumps({"job_id": meta["job_id"], "status": meta["status"], "reason": str(exc)}, ensure_ascii=False))
        return 3
    meta.update(packaged)
    meta["status"] = "WORKER_PASS"
    write_job(job_dir, meta)
    result = {
        "job_id": meta["job_id"],
        "status": "WORKER_PASS",
        "branch": meta["branch"],
        "sha": packaged["sha"],
        "job_dir": str(job_dir),
    }
    (job_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    task = args.task
    if args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8")
    if not task or not task.strip():
        raise RuntimeError("task is empty")

    preflight = args.preflight or ""
    if args.preflight_file:
        preflight = Path(args.preflight_file).read_text(encoding="utf-8")
    if len(preflight) > 24000:
        raise RuntimeError("preflight is too large; keep it narrow (max 24000 chars)")

    cfg = load_config()
    if cfg.get("mode") != "economical":
        raise RuntimeError("router v1 expects economical mode")
    meta = create_job(task.strip(), preflight.strip())
    job_dir = JOBS_ROOT / meta["job_id"]
    status, combined = run_worker_chain(job_dir, meta)
    return finalize_worker_run(job_dir, meta, status, combined)

def cmd_repair(args: argparse.Namespace) -> int:
    job_dir, meta = load_job(args.job)
    if int(meta.get("repair_attempts") or 0) >= 1:
        raise RuntimeError("economical policy allows only one same-worker repair attempt")
    instruction = args.instruction
    if args.instruction_file:
        instruction = Path(args.instruction_file).read_text(encoding="utf-8")
    if not instruction or not instruction.strip():
        raise RuntimeError("repair instruction is empty")
    worktree = Path(meta["worktree"])
    if run(["git", "status", "--porcelain"], worktree).stdout.strip():
        raise RuntimeError("repair worktree must be clean before retry")
    meta["repair_attempts"] = int(meta.get("repair_attempts") or 0) + 1
    meta["status"] = "REPAIR_RUNNING"
    write_job(job_dir, meta)

    current_worker = str(meta.get("worker") or load_config().get("policy", {}).get("primary_worker") or "kimi_code")
    cfg = load_config()
    full_chain = worker_chain(cfg)
    try:
        start = full_chain.index(current_worker)
        repair_chain = full_chain[start:]
    except ValueError:
        repair_chain = [current_worker] + [x for x in full_chain if x != current_worker]

    repair_bases = {
        "repo": run(["git", "rev-parse", "HEAD"], worktree).stdout.strip(),
    }
    status, combined = run_worker_chain(
        job_dir,
        meta,
        repair=instruction.strip(),
        chain=repair_chain,
        reset_bases=repair_bases,
    )
    return finalize_worker_run(job_dir, meta, status, combined)



def cmd_recover(args: argparse.Namespace) -> int:
    """Package an already-completed worker run after a protocol-only block.

    This never reruns a model. It is intentionally limited to jobs whose saved
    stdout contains an unambiguous WORKER_PASS marker and whose worktrees still
    contain the worker diff.
    """
    job_dir, meta = load_job(args.job)
    if meta.get("status") != "WORKER_BLOCKED_PROTOCOL":
        raise RuntimeError("recover is allowed only for WORKER_BLOCKED_PROTOCOL")
    worker_name = str(meta.get("worker") or "kimi_code")
    candidates = [
        job_dir / f"{worker_name}_final.txt",
        job_dir / f"{worker_name}_stdout.log",
        job_dir / "worker_stdout.log",
    ]
    log = next((item for item in candidates if item.is_file()), None)
    if log is None:
        raise RuntimeError("worker output missing")
    stdout = log.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"(?m)^\s*WORKER_PASS\s*$", stdout):
        raise RuntimeError("saved worker output does not contain WORKER_PASS")
    return finalize_worker_run(job_dir, meta, "WORKER_PASS", stdout)

def cmd_status(_: argparse.Namespace) -> int:
    cfg = load_config()
    state = load_state()
    rows = []
    for name, worker in cfg.get("workers", {}).items():
        exe = worker.get("executable")
        installed = bool(exe and Path(exe).exists()) if exe else False
        runtime = state.get("workers", {}).get(name, {})
        eligible, eligibility_reason = worker_eligibility(name, cfg)
        rows.append({
            "worker": name,
            "enabled": worker.get("enabled", False),
            "automatic": worker.get("automatic", False),
            "installed": installed,
            "eligible_now": eligible,
            "eligibility_reason": eligibility_reason,
            "runtime_status": runtime.get("status", "available" if worker.get("automatic") else "manual"),
            "reason": runtime.get("reason", ""),
            "available_after": worker.get("available_after"),
        })
    print(json.dumps({"project": PROJECT, "mode": cfg.get("mode"), "workers": rows}, ensure_ascii=False, indent=2))
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    set_worker_state(args.worker, "paused", args.reason or "owner_paused")
    print(f"{args.worker}=paused")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    set_worker_state(args.worker, "available", "owner_resumed")
    print(f"{args.worker}=available")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="NGI economical worker router v1")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--task", default="")
    p.add_argument("--task-file")
    p.add_argument("--preflight", default="")
    p.add_argument("--preflight-file")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("repair")
    p.add_argument("--job", required=True)
    p.add_argument("--instruction", default="")
    p.add_argument("--instruction-file")
    p.set_defaults(func=cmd_repair)
    p = sub.add_parser("recover")
    p.add_argument("--job", required=True)
    p.set_defaults(func=cmd_recover)
    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("pause")
    p.add_argument("--worker", required=True, choices=["kimi_code", "codex", "claude_code"])
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_pause)
    p = sub.add_parser("resume")
    p.add_argument("--worker", required=True, choices=["kimi_code", "codex", "claude_code"])
    p.set_defaults(func=cmd_resume)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(json.dumps({"status": "ROUTER_BLOCKED", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
