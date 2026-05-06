"""Export a working instance's data/default-user as the API template.

Usage:
    python scripts/export_api_template.py INSTANCE_ID
    python scripts/export_api_template.py 5e0dyt

This copies users/{INSTANCE_ID}/data/default-user to
templates/sillytavern/data/default-user so it becomes the
template for all future instances.

After exporting, edit the JSON files to replace specific values
with {{PLACEHOLDER}} variables:
    - API key         → {{PROXY_API_KEY}}
    - API base URL    → {{API_BASE_URL}}
    - Model name      → {{API_MODEL}}
    - Instance ID     → {{INSTANCE_ID}}
"""
import sys
import shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/export_api_template.py INSTANCE_ID")
        sys.exit(1)

    instance_id = sys.argv[1]
    src = PROJECT / "users" / instance_id / "data" / "default-user"
    dst = PROJECT / "templates" / "sillytavern" / "data" / "default-user"

    if not src.exists():
        print(f"[ERROR] Source not found: {src}")
        print(f"  Is {instance_id} a valid running instance?")
        sys.exit(1)

    if dst.exists():
        shutil.rmtree(str(dst))

    # Skip runtime/generated dirs that shouldn't be templated
    skip = {"backups", "chats", "groups", "thumbnails", "vectors",
            "content.log", "stats.json", "access.log"}
    shutil.copytree(
        str(src), str(dst),
        ignore=shutil.ignore_patterns(*skip),
    )

    # Also copy cookie-secret from data root
    cookie_src = PROJECT / "users" / instance_id / "data" / "cookie-secret.txt"
    if cookie_src.exists():
        shutil.copy2(str(cookie_src), str(dst / ".." / "cookie-secret.txt"))

    print(f"[OK] Template exported: {dst}")
    print()
    print("Next steps:")
    print("  1. Edit files in templates/sillytavern/data/default-user/")
    print("  2. Replace real values with placeholders:")
    print("     API key      → {{PROXY_API_KEY}}")
    print("     API base URL → {{API_BASE_URL}}")
    print("     Model name   → {{API_MODEL}}")
    print("     Instance ID  → {{INSTANCE_ID}}")
    print("  3. Run: python scripts/apply_api_config.py INSTANCE_ID")


if __name__ == "__main__":
    main()
