"""Re-apply API template variables to an existing instance and restart container.

Usage:
    python scripts/apply_api_config.py INSTANCE_ID

This rereads the API template from templates/sillytavern/data/default-user,
replaces {{PLACEHOLDER}} variables with the instance's actual values,
then restarts the container.
"""
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from manager.db import init_db, get_db
from manager.config import USERS_DIR, API_BASE_URL, API_MODEL
from manager.template_service import render_config
from manager.docker_service import stop_container, start_container


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/apply_api_config.py INSTANCE_ID")
        sys.exit(1)

    instance_id = sys.argv[1]
    init_db()

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()

    if not row:
        print(f"[ERROR] Instance not found: {instance_id}")
        sys.exit(1)

    inst = dict(row)
    instance_dir = USERS_DIR / instance_id
    template_dir = PROJECT / "templates" / "sillytavern" / "data" / "default-user"

    if not template_dir.exists():
        print("[WARNING] 未检测到 API 配置模板。")
        print("  请手动配置一次酒馆并运行:")
        print(f"  python scripts/export_api_template.py {instance_id}")
        print("  然后将模板中的真实值替换为占位符 {{API_BASE_URL}} {{API_MODEL}} {{PROXY_API_KEY}}")

    # Rerender with current instance variables
    container = inst["container_name"]
    stop_container(container)

    # Copy template data files into instance
    if template_dir.exists():
        import shutil
        dst_data = instance_dir / "data" / "default-user"
        if dst_data.exists():
            shutil.rmtree(str(dst_data))
        shutil.copytree(str(template_dir), str(dst_data))

    # Render all variables in the instance directory
    render_config(instance_dir, {
        "INSTANCE_ID": instance_id,
        "USERNAME": inst["username"],
        "PASSWORD": inst["password"],
        "API_BASE_URL": API_BASE_URL,
        "API_MODEL": API_MODEL,
        "PROXY_API_KEY": inst["api_key"],
    })

    start_container(container)
    print(f"[OK] API config applied to {instance_id}, container restarted")


if __name__ == "__main__":
    main()
