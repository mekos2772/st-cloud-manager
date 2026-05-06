"""List all instances.

Usage:
    python scripts/list_instances.py
    python scripts/list_instances.py --status running
    python scripts/list_instances.py --status expired
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manager.db import init_db
from manager.instance_service import list_instances


def main():
    parser = argparse.ArgumentParser(description="List SillyTavern instances")
    parser.add_argument("--status", type=str, default=None)
    args = parser.parse_args()

    init_db()
    instances = list_instances(args.status)
    if not instances:
        print("No instances found")
        return

    print(f"{'ID':10s}  {'Domain':30s}  {'Status':10s}  {'Created':20s}  {'Expires':20s}")
    print("-" * 100)
    for inst in instances:
        print(
            f"{inst['instance_id']:10s}  "
            f"{inst['domain']:30s}  "
            f"{inst['status']:10s}  "
            f"{inst['created_at']:20s}  "
            f"{inst['expires_at']:20s}"
        )
    print(f"  --- {len(instances)} instance(s) ---")


if __name__ == "__main__":
    main()
