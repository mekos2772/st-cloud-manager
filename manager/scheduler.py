import time
from manager.instance_service import check_expired

INTERVAL_SECONDS = 300


def run_scheduler():
    print(f"[scheduler] started, checking every {INTERVAL_SECONDS}s")
    while True:
        try:
            count = check_expired()
            if count > 0:
                print(f"[scheduler] expired {count} instance(s)")
        except Exception as e:
            print(f"[scheduler] error: {e}")
        time.sleep(INTERVAL_SECONDS)
