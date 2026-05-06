"""Renew an instance for additional days.

Usage:
    python scripts/renew_instance.py abc123
    python scripts/renew_instance.py abc123 --days 30
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manager.db import init_db
from manager.instance_service import renew_instance, get_instance


def main():
    parser = argparse.ArgumentParser(description="Renew a SillyTavern instance")
    parser.add_argument("instance_id", type=str, help="Instance ID")
    parser.add_argument("--days", type=int, default=30, help="Days to add")
    args = parser.parse_args()

    init_db()
    inst = get_instance(args.instance_id)
    if not inst:
        print(f"[ERROR] Instance not found: {args.instance_id}", file=sys.stderr)
        sys.exit(1)

    result = renew_instance(args.instance_id, days=args.days)
    print(f"[OK] Instance {args.instance_id} renewed")
    print(f"New expiry: {result['expires_at']}")


if __name__ == "__main__":
    main()
