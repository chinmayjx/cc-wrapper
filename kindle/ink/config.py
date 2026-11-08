"""Settings, from the environment and kindle/.env (KEY=value lines; the environment wins). .env.example lists them.

Nothing personal is built in: hostnames, the Access team and audience, the artifact hub, the speech-to-text
machine all come from here. Secrets (speech-to-text keys, Claude's credentials) are read from files on this
machine when needed, never from the repo.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # kindle/
HOME = Path.home()


def _load_env(path):
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_env(Path(os.environ.get("KINDLE_ENV_FILE") or ROOT / ".env"))


def get(key, default=""):
    return os.environ.get(key, "").strip() or default


def path(key, default):
    return Path(os.path.expanduser(get(key, str(default))))


def listen(key, default):
    host, _, port = get(key, default).rpartition(":")
    return host or "127.0.0.1", int(port)


# The panel (the Kindle's pages), and the phone's mic WebSocket on a port of its own: the panel's port is the one
# published to the internet (KINDLE_ORIGIN), the mic's stays on your private network.
LISTEN = listen("KINDLE_LISTEN", "127.0.0.1:8790")
MIC_LISTEN = listen("KINDLE_MIC_LISTEN", "127.0.0.1:8792")
ORIGIN = get("KINDLE_ORIGIN").rstrip("/")  # https://kindle.example.com: POSTs from any other origin are refused

# Who may in. "access": Cloudflare Access signs every request (Cf-Access-Jwt-Assertion); only ACCESS_EMAIL gets
# in. "none": no check at all, for a panel only your own network reaches (or local testing).
AUTH = get("KINDLE_AUTH", "access")
ACCESS_TEAM = get("KINDLE_ACCESS_TEAM").rstrip("/")  # https://<team>.cloudflareaccess.com
ACCESS_AUD = get("KINDLE_ACCESS_AUD")
ACCESS_EMAIL = get("KINDLE_ACCESS_EMAIL")

# Claude's sign-in (the panel reads chats through Anthropic's sessions API as the signed-in Claude Code CLI).
CLAUDE_DIR = path("CLAUDE_CONFIG_DIR", HOME / ".claude")
CLAUDE_JSON = path("KINDLE_CLAUDE_JSON", HOME / ".claude.json")

# Optional: a server that starts Claude Code Remote Control chats on this machine and brings stopped ones back
# (devbox-hub's /api/sessions). Without it the panel reads, sends and stops, but "New chat" has no folders.
SESSIONS_URL = get("KINDLE_SESSIONS_URL").rstrip("/")
FOLDERS = [os.path.expanduser(f) for f in get("KINDLE_FOLDERS", "~/code").split(":") if f]
PINNED_FILE = path("KINDLE_PINNED_FILE", HOME / ".config" / "kindle" / "pinned-chats")

# Optional: an artifact hub (github.com/chinmayjx/artifact-hub) the panel reads artifacts from, and the public
# address its links use in chats (those open in the panel's own reader).
ARTIFACT_HUB_URL = get("KINDLE_ARTIFACT_HUB_URL").rstrip("/")
ARTIFACT_LINK = get("KINDLE_ARTIFACT_LINK").rstrip("/")

CACHE_DIR = path("KINDLE_CACHE_DIR", HOME / ".cache" / "kindle")
MERMAID_DIR = path("KINDLE_MERMAID_DIR", HOME / ".local" / "share" / "kindle" / "mermaid")

# Dictation: which engine writes the phone's audio down (elevenlabs, deepgram or whisper). KINDLE_STT_ENGINE_FILE,
# if set, names a file holding the engine, read for every piece, so switching needs no restart.
STT_ENGINE = get("KINDLE_STT_ENGINE", "elevenlabs")
STT_ENGINE_FILE = path("KINDLE_STT_ENGINE_FILE", "") if get("KINDLE_STT_ENGINE_FILE") else None
ELEVENLABS_KEY_FILE = path("KINDLE_ELEVENLABS_KEY_FILE", HOME / ".secrets" / "elevenlabs" / "api.env")
DEEPGRAM_KEY_FILE = path("KINDLE_DEEPGRAM_KEY_FILE", HOME / ".secrets" / "deepgram" / "api.env")
WHISPER_URL = get("KINDLE_WHISPER_URL").rstrip("/")  # stt/stt_server.py on a machine with a GPU
