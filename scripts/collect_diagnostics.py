"""Diagnostics collector — gathers test artifacts for AI analysis.

Run after tests. Produces artifacts/diagnostics/report.json with:
  - validate_report.json contents
  - pytest output per suite
  - DB snapshot (instances, settings, keys, trial_queue)
  - env/config summary
  - git status
  - manager_running flag
  - per-suite passed/failed/skipped breakdown
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "diagnostics"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def _encode_path(path: Path | str) -> str:
    try:
        return Path(path).relative_to(ROOT).as_posix()
    except ValueError:
        return str(Path(path).as_posix())


def _cmd(cmd: list[str]) -> dict:
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        return {"code": r.returncode, "out": r.stdout[:3000], "err": r.stderr[:1000]}
    except Exception as e:
        return {"code": -1, "out": "", "err": str(e)[:1000]}


def _parse_pytest_breakdown(stdout: str) -> dict:
    passed = 0; failed = 0; skipped = 0
    m = re.search(r"(\d+) passed", stdout)
    if m: passed = int(m.group(1))
    m = re.search(r"(\d+) failed", stdout)
    if m: failed = int(m.group(1))
    m = re.search(r"(\d+) skipped", stdout)
    if m: skipped = int(m.group(1))
    return {"passed": passed, "failed": failed, "skipped": skipped}


def _manager_online() -> bool:
    try:
        import httpx
        resp = httpx.get("http://127.0.0.1:5000/activate", timeout=3, follow_redirects=False)
        return resp.status_code in (200, 307, 302)
    except Exception:
        return False


def _db_snapshot() -> dict:
    try:
        sys.path.insert(0, str(ROOT))
        from manager.db import get_db
        with get_db() as conn:
            instances = conn.execute(
                "SELECT instance_id, status, domain, path_prefix, is_trial FROM instances ORDER BY created_at DESC"
            ).fetchall()
            settings = conn.execute("SELECT key, value FROM system_settings ORDER BY key").fetchall()
            keys = conn.execute("SELECT status, COUNT(*) as cnt FROM activation_keys GROUP BY status").fetchall()
            trial_queue = conn.execute("SELECT status, COUNT(*) as cnt FROM trial_queue GROUP BY status").fetchall()
        return {
            "instances": [dict(r) for r in instances],
            "settings": [{"key": r["key"], "value": r["value"][:80]}
                         for r in settings if "token" not in r["key"].lower()],
            "keys": [dict(r) for r in keys],
            "trial_queue": [dict(r) for r in trial_queue],
        }
    except Exception as e:
        return {"error": str(e)}


def _env_summary() -> dict:
    keys = [
        "ST_RUNTIME_MODE", "ST_ROUTING_MODE", "ST_BASE_DOMAIN",
        "ST_DOMAIN_SUFFIX", "ST_PUBLIC_SCHEME", "ST_TRIAL_ENABLED",
        "ST_TRIAL_MAX_INSTANCES", "ST_TRIAL_IDLE_TIMEOUT",
        "ST_ENABLE_MANAGER_PATH_FALLBACK",
        "ST_NGINX_BIN", "ST_NGINX_CONF_DIR",
        "ST_DOCKER_NETWORK", "ST_DOCKER_IMAGE",
    ]
    return {k: os.getenv(k, "(not set)") for k in keys}


def _load_json(path: Path) -> dict | None:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def main():
    print("=== ST Cloud Manager Diagnostics ===")

    validate = _load_json(ROOT / "artifacts" / "validate_report.json")

    # Run suites if no validate report exists
    if not validate:
        print("Running smoke tests...")
        smoke = _cmd([sys.executable, "-m", "pytest", "tests/test_smoke.py", "-v", "--tb=line"])
        e2e_caps = _cmd([sys.executable, "-m", "pytest", "tests/test_e2e_runtime_caps.py", "-v", "--tb=line"])
        e2e_http = _cmd([sys.executable, "-m", "pytest", "tests/test_e2e_path_mode.py", "-v", "--tb=line"])
    else:
        smoke = None
        e2e_caps = _cmd([sys.executable, "-m", "pytest", "tests/test_e2e_runtime_caps.py", "-v", "--tb=line"])
        e2e_http = _cmd([sys.executable, "-m", "pytest", "tests/test_e2e_path_mode.py", "-v", "--tb=line"])

    git = _cmd(["git", "status", "--porcelain"]) if (ROOT / ".git").exists() else {"out": "(no git repo)", "code": 0}

    manager_running = _manager_online()
    db = _db_snapshot()
    env = _env_summary()

    # Per-suite breakdown
    smoke_bd = _parse_pytest_breakdown(smoke["out"]) if smoke else {}
    caps_bd = _parse_pytest_breakdown(e2e_caps["out"]) if e2e_caps else {}
    http_bd = _parse_pytest_breakdown(e2e_http["out"]) if e2e_http else {}

    http_skipped = http_bd.get("skipped", 0)
    http_passed = http_bd.get("passed", 0)

    # Compute verdict
    all_codes = []
    if smoke: all_codes.append(smoke["code"])
    if e2e_caps: all_codes.append(e2e_caps["code"])
    if e2e_http: all_codes.append(e2e_http["code"])
    failed = sum(1 for c in all_codes if c != 0)

    if failed > 0:
        verdict = "FAIL"
    elif http_skipped > 0 and http_passed == 0:
        verdict = "WARN"
    else:
        verdict = "PASS"

    report = {
        "project": "st-cloud-manager",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "manager_running": manager_running,
        "skipped_critical_tests": http_skipped > 0 and http_passed == 0,
        "git_status": git["out"].strip() if git["code"] == 0 else "(git error)",
        "env_summary": env,
        "db_snapshot": db,
        "validate_report": validate,
        "http_e2e": http_bd,
        "runtime_caps": caps_bd,
        "smoke_tests": smoke_bd if smoke else None,
    }

    report_path = ARTIFACTS / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDiagnostics: {_encode_path(report_path)}")
    print(f"Verdict: {verdict}  (manager={'UP' if manager_running else 'DOWN'}, "
          f"smoke={smoke_bd.get('passed',0)}/{smoke_bd.get('failed',0)}, "
          f"caps={caps_bd.get('passed',0)}/{caps_bd.get('failed',0)}, "
          f"http={http_bd.get('passed',0)}/{http_bd.get('failed',0)}/{http_bd.get('skipped',0)})")

    if verdict == "FAIL":
        sys.exit(1)


if __name__ == "__main__":
    main()
