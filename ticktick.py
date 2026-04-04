"""TickTick API integration for VoiceType agent.

Uses OAuth2 access_token to create/list/complete tasks via TickTick Open API.
Token is stored in VoiceType config and refreshed automatically.
"""

import json
import logging
import os
import time

import httpx

log = logging.getLogger("VoiceType")

API_URL = "https://api.ticktick.com/open/v1"
TOKEN_URL = "https://ticktick.com/oauth/token"

# Credentials
CLIENT_ID = "Am6pcJdH97QTMlo3wY"
CLIENT_SECRET = "1Nt1xXhtp4axFlucmYNh9idXpNxxBH56"
DEFAULT_PROJECT = "69c2f0958f0815a2ae426caa"

# Token file (local)
_TOKEN_FILE = os.path.expanduser("~/Library/Application Support/VoiceType/ticktick_token.json")


def _load_token():
    try:
        with open(_TOKEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_token(data):
    os.makedirs(os.path.dirname(_TOKEN_FILE), exist_ok=True)
    with open(_TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _get_access_token():
    """Get valid access token, refreshing if needed."""
    token_data = _load_token()
    if not token_data:
        return None

    # Check if expired
    expires_at = token_data.get("expires_at", 0)
    if time.time() > expires_at - 60:  # refresh 1 min before expiry
        refreshed = _refresh_token(token_data.get("refresh_token"))
        if refreshed:
            return refreshed.get("access_token")
        return None

    return token_data.get("access_token")


def _refresh_token(refresh_token):
    """Refresh the access token."""
    if not refresh_token:
        return None
    try:
        import base64
        auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Authorization": f"Basic {auth}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            token_data = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "expires_at": time.time() + data.get("expires_in", 86400),
            }
            _save_token(token_data)
            log.info("TickTick token refreshed")
            return token_data
        log.warning("TickTick refresh failed: %s", resp.status_code)
    except Exception as e:
        log.error("TickTick refresh error: %s", e)
    return None


def exchange_code(code):
    """Exchange OAuth authorization code for tokens."""
    import base64
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://agentix-labs.ru/webhook.php?action=ticktick_callback",
        },
        headers={"Authorization": f"Basic {auth}"},
        timeout=10,
    )
    if resp.status_code == 200:
        data = resp.json()
        token_data = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": time.time() + data.get("expires_in", 86400),
        }
        _save_token(token_data)
        log.info("TickTick authorized successfully")
        return True
    log.error("TickTick code exchange failed: %s %s", resp.status_code, resp.text[:200])
    return False


def set_token(access_token, refresh_token=""):
    """Manually set access token (e.g. from server)."""
    _save_token({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": time.time() + 86400 * 30,  # assume 30 days
    })


def is_connected():
    """Check if TickTick is connected."""
    return _get_access_token() is not None


def _api(method, path, data=None):
    """Make authenticated API request."""
    token = _get_access_token()
    if not token:
        raise RuntimeError("TickTick не подключён")
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{API_URL}{path}"

    if method == "GET":
        resp = httpx.get(url, headers=headers, timeout=10)
    elif method == "POST":
        resp = httpx.post(url, json=data, headers=headers, timeout=10)
    elif method == "DELETE":
        resp = httpx.delete(url, headers=headers, timeout=10)
    else:
        resp = httpx.request(method, url, json=data, headers=headers, timeout=10)

    if resp.status_code in (200, 201):
        return resp.json() if resp.text else {}
    raise RuntimeError(f"TickTick API: {resp.status_code}")


# --- Public API ---

def create_task(title, content="", priority=0, due_date=None, due_time=None,
                tags=None, project_id=None):
    """Create a task in TickTick."""
    task = {
        "title": title,
        "content": content,
        "priority": priority,
        "projectId": project_id or DEFAULT_PROJECT,
    }
    if due_date:
        t = due_time or "11:00"
        task["dueDate"] = f"{due_date}T{t}:00+0300"
    if tags:
        task["tags"] = tags
    return _api("POST", "/task", task)


def get_projects():
    """Get all projects/lists."""
    return _api("GET", "/project")


def get_tasks(project_id=None):
    """Get tasks from a project."""
    pid = project_id or DEFAULT_PROJECT
    data = _api("GET", f"/project/{pid}/data")
    return data.get("tasks", [])


def complete_task(project_id, task_id):
    """Complete a task."""
    return _api("POST", f"/project/{project_id}/task/{task_id}/complete")


def get_all_tasks():
    """Get tasks from all projects."""
    projects = get_projects()
    all_tasks = []
    for p in projects:
        try:
            data = _api("GET", f"/project/{p['id']}/data")
            for task in data.get("tasks", []):
                task["_project_name"] = p.get("name", "")
                all_tasks.append(task)
        except Exception:
            continue
    return all_tasks
