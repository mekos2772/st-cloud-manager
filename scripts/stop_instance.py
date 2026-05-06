"""Stop a SillyTavern instance.

Usage:
    python scripts/stop_instance.py abc123
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manager.db import init_db
from manager.instance_service import stop_instance


def main():
    parser = argparse.ArgumentParser(description="Stop a SillyTavern instance")
    parser.add_argument("instance_id", type=str, help="Instance ID")
    args = parser.parse_args()

    init_db()
    stop_instance(args.instance_id)
    print(f"[OK] Instance {args.instance_id} stopped")


if __name__ == "__main__":
    main()
