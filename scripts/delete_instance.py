"""Delete a SillyTavern instance (container + archive data).

Usage:
    python scripts/delete_instance.py abc123
    python scripts/delete_instance.py abc123 --force  # skip confirmation
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manager.db import init_db
from manager.instance_service import delete_instance, get_instance


def main():
    parser = argparse.ArgumentParser(description="Delete a SillyTavern instance")
    parser.add_argument("instance_id", type=str, help="Instance ID")
    parser.add_argument("--force", action="store_true", help="Skip confirmation")
    args = parser.parse_args()

    init_db()
    inst = get_instance(args.instance_id)
    if not inst:
        print(f"[ERROR] Instance not found: {args.instance_id}", file=sys.stderr)
        sys.exit(1)

    print(f"Instance: {inst['instance_id']}")
    print(f"Domain:   {inst['domain']}")
    print(f"Status:   {inst['status']}")
    print(f"Created:  {inst['created_at']}")
    print(f"Expires:  {inst['expires_at']}")

    if not args.force:
        confirm = input("\nDelete this instance? Data will be archived. [y/N]: ")
        if confirm.lower() != "y":
            print("Cancelled")
            sys.exit(0)

    delete_instance(args.instance_id)
    print(f"[OK] Instance {args.instance_id} deleted (data archived)")


if __name__ == "__main__":
    main()
