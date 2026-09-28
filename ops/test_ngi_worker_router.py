from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("ngi_worker_router.py")
spec = importlib.util.spec_from_file_location("ngi_worker_router_under_test", MODULE_PATH)
router = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(router)


def config():
    return {
        "project": "Vera",
        "mode": "economical",
        "policy": {
            "primary_worker": "kimi_code",
            "fallback_order": ["codex", "claude_code"],
        },
        "workers": {
            "kimi_code": {
                "enabled": True,
                "automatic": True,
                "executable": "/root/.kimi-code/bin/kimi",
            },
            "kimi_work": {
                "enabled": False,
                "automatic": False,
            },
            "codex": {
                "enabled": True,
                "automatic": True,
                "executable": "/root/.local/bin/codex",
            },
            "claude_code": {
                "enabled": True,
                "automatic": True,
                "executable": "/root/.local/bin/claude",
                "available_after": "2026-10-01T00:00:00+03:00",
            },
        },
    }


def test_worker_chain_order():
    assert router.worker_chain(config()) == ["kimi_code", "codex", "claude_code"]


def test_claude_date_gate(monkeypatch):
    monkeypatch.setattr(router, "worker_runtime_state", lambda name: {})
    before = dt.datetime(2026, 9, 28, 5, 0, tzinfo=dt.timezone.utc)
    after = dt.datetime(2026, 10, 1, 0, 0, tzinfo=dt.timezone.utc)
    ok_before, reason_before = router.worker_eligibility("claude_code", config(), now=before)
    ok_after, reason_after = router.worker_eligibility("claude_code", config(), now=after)
    assert ok_before is False
    assert reason_before.startswith("available_after:")
    assert ok_after is True
    assert reason_after == "available"


def test_codex_available_now(monkeypatch):
    monkeypatch.setattr(router, "worker_runtime_state", lambda name: {})
    ok, reason = router.worker_eligibility(
        "codex",
        config(),
        now=dt.datetime(2026, 9, 28, 5, 0, tzinfo=dt.timezone.utc),
    )
    assert ok is True
    assert reason == "available"


def test_kimi_work_not_headless(monkeypatch):
    monkeypatch.setattr(router, "worker_runtime_state", lambda name: {})
    ok, reason = router.worker_eligibility("kimi_work", config())
    assert ok is False
    assert reason == "disabled"


def test_quota_falls_back_to_codex_without_claude(monkeypatch, tmp_path):
    calls = []
    resets = []
    snapshots = []

    monkeypatch.setattr(router, "load_config", lambda: config())
    monkeypatch.setattr(router, "worker_eligibility", lambda name, cfg: (True, "available"))

    def fake_run_worker(job_dir, meta, name, repair=None):
        calls.append(name)
        if name == "kimi_code":
            return "WORKER_BLOCKED_QUOTA", "quota"
        if name == "codex":
            return "WORKER_PASS", "pass"
        raise AssertionError("Claude must not run after Codex PASS")

    monkeypatch.setattr(router, "run_worker", fake_run_worker)
    monkeypatch.setattr(router, "_diagnostic_snapshot", lambda job_dir, meta, name: snapshots.append(name))
    monkeypatch.setattr(router, "_reset_job_worktrees", lambda meta, bases=None: resets.append((meta["job_id"], bases)))
    monkeypatch.setattr(router, "write_job", lambda job_dir, meta: None)

    meta = {
        "job_id": "JOB_TEST",
        "worker_attempts": [],
        "backend_base_sha": "b",
        "frontend_base_sha": "f",
    }
    status, _ = router.run_worker_chain(tmp_path, meta)
    assert status == "WORKER_PASS"
    assert calls == ["kimi_code", "codex"]
    assert snapshots == ["kimi_code"]
    assert len(resets) == 1
    assert meta["worker"] == "codex"


