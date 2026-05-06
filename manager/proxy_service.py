import secrets
import string
import urllib.request
import json
from manager.config import PROXY_BASE_URL, PROXY_MASTER_KEY


def create_proxy_key(instance_id: str) -> str:
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    key_name = f"st-{instance_id}-{suffix}"

    if PROXY_MASTER_KEY:
        try:
            return _create_via_api(key_name)
        except Exception:
            pass

    return f"sk-st-{instance_id}-{suffix}"


def delete_proxy_key(instance_id: str):
    if PROXY_MASTER_KEY:
        try:
            _delete_via_api(instance_id)
        except Exception:
            pass


def _create_via_api(key_name: str) -> str:
    req = urllib.request.Request(
        f"{PROXY_BASE_URL.rstrip('/v1')}/key/generate",
        data=json.dumps({"key_alias": key_name}).encode(),
        headers={
            "Authorization": f"Bearer {PROXY_MASTER_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data.get("key", data.get("api_key", ""))


def _delete_via_api(instance_id: str):
    req = urllib.request.Request(
        f"{PROXY_BASE_URL.rstrip('/v1')}/key/delete",
        data=json.dumps({"key_alias": f"st-{instance_id}"}).encode(),
        headers={
            "Authorization": f"Bearer {PROXY_MASTER_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)
