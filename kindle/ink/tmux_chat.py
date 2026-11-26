"""Interactive Claude Code sessions in tmux with Remote Control enabled (`claude --remote-control`).

Messages posted to Anthropic's Sessions API with the CLI's OAuth token arrive labelled as "Another Claude session"
(peer messages). Running Claude interactively in tmux with `--remote-control` keeps the chat synced with claude.ai,
the phone app and the Kindle reader, while messages sent into the tmux pane arrive as normal local user input.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ink import config

SESSION_ID = re.compile(r"session_[A-Za-z0-9]+")
_pane_locks = {}
_pane_locks_lock = threading.Lock()
_state_lock = threading.Lock()


def _claude_bin():
    configured = config.get("KINDLE_CLAUDE_BIN")
    if configured:
        return configured
    local = Path.home() / ".local" / "bin" / "claude"
    if local.exists():
        return str(local)
    return shutil.which("claude") or "claude"


def _tmux(*args, input_bytes=None, check=False, timeout=15):
    return subprocess.run(
        ["tmux", *args],
        input=input_bytes,
        capture_output=True,
        check=check,
        timeout=timeout,
    )


def _alive(pid, proc_start=None):
    """Whether `pid` is still running (and matches `proc_start` when recorded)."""
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    if proc_start is None:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19] == str(proc_start)
    except (OSError, IndexError, ValueError):
        return False


def _pane_target(raw_tmux):
    """Extract the globally unique `%N` pane target from Claude's `session:@win.%pane` string."""
    if not raw_tmux:
        return None
    m = re.search(r"%\d+$", str(raw_tmux))
    return m.group(0) if m else str(raw_tmux)


def _pane_alive(target):
    if not target:
        return False
    r = _tmux("display-message", "-p", "-t", target, "#{pane_dead}")
    return r.returncode == 0 and r.stdout.strip() == b"0"


def _state_file():
    return config.CACHE_DIR / "tmux-sessions.json"


def _load_state():
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}


def _save_state(data):
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def _remember(bridge_session_id, cwd, local_uuid):
    if not bridge_session_id or not local_uuid:
        return
    with _state_lock:
        data = _load_state()
        data[bridge_session_id] = {"cwd": str(cwd), "uuid": str(local_uuid)}
        _save_state(data)


def _known(bridge_session_id):
    with _state_lock:
        return _load_state().get(bridge_session_id)


def find_pane(session):
    """Return the live tmux pane target (`%N`) for an interactive CLI serving `session`, or None."""
    sessions_dir = config.CLAUDE_DIR / "sessions"
    if not sessions_dir.is_dir():
        return None
    for path in sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get("bridgeSessionId") != session:
            continue
        if data.get("entrypoint") != "cli":
            continue
        if not _alive(data.get("pid"), data.get("procStart")):
            continue
        target = _pane_target(data.get("tmux"))
        if target and _pane_alive(target):
            if data.get("cwd") and data.get("sessionId"):
                _remember(session, data["cwd"], data["sessionId"])
            return target
    return None


def _pane_lock(target):
    with _pane_locks_lock:
        return _pane_locks.setdefault(target, threading.Lock())


def send_to_pane(target, text):
    """Paste `text` into tmux pane `target` using bracketed paste, then press Enter."""
    buf = f"ink-{secrets.token_hex(4)}"
    with _pane_lock(target):
        load = _tmux("load-buffer", "-b", buf, "-", input_bytes=text.encode("utf-8"))
        if load.returncode != 0:
            raise RuntimeError(f"tmux load-buffer failed: {load.stderr.decode(errors='replace').strip()}")
        paste = _tmux("paste-buffer", "-p", "-d", "-b", buf, "-t", target)
        if paste.returncode != 0:
            _tmux("delete-buffer", "-b", buf)
            raise RuntimeError(f"tmux paste-buffer failed: {paste.stderr.decode(errors='replace').strip()}")
        # Give Claude's TUI a moment to process the bracketed paste before submitting.
        time.sleep(0.15)
        enter = _tmux("send-keys", "-t", target, "Enter")
        if enter.returncode != 0:
            raise RuntimeError(f"tmux send-keys failed: {enter.stderr.decode(errors='replace').strip()}")


