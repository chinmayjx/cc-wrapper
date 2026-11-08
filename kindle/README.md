# Ink: Claude Code on a Kindle

A small web frontend for Claude Code, made for the e-ink browser of a Kindle (built on a Paperwhite's
"Experimental Browser": old WebKit, ES5, no animation). It shows your Claude Code chats with Claude's own status
for each (working, needs you), one chat paged like a book, and lets you send a message, stop Claude, or start a
new chat in one of your folders. Artifacts from an [artifact hub](https://github.com/chinmayjx/artifact-hub)
(Markdown, PDF, office files) open as pages, and Mermaid diagrams in Claude's replies are drawn as images.

The Kindle has no microphone, so its **Mic** button records through your phone: with **Kindle dictation** on in
the Claude Code app's settings tray (this repo's app, see the main README), the phone keeps a WebSocket open to
this server, records when the Kindle asks, and the server sends the audio to a speech-to-text service
(ElevenLabs Scribe, Deepgram, or Whisper on your own GPU) and puts the text in the reply box.

It reads chats through Anthropic's sessions API, the one claude.ai and the phone app read, as your signed-in
Claude Code CLI: so it shows the chats you run with Claude Code's **Remote Control** (`claude remote-control`).
It's Python with the standard library, plus PyJWT (Cloudflare Access tokens) and Pillow (images).

## Layout

| Path | What |
| --- | --- |
| `ink/panel.py` | The panel: its pages and API (the routes are listed at the top) |
| `ink/assets/` | Its script (ES5), stylesheet and the screen-size check page |
| `ink/claude.py` | Anthropic's sessions API, and the optional sessions server for new chats |
| `ink/dictate.py` | Dictation: pieces cut at pauses, sent to the speech-to-text engine |
| `ink/mic.py`, `ink/websocket.py` | The phone's mic: the `/api/mic` WebSocket and its protocol |
| `ink/config.py` | Settings (from `.env`) |
| `stt/` | Optional Whisper server for a machine with a GPU |
| `deploy/` | systemd units, `install.sh`, the optional Cloudflare Tunnel and Whisper server |
| `tests/` | `python3 -m unittest` (from `kindle/`) |

## Setup

1. Claude Code signed in on this machine (`~/.claude/.credentials.json` and `~/.claude.json`), running your chats
   through Remote Control.
2. `cp .env.example .env` and fill it in (below). Secrets never go in `.env`: the speech-to-text keys stay in their
   own files (`~/.secrets/...` by default) and Claude's credentials are read from `~/.claude`.
3. `deploy/install.sh` (add `--mermaid` for diagrams): installs PyJWT, Pillow and poppler-utils with apt, and the
   `kindle` service from `deploy/kindle.service`, running `python3 -m ink` from this checkout as you.
   By hand: `cd kindle && python3 -m ink`.
4. Publish the panel's port to the Kindle (next section), and route the phone's mic to the mic port.
5. On the Kindle, open the panel's address and log in.

Run a second copy for testing on another port with `KINDLE_AUTH=none python3 -m ink --port 8791` (panel only,
no mic).

### Publishing it: Cloudflare Tunnel and Access (optional)