def test_real_blocker_does_not_spend_fallback(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(router, "load_config", lambda: config())
    monkeypatch.setattr(router, "worker_eligibility", lambda name, cfg: (True, "available"))

    def fake_run_worker(job_dir, meta, name, repair=None):
        calls.append(name)
        return "WORKER_BLOCKED", "needs product decision"

    monkeypatch.setattr(router, "run_worker", fake_run_worker)
    monkeypatch.setattr(router, "write_job", lambda job_dir, meta: None)

    meta = {
        "job_id": "JOB_TEST",
        "worker_attempts": [],
        "backend_base_sha": "b",
        "frontend_base_sha": "f",
    }
    status, _ = router.run_worker_chain(tmp_path, meta)
    assert status == "WORKER_BLOCKED"
    assert calls == ["kimi_code"]


def test_codex_command_uses_workspace_write_without_approve_for_me(monkeypatch, tmp_path):
    captured = {}

    cfg = config()
    cfg["workers"]["codex"]["timeout_seconds"] = 600
    monkeypatch.setattr(router, "load_config", lambda: cfg)
    monkeypatch.setattr(router, "worker_eligibility", lambda name, cfg: (True, "available"))

    worktree = tmp_path / "repo"
    worktree.mkdir()
    meta = {
        "job_id": "JOB_TEST",
        "task": "docs only",
        "worktree": str(worktree),
        "base_sha": "b",
        "repair_attempts": 0,
    }

    def fake_run(cmd, cwd=None, check=True, capture=True, timeout=None, stdin_devnull=False):
        captured["cmd"] = list(cmd)
        captured["timeout"] = timeout
        captured["stdin_devnull"] = stdin_devnull
        if "--output-last-message" in cmd:
            idx = cmd.index("--output-last-message")
            Path(cmd[idx + 1]).write_text("WORKER_PASS\n", encoding="utf-8")
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(router, "run", fake_run)
    monkeypatch.setattr(router, "write_job", lambda job_dir, meta: None)

    status, _ = router.run_worker(tmp_path, meta, "codex")
    assert status == "WORKER_PASS"
    assert "--sandbox" in captured["cmd"]
    assert "workspace-write" in captured["cmd"]
    assert "--approve-for-me" not in captured["cmd"]
    assert 'approval_policy="never"' in captured["cmd"]
    assert captured["timeout"] == 600
    assert captured["stdin_devnull"] is True


def test_worker_prompt_uses_coordinator_preflight():
    meta = {
        "job_id": "JOB_TEST",
        "worktree": "/tmp/vera-job",
        "base_sha": "repo-sha",
        "preflight": (
            "READ FIRST:\n"
            "- vera_bot.py::cb_sunday_companion\n"
            "- docs/VERA_PROJECT_STATUS.md\n"
            "DO NOT inspect landing or backups."
        ),
    }
    prompt = router.worker_prompt(meta, "Implement one focused change.", "kimi_code")
    assert "COORDINATOR TECHNICAL PREFLIGHT" in prompt
    assert "vera_bot.py::cb_sunday_companion" in prompt
    assert "docs/VERA_PROJECT_STATUS.md" in prompt
    assert "Do not run broad repository inventory commands" in prompt
    assert "Do NOT read the whole roadmap" in prompt


def test_worker_prompt_without_preflight_still_forbids_broad_scan():
    meta = {
        "job_id": "JOB_TEST",
        "worktree": "/tmp/vera-job",
        "base_sha": "repo-sha",
        "preflight": "",
    }
    prompt = router.worker_prompt(meta, "Docs-only task.", "codex")
    assert "preflight: not supplied".lower() in prompt.lower()
    assert "repo-wide recursive grep" in prompt

def test_actual_dirty_worktree_reset(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    git("init")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD").stdout.strip()
    tracked.write_text("dirty\n", encoding="utf-8")
    (repo / "task.tmp").write_text("untracked\n", encoding="utf-8")
    router._reset_job_worktrees({"worktree": str(repo), "base_sha": base})
    assert git("status", "--porcelain", "--untracked-files=all").stdout.strip() == ""
    assert tracked.read_text(encoding="utf-8") == "base\n"
    assert not (repo / "task.tmp").exists()
