"""One-click local validation.

Runs deterministic checks that must pass before merging.

Modes:
    python scripts/validate_local.py              (default: WARN if manager offline)
    python scripts/validate_local.py --strict     (FAIL if manager offline)

Verdict tiers:
    PASS  = zero failures, no critical skips
    WARN  = zero failures, but HTTP E2E skipped (manager offline)
    FAIL  = one or more failures, OR strict mode + manager offline
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

MANAGER_BASE = "http://127.0.0.1:5000"
MANAGER_CHECK_URL = f"{MANAGER_BASE}/activate"


def _encode_path(path: Path | str) -> str:
    """Emit a clean relative path, avoiding console encoding issues."""
    try:
        return Path(path).relative_to(ROOT).as_posix()
    except ValueError:
        return str(Path(path).as_posix())


def run(cmd: list[str], timeout: int = 120) -> dict:
    tag = " ".join(cmd) if isinstance(cmd, list) else cmd
    start = time.time()
    try:
        result = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
        )
        elapsed = time.time() - start
        return {
            "tag": tag, "code": result.returncode,
            "stdout": result.stdout[-4000:], "stderr": result.stderr[-2000:],
            "elapsed_s": round(elapsed, 2),
        }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        return {"tag": tag, "code": -1, "stdout": "", "stderr": f"TIMEOUT after {timeout}s", "elapsed_s": round(elapsed, 2)}
    except FileNotFoundError:
        return {"tag": tag, "code": -2, "stdout": "", "stderr": f"Command not found: {cmd[0]}", "elapsed_s": 0}


def check_import(module_path: str) -> dict:
    try:
        start = time.time()
        result = subprocess.run(
            [sys.executable, "-c", f"import sys; sys.path.insert(0, '.'); import {module_path}"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
        elapsed = time.time() - start
        return {"tag": f"import {module_path}", "code": result.returncode, "stdout": result.stdout[:500], "stderr": result.stderr[:500], "elapsed_s": round(elapsed, 2)}
    except Exception as e:
        return {"tag": f"import {module_path}", "code": -1, "stdout": "", "stderr": str(e)[:500], "elapsed_s": 0}


def _manager_online() -> bool:
    try:
        import httpx
        resp = httpx.get(MANAGER_CHECK_URL, timeout=3, follow_redirects=False)
        return resp.status_code in (200, 307, 302)
    except Exception:
        return False


def _parse_pytest_breakdown(stdout: str) -> dict:
    """Extract passed/failed/skipped from pytest output."""
    m = re.search(r"(\d+) passed", stdout)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", stdout)
    failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) skipped", stdout)
    skipped = int(m.group(1)) if m else 0
    return {"passed": passed, "failed": failed, "skipped": skipped}


KEY_IMPORTS = [
    "manager.app",
    "manager.runtimes.base",
    "manager.runtimes.docker_runtime",
    "manager.runtimes.process_runtime",
    "manager.services.instance_orchestrator",
    "manager.services.trial_service",
    "manager.router_service",
    "manager.routes.public",
    "manager.routes.admin",
    "manager.routes.proxy",
]

# Tag -> suite label
SUITE_LABELS = {
    "python -m compileall -q manager": "compile",
    "python -m pytest tests/test_smoke.py": "smoke",
    "python -m pytest tests/test_e2e_runtime_caps.py": "e2e_caps",
    "python -m pytest tests/test_e2e_path_mode.py": "http_e2e",
}


def main():
    parser = argparse.ArgumentParser(description="ST Cloud Manager validation")
    parser.add_argument("--strict", "--require-manager", dest="strict", action="store_true",
                        help="FAIL if manager is not running (HTTP E2E skip becomes critical)")
    args = parser.parse_args()

    import_checks = [check_import(m) for m in KEY_IMPORTS]
    compile_chk = run(["python", "-m", "compileall", "-q", "manager"])
    smoke_chk = run(["python", "-m", "pytest", "tests/test_smoke.py", "-v", "--tb=short"])
    caps_chk = run(["python", "-m", "pytest", "tests/test_e2e_runtime_caps.py", "-v", "--tb=short"])
    http_chk = run(["python", "-m", "pytest", "tests/test_e2e_path_mode.py", "-v", "--tb=short"])

    cmd_checks = [compile_chk, smoke_chk, caps_chk, http_chk]
    all_results = import_checks + cmd_checks
    total = len(all_results)
    failed = sum(1 for r in all_results if r["code"] > 0)

    # Per-suite breakdown
    suites = {}
    for chk in cmd_checks:
        label = SUITE_LABELS.get(next((k for k in SUITE_LABELS if k in chk["tag"]), chk["tag"]), "other")
        breakdown = _parse_pytest_breakdown(chk["stdout"])
        suites[label] = {"tag": chk["tag"], "code": chk["code"], **breakdown}

    http_skipped = suites.get("http_e2e", {}).get("skipped", 0)
    http_passed = suites.get("http_e2e", {}).get("passed", 0)
    manager_running = _manager_online()

    # Console
    print(f"=== ST Cloud Manager Validation ===   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"    mode: {'strict' if args.strict else 'default'}   manager: {'UP' if manager_running else 'DOWN'}")
    print()
    print("--- Imports ---")
    for r in import_checks:
        icon = "PASS" if r["code"] == 0 else "FAIL"
        print(f"  [{icon}] {r['tag']} ({r['elapsed_s']:.1f}s)")

    print("\n--- Suites ---")
    for label, s in suites.items():
        parts = [f"passed={s['passed']}", f"failed={s['failed']}"]
        if s.get("skipped", 0):
            parts.append(f"skipped={s['skipped']}")
        status = "PASS" if s["code"] == 0 else "FAIL"
        print(f"  [{status}] {label}: {', '.join(parts)}")

    # Verdict logic
    if failed > 0:
        verdict = "FAIL"
        reason = f"{failed} check(s) failed"
    elif http_skipped > 0 and http_passed == 0:
        if args.strict:
            verdict = "FAIL"
            reason = "HTTP E2E skipped — manager not running (strict mode)"
        else:
            verdict = "WARN"
            reason = "HTTP E2E skipped — manager not running"
    else:
        verdict = "PASS"
        reason = "all checks green"

    print(f"\nVerdict: {verdict}")
    print(f"  {reason}")

    # Show failures
    for r in all_results:
        if r["code"] > 0:
            print(f"\nFAIL: {r['tag']}")
            if r["stdout"]:
                print(r["stdout"][-2000:])
            if r["stderr"]:
                print(r["stderr"][-1000:])

    # JSON report
    report = {
        "project": "st-cloud-manager",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "strict" if args.strict else "default",
        "verdict": verdict,
        "verdict_reason": reason,
        "manager_running": manager_running,
        "skipped_critical_tests": http_skipped > 0 and http_passed == 0,
        "total": total,
        "failed": failed,
        "suites": suites,
        "imports": [{"tag": r["tag"], "code": r["code"]} for r in import_checks],
    }

    report_path = ARTIFACTS / "validate_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {_encode_path(report_path)}")

    sys.exit(0 if verdict != "FAIL" else 1)


if __name__ == "__main__":
    main()
