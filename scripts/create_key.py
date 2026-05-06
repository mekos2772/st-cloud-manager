"""Generate activation keys.

Usage:
    python scripts/create_key.py --count 10 --days 30
    python scripts/create_key.py --count 5 --days 7 --plan premium
    python scripts/create_key.py --list
    python scripts/create_key.py --list --status unused
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manager.db import init_db
from manager.key_service import create_keys, list_keys


def main():
    parser = argparse.ArgumentParser(description="Generate activation keys")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--plan", type=str, default="default")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--status", type=str, default=None)
    args = parser.parse_args()

    init_db()

    if args.list:
        keys = list_keys(args.status)
        for k in keys:
            print(f"  {k['key']:20s}  {k['status']:10s}  {k['plan']:10s}  {k['days']}d  {k['created_at']}")
        print(f"  --- {len(keys)} key(s) ---")
    else:
        keys = create_keys(count=args.count, days=args.days, plan=args.plan)
        for k in keys:
            print(k)


if __name__ == "__main__":
    main()
