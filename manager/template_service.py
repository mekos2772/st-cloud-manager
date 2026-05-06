import shutil
from pathlib import Path
from manager.config import TEMPLATES_DIR, USERS_DIR

SKIP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff2", ".ttf", ".otf"}
SKIP_DIRS = {"backups", "chats", "groups", "thumbnails", "vectors"}


def copy_template(instance_id: str) -> tuple[Path, Path, Path]:
    src = TEMPLATES_DIR / "sillytavern"
    dst = USERS_DIR / instance_id

    if dst.exists():
        raise FileExistsError(f"Instance directory already exists: {dst}")

    shutil.copytree(str(src), str(dst))

    # Remove .tpl suffix from files (rename config.yaml.tpl → config.yaml)
    for tpl_file in dst.rglob("*.tpl"):
        target = tpl_file.with_suffix("")
        tpl_file.rename(target)

    return (
        dst / "config",
        dst / "data",
        dst / "plugins",
    )


def render_config(instance_dir: Path, variables: dict[str, str]):
    """Recursively replace {{PLACEHOLDER}} variables in all text files."""
    for file_path in instance_dir.rglob("*"):
        if file_path.is_dir():
            continue
        suffix = file_path.suffix.lower()
        if suffix in SKIP_EXTENSIONS:
            continue
        # Skip dirs that grow at runtime
        if any(p in SKIP_DIRS for p in file_path.parts):
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue

        changed = False
        for key, value in variables.items():
            placeholder = "{{" + key + "}}"
            if placeholder in content:
                content = content.replace(placeholder, value)
                changed = True

        if changed:
            file_path.write_text(content, encoding="utf-8")


def archive_instance(instance_id: str, archive_dir: Path):
    src = USERS_DIR / instance_id
    if not src.exists():
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(archive_dir / instance_id))


def remove_instance_dir(instance_id: str):
    src = USERS_DIR / instance_id
    if src.exists():
        shutil.rmtree(str(src))
