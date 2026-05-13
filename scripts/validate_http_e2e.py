"""HTTP E2E validation — auto-starts manager, runs E2E tests, stops.

Usage:
    python scripts/validate_http_e2e.py                     (real ST runtime)
    python scripts/validate_http_e2e.py --mock-st            (fake ST server)
    python scripts/validate_http_e2e.py --port 5000 --timeout 120

Steps:
    1. Start uvicorn manager.app:app on 127.0.0.1:{port}
    2. Wait for /activate to respond
    3. Run tests/test_e2e_path_mode.py
    4. Kill manager
    5. Report

With --mock-st: sets ST_E2E_FAKE_SERVER=1 so FakeRuntime starts real
HTTP servers instead of writing dead port files.  The path_proxy can
then proxy real HTTP traffic through the manager.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))


def _encode_path(path: Path | str) -> str:
    try:
        return Path(path).relative_to(ROOT).as_posix()
    except ValueError:
        return str(Path(path).as_posix())


def _wait_for_manager(port: int, timeout: int) -> bool:
    import httpx
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/activate"
    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=2, follow_redirects=False)
            if resp.status_code in (200, 307, 302):
                print(f"  Manager ready on :{port}")
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    parser = argparse.ArgumentParser(description="HTTP E2E validation with auto-start manager")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--timeout", type=int, default=60, help="Seconds to wait for manager startup")
    parser.add_argument("--mock-st", action="store_true", help="Use FakeSTServer instead of real ST runtime")
    args = parser.parse_args()

    if args.mock_st:
        os.environ["ST_E2E_FAKE_SERVER"] = "1"
        os.environ["ST_ROUTING_MODE"] = "path"
        os.environ["ST_TRIAL_ENABLED"] = "true"
        print(f"  Mock ST mode: ST_E2E_FAKE_SERVER=1, routing=path, trial=true")

        # Write settings to DB before manager starts so it picks them up
        from manager.db import init_db
        init_db()
        from manager.settings_service import set_settings
        set_settings({"routing_mode": "path", "base_domain": "localhost", "trial_enabled": "true"})
        print("  DB settings: routing=path, trial=true")
    else:
        os.environ.pop("ST_E2E_FAKE_SERVER", None)

    print(f"=== HTTP E2E Validation ===   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")

    # Start manager
    print(f"\n1. Starting manager on :{args.port}...")
    manager = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "manager.app:app", "--host", "127.0.0.1", "--port", str(args.port)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if not _wait_for_manager(args.port, args.timeout):
        print("  FAIL: manager did not become ready")
        manager.terminate()
        manager.wait(timeout=5)
        sys.exit(1)

    # Run E2E
    print("\n2. Running HTTP E2E tests...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_e2e_path_mode.py", "-v", "--tb=short"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )
    print(result.stdout[-3000:])
    if result.stderr:
        print(result.stderr[-1000:])

    # Stop manager
    print("\n3. Stopping manager...")
    manager.terminate()
    try:
        manager.wait(timeout=5)
    except subprocess.TimeoutExpired:
        manager.kill()
    print("  Manager stopped")

    # Report
    verdict = "PASS" if result.returncode == 0 else "FAIL"
    print(f"\nVerdict: {verdict}  (exit={result.returncode})")

    report = {
        "project": "st-cloud-manager",
        "suite": "http_e2e",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "exit_code": result.returncode,
    }
    report_path = ARTIFACTS / "http_e2e_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {_encode_path(report_path)}")

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