The Kindle's browser has to reach the panel from wherever it is. One way, with nothing listening on a public
address: a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
that this machine dials out to, with [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
in front of the hostname (a one-time PIN to your email works on the Kindle).

1. `cloudflared tunnel create kindle`, `cloudflared tunnel route dns kindle kindle.example.com`.
2. Copy `deploy/cloudflared-kindle.yml.example` to `/etc/cloudflared-kindle/config.yml` with your tunnel's id and
   hostname, its credentials file next to it, and install `deploy/cloudflared-kindle.service`.
3. In Zero Trust, add a self-hosted Access application for the hostname with a policy that lets only you in.
4. Set `KINDLE_ACCESS_TEAM`, `KINDLE_ACCESS_AUD` (the application's audience tag), `KINDLE_ACCESS_EMAIL` and
   `KINDLE_ORIGIN` in `.env`. The panel checks Access's signed token on every request itself, so a wrong Access
   rule still can't let anyone else in; without these settings it refuses everything.

Only the panel's port goes through the tunnel; the mic port does not.

### The phone's mic

The Claude Code app connects its mic to `<HUB_URL>/api/mic`, where `HUB_URL` is the server the app was built
with (the one it registers for push with). This server takes that WebSocket on its mic port (`KINDLE_MIC_LISTEN`,
127.0.0.1:8792), which should be reachable only from your private network. If `HUB_URL` is another server of
yours behind `tailscale serve`, add a path for it on that port:

```sh
sudo tailscale serve --bg --https=<HUB_URL's port> --set-path /api/mic http://127.0.0.1:8792/api/mic
```

Any reverse proxy that passes WebSocket upgrades on that path does the same. The protocol is in `ink/mic.py`
and the main README.

### Speech to text

`KINDLE_STT_ENGINE` picks it: `elevenlabs` (Scribe; `ELEVENLABS_API_KEY=` in `~/.secrets/elevenlabs/api.env`),
`deepgram` (Nova-3; `DEEPGRAM_API_KEY=` in `~/.secrets/deepgram/api.env`) or `whisper` (`KINDLE_WHISPER_URL`).
The keys may also be environment variables. `KINDLE_STT_ENGINE_FILE` names a file holding the engine instead,
read for every piece, so switching needs no restart.

For Whisper on your own NVIDIA GPU: `deploy/stt-server.sh user@gpu-machine` clones this repo there, installs
faster-whisper in `~/.venvs/whisper` and runs `stt/stt_server.py` on the machine's Tailscale address, port 8793
(`deploy/kindle-stt.service`). Then `KINDLE_WHISPER_URL=http://<that address>:8793`. When another engine returns
nothing for a piece with speech in it, the panel tries Whisper (if set), else Deepgram, once more.

## Config

All in `kindle/.env` (`.env.example` has each with its default); the environment overrides it.

| Key | What | Default |
| --- | --- | --- |
| `KINDLE_LISTEN` | The panel's address | `127.0.0.1:8790` |
| `KINDLE_MIC_LISTEN` | The phone mic's WebSocket | `127.0.0.1:8792` |
| `KINDLE_ORIGIN` | The panel's public URL; POSTs from other origins are refused | none |
| `KINDLE_AUTH` | `access` (Cloudflare Access) or `none` | `access` |
| `KINDLE_ACCESS_TEAM` | `https://<team>.cloudflareaccess.com` | none |
| `KINDLE_ACCESS_AUD` | The Access application's audience tag | none |
| `KINDLE_ACCESS_EMAIL` | The one email let in | none |
| `KINDLE_SESSIONS_URL` | A sessions server for new chats and reconnecting stopped ones | none (no New chat) |
| `KINDLE_FOLDERS` | Folders always offered for a new chat, `:`-separated | `~/code` |
| `KINDLE_PINNED_FILE` | `session_... name` lines: chats always listed | `~/.config/kindle/pinned-chats` |
| `KINDLE_ARTIFACT_HUB_URL` | The artifact hub's API | none (no artifacts) |
| `KINDLE_ARTIFACT_LINK` | The artifact hub's public link prefix, opened in the panel | none |
| `KINDLE_STT_ENGINE` | `elevenlabs`, `deepgram` or `whisper` | `elevenlabs` |
| `KINDLE_STT_ENGINE_FILE` | A file naming the engine, read per piece | none |
| `KINDLE_ELEVENLABS_KEY_FILE` | File with `ELEVENLABS_API_KEY=` | `~/.secrets/elevenlabs/api.env` |
| `KINDLE_DEEPGRAM_KEY_FILE` | File with `DEEPGRAM_API_KEY=` | `~/.secrets/deepgram/api.env` |
| `KINDLE_WHISPER_URL` | `stt/stt_server.py` | none |
| `CLAUDE_CONFIG_DIR`, `KINDLE_CLAUDE_JSON` | Claude Code's sign-in | `~/.claude`, `~/.claude.json` |
| `KINDLE_CACHE_DIR` | Rendered chats, PDF pages, diagrams, the last dictation | `~/.cache/kindle` |
| `KINDLE_MERMAID_DIR` | mermaid-cli (`install.sh --mermaid`) | `~/.local/share/kindle/mermaid` |

### The sessions server

Reading, sending and stopping need only Claude's sign-in. Starting a chat, and bringing back a chat whose Claude
stopped (reboot, idle) before a message goes to it, need something on the machine that runs
`claude remote-control` per folder. The panel asks `KINDLE_SESSIONS_URL` for that:

| Request | Answer |
| --- | --- |
| `GET /api/sessions/<id>` | `{"session", "cwd", "running"}`, or 404 if it isn't one of this machine's chats |
| `GET /api/sessions/folders` | `{"folders": [...]}`: folders it has run chats in |
| `POST /api/sessions` `{"cwd"}` | a new chat there, `{"session", ...}`; 403 for a folder it won't use; 502 `{"error"}` |
| `POST /api/sessions/<id>/connect` `{}` | bring the chat back; 404 if unknown |
