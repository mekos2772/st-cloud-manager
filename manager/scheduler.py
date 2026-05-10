import time
from manager.instance_service import check_expired, check_trial_idle, process_trial_queue

INTERVAL_SECONDS = 60
QUEUE_INTERVAL_SECONDS = 30


def run_scheduler():
    print(f"[scheduler] started, checking every {INTERVAL_SECONDS}s")
    last_queue_check = 0
    while True:
        try:
            # Expired instance cleanup
            count = check_expired()
            if count > 0:
                print(f"[scheduler] expired {count} instance(s)")

            # Trial idle detection & release
            idle_count = check_trial_idle()
            if idle_count > 0:
                print(f"[scheduler] released {idle_count} idle trial instance(s)")

            # Trial queue processing (check every QUEUE_INTERVAL_SECONDS)
            now = time.time()
            if now - last_queue_check >= QUEUE_INTERVAL_SECONDS:
                processed = process_trial_queue()
                if processed > 0:
                    print(f"[scheduler] processed {processed} queued trial(s)")
                last_queue_check = now

        except Exception as e:
            print(f"[scheduler] error: {e}")

        time.sleep(INTERVAL_SECONDS)
