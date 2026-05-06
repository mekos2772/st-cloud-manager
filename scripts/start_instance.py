"""Start a stopped SillyTavern instance.

Usage:
    python scripts/start_instance.py abc123
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manager.db import init_db
from manager.instance_service import start_instance


def main():
    parser = argparse.ArgumentParser(description="Start a SillyTavern instance")
    parser.add_argument("instance_id", type=str, help="Instance ID")
    args = parser.parse_args()

    init_db()
    try:
        start_instance(args.instance_id)
        print(f"[OK] Instance {args.instance_id} started")
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
