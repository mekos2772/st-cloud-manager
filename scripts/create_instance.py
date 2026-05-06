"""Create a SillyTavern instance from an activation key.

Usage:
    python scripts/create_instance.py ST-A8K2-Q9XM
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from manager.db import init_db
from manager.instance_service import create_instance


def main():
    parser = argparse.ArgumentParser(description="Create a SillyTavern instance")
    parser.add_argument("key", type=str, help="Activation key")
    args = parser.parse_args()

    init_db()

    try:
        result = create_instance(args.key)
        print(f"URL:       {result['url']}")
        print(f"Username:  {result['username']}")
        print(f"Password:  {result['password']}")
        print(f"Expires:   {result['expires_at']}")
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
