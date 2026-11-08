"""Claude Code chats: Anthropic's sessions API (what claude.ai and the phone app read), and an optional sessions
server on this machine that starts Remote Control chats and brings stopped ones back.

The API is called as the signed-in Claude Code CLI, with its OAuth token (CLAUDE_DIR/.credentials.json) and
organization (~/.claude.json), read fresh for each call so a refreshed token is picked up.

The sessions server (KINDLE_SESSIONS_URL, e.g. devbox-hub's) speaks:
    GET  /api/sessions/<id>           {"session", "cwd", "running", ...} of a chat of this machine's folders; 404: not one
    GET  /api/sessions/folders        {"folders": [...]}: the folders it has run chats in
    POST /api/sessions                {"cwd"}: a new chat there -> {"session", ...}; 403 a folder it won't use
    POST /api/sessions/<id>/connect   bring a stopped chat back
"""

import json
import urllib.error
import urllib.request

from ink import config

API = "https://api.anthropic.com"


class Refused(Exception):
    pass


def api(method, path, body=None, *, headers=None):
    """Anthropic's sessions API, as the signed-in CLI. RuntimeError when it's unreachable or refuses."""
    token = json.loads((config.CLAUDE_DIR / ".credentials.json").read_text())["claudeAiOauth"]["accessToken"]
    org = json.loads(config.CLAUDE_JSON.read_text())["oauthAccount"]["organizationUuid"]
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + token, "anthropic-version": "2023-06-01", "content-type": "application/json",
        "anthropic-beta": "ccr-byoc-2025-07-29,environments-2025-11-01", "x-organization-uuid": org,
        **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"claude.ai said {e.code}: {e.read()[:200].decode(errors='replace')}")
    except OSError as e:
        raise RuntimeError(f"claude.ai unreachable: {e}")


# ---- The sessions server ------------------------------------------------------------------------------------

def _local(method, path, body=None, timeout=20):
    """The sessions server: its JSON, or None for a 404. Refused for a 403, RuntimeError otherwise."""
    if not config.SESSIONS_URL:
        raise RuntimeError("no sessions server (KINDLE_SESSIONS_URL)")
    req = urllib.request.Request(config.SESSIONS_URL + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 403:
            raise Refused(path)
        try:
            why = json.loads(e.read()).get("error")
        except ValueError:
            why = None
        raise RuntimeError(why or f"the sessions server said {e.code}")
    except OSError as e:
        raise RuntimeError(f"the sessions server is unreachable: {e}")


def lookup(session):
    """The chat as the sessions server knows it ({"running", ...}), or None (not one of its chats, or no server)."""
    if not config.SESSIONS_URL:
        return None
    return _local("GET", f"/api/sessions/{session}")


def connect(session):
    """Bring a stopped chat back (starting its folder's Remote Control server). Seconds."""
    found = _local("POST", f"/api/sessions/{session}/connect", {}, timeout=120)
    if found is None:
        raise KeyError(session)
    return found


def start(cwd):
    """A new chat in that folder: {"session", ...}. Seconds."""
    made = _local("POST", "/api/sessions", {"cwd": cwd}, timeout=120)
    if made is None:
        raise RuntimeError("the sessions server can't start chats")
    return made


def folders():
    """Folders to offer for a new chat: KINDLE_FOLDERS, then those the sessions server has run chats in."""
    out = list(config.FOLDERS)
    if config.SESSIONS_URL:
        try:
            out += [f for f in (_local("GET", "/api/sessions/folders") or {}).get("folders") or [] if f not in out]
        except (RuntimeError, Refused):
            pass
    return out