def stop_pane(target):
    """Interrupt Claude's current turn in tmux pane `target`."""
    r = _tmux("send-keys", "-t", target, "Escape")
    return r.returncode == 0


def _trust(cwd):
    """Record folder trust in ~/.claude.json so Claude doesn't block on the interactive trust prompt."""
    git = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    folder = git.stdout.strip() if git.returncode == 0 and git.stdout.strip() else str(cwd)
    path = config.CLAUDE_JSON
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    project = data.setdefault("projects", {}).setdefault(folder, {})
    if project.get("hasTrustDialogAccepted"):
        return
    project["hasTrustDialogAccepted"] = True
    tmp = path.with_name(path.name + ".ink-tmp")
    tmp.write_text(json.dumps(data, indent=2))
    try:
        tmp.chmod(path.stat().st_mode & 0o777)
    except OSError:
        pass
    tmp.replace(path)


def _launch_and_wait(cwd, args, timeout=35):
    """Start Claude in a detached tmux session and wait until its Remote Control session ID is registered."""
    _trust(cwd)
    name = "ink-" + secrets.token_hex(4)
    cmd = [_claude_bin(), *args]
    _tmux(
        "new-session", "-d", "-s", name, "-x", "160", "-y", "45", "-c", str(cwd), *cmd,
        ";", "set-option", "-w", "-t", "=" + name + ":", "remain-on-exit", "on",
        check=True,
    )
    pane_info = _tmux("list-panes", "-t", "=" + name + ":", "-F", "#{pane_pid} #{pane_id}").stdout.decode().strip()
    if not pane_info:
        _tmux("kill-session", "-t", "=" + name)
        raise RuntimeError("tmux pane did not start")
    pane_pid, pane_id = pane_info.split()[:2]
    session_file = config.CLAUDE_DIR / "sessions" / f"{pane_pid}.json"
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text())
            except (OSError, ValueError):
                data = {}
            sid = data.get("bridgeSessionId")
            local_uuid = data.get("sessionId")
            if sid and SESSION_ID.fullmatch(sid) and _pane_alive(pane_id):
                _tmux("set-option", "-w", "-t", "=" + name + ":", "remain-on-exit", "off")
                _remember(sid, cwd, local_uuid)
                # Give the interactive prompt a brief moment to finish rendering.
                time.sleep(0.5)
                return {"session": sid, "cwd": str(cwd), "url": f"https://claude.ai/code/{sid}",
                        "running": True, "pane": pane_id}
        if _tmux("display-message", "-p", "-t", "=" + name + ":", "#{pane_dead}").stdout.strip() == b"1":
            out = _tmux("capture-pane", "-p", "-J", "-S", "-30", "-t", "=" + name + ":").stdout.decode(errors="replace")
            _tmux("kill-session", "-t", "=" + name)
            err = " ".join(out.partition("Error:")[2].partition("Pane is dead")[0].split())
            raise RuntimeError(err or "Claude exited before Remote Control came up")
        time.sleep(0.3)
    _tmux("kill-session", "-t", "=" + name)
    raise RuntimeError("Claude's Remote Control session didn't come up in time")


def start(cwd):
    """Start a new interactive Claude session with Remote Control in `cwd` inside tmux."""
    return _launch_and_wait(cwd, ["--remote-control"])


def resume(session):
    """Bring back a previously known tmux chat with `--resume <uuid> --remote-control`. Returns pane target or None."""
    info = _known(session)
    if not info or not Path(info["cwd"]).is_dir():
        return None
    started = _launch_and_wait(info["cwd"], ["--resume", info["uuid"], "--remote-control"])
    return started["pane"] if started.get("session") == session else None
