"""Ink: a Claude Code panel for a Kindle's e-ink browser (made on a Paperwhite's "Experimental Browser").

Meant to be published through a Cloudflare Tunnel that this machine dials out to (cloudflared,
deploy/cloudflared-kindle.yml.example), so nothing listens on a public address. Cloudflare Access logs you in;
this server checks the token Access adds to every request (Cf-Access-Jwt-Assertion), so a wrong Access rule
still can't let anyone in (config.AUTH). It listens on its own port (KINDLE_LISTEN) and serves only these routes.

    GET  /                    the chats, newest first, with Claude's own status line for each
    GET  /c/<session>         one chat, paged like a book (ink_assets/chat.js)
    GET  /new                 folders to start a chat in
    GET  /screen              the browser's size and features, for tuning the layout
    GET  /api/chat/<session>?after=<n>   messages after #n (-1: the latest few), the chat's state
    GET  /api/chat/<session>?before=<n>  a page of earlier messages, before #n
    GET  /api/chats                      the most recent chats, for the switcher in a chat
    GET  /art/<artifact>[/v<n>]          a Markdown or text artifact, paged like a chat (others: a note)
    GET  /api/art/<artifact>[/v<n>]      the same as {"title", "html"}: a chat opens it over its messages
    GET  /api/arts                       the newest artifacts, for the artifact list in a chat
    GET  /artpg/<artifact>/<n>/<page>.png   page <page> of a PDF or office artifact's version n, as an image
    GET  /mmd/<hash>.png                 a mermaid diagram from a Markdown code block, drawn as an image
    POST /api/chat/<session>/send        {"text"}: a message into the chat, as if typed on the phone
    POST /api/chat/<session>/stop        stop what Claude is doing (the phone app's stop button)
    GET  /api/dictate                    the dictation in progress: recording, transcribing, done; its text
    POST /api/dictate/start|stop         record through the phone's mic (ink/dictate.py)
    POST /api/new             {"cwd"}: a new chat in that folder (the sessions server, ink/claude.py)

Chats are read from Anthropic's sessions API, the one claude.ai and the phone app read: the Claude Code
process of a Remote Control chat uploads every message as it happens. Messages arrive whole (a paragraph,
a tool call), not word by word. The Kindle browser is old: pages are plain HTML, the script is ES5, and
POSTs must carry an X-Ink header (a cross-site page can't add one without CORS, which this server never
allows).
"""

import html
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ink import claude, config, dictate

ASSETS = Path(__file__).with_name("assets")
CACHE_DIR = config.CACHE_DIR / "chats"

SESSION_ID = re.compile(r"session_[A-Za-z0-9]+")
KEEP = 400  # messages kept per chat; the Kindle never pages back further than this
OPENING = 40  # messages sent when a chat opens, and per "Earlier messages" tap
OVERLAP = 50  # events re-read each look: one stored late (sent mid-compaction) lands behind the cursor
CSS_VERSION = "33"


class Denied(Exception):
    pass


# ---- Cloudflare Access ---------------------------------------------------------------------------------

_jwks = None


def check_access(headers):
    """The email Cloudflare Access signed this request for. Raises Denied unless it's the configured one."""
    if config.AUTH == "none":
        return "anyone"
    if config.AUTH != "access":
        raise Denied(f"unknown KINDLE_AUTH {config.AUTH!r}")
    if not (config.ACCESS_TEAM and config.ACCESS_AUD and config.ACCESS_EMAIL):
        raise Denied("Cloudflare Access isn't configured (KINDLE_ACCESS_TEAM, _AUD, _EMAIL)")
    import jwt  # PyJWT (python3-jwt), with cryptography for RS256

    global _jwks
    token = headers.get("Cf-Access-Jwt-Assertion")
    if not token:
        raise Denied("no Access token")
    if _jwks is None:
        _jwks = jwt.PyJWKClient(config.ACCESS_TEAM + "/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=config.ACCESS_AUD, issuer=config.ACCESS_TEAM)
    except jwt.PyJWTError as e:
        raise Denied(f"bad Access token: {e}")
    if claims.get("email", "").lower() != config.ACCESS_EMAIL.lower():
        raise Denied("not the user")
    return claims["email"]


# ---- Rendering chat events -----------------------------------------------------------------------------

# Links open: artifact hub links in the panel's own reader (/art/...), other web links as they are. Only
# http(s); anything else (javascript: and the like) stays text.
ARTIFACT_LINK = re.compile(re.escape(config.ARTIFACT_LINK) + r"/a/([A-Za-z0-9_-]+)(?:/v(\d+))?/?") if config.ARTIFACT_LINK else None
BARE_URL = re.compile(r"https?://[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'\"]")


def _a(url, label):
    """A link for already-escaped url and label text."""
    art = ARTIFACT_LINK.fullmatch(url) if ARTIFACT_LINK else None
    if art:
        return f'<a href="/art/{art.group(1)}{"/v" + art.group(2) if art.group(2) else ""}">{label}</a>'
    if not re.match(r"https?://", url):
        return f"<u>{label}</u>"
    return f'<a href="{url.replace(chr(34), "&quot;")}">{label}</a>'


def _inline(text):
    """Escaped text with **bold**, `code`, [links](...) and bare https:// links."""
    t = html.escape(text, quote=False)
    kept = []  # finished HTML, put back at the end so later rules don't reach inside it

    def keep(piece):
        kept.append(piece)
        return f"\x00{len(kept) - 1}\x00"

    t = re.sub(r"`([^`\n]+)`", lambda m: keep(f"<code>{m.group(1)}</code>"), t)
    t = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", lambda m: keep(_a(m.group(2), m.group(1))), t)
    t = BARE_URL.sub(lambda m: keep(_a(m.group(0), m.group(0))), t)
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", t)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], t)


TABLE_COLS = 4  # wider tables don't fit the Kindle's column: one card per row instead


def _cells(line):
    """A table row's cells; \\| is a pipe inside a cell."""
    return [c.strip().replace("\x01", "|") for c in line.strip().replace("\\|", "\x01").strip("|").split("|")]


def _table(rows, header):
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    rows = [r + [""] * (cols - len(r)) for r in rows]
    head, body = (rows[0], rows[1:]) if header else (None, rows)
    if cols <= TABLE_COLS:
        th = "<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>" if head else ""
        tds = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
        return f'<table class="md">{th}{tds}</table>'
    cards = []
    for r in body:
        first = f"<b>{_inline(r[0])}</b>"
        rest = "".join(f"<br>{_inline(head[j]) + ': ' if head and head[j] else ''}{_inline(c)}"
                       for j, c in enumerate(r[1:], 1) if c)
        cards.append(f'<p class="rowcard">{first}{rest}</p>')
    return "".join(cards)


def markdown(text):
    """Enough Markdown for Claude's replies: paragraphs, bullets, numbered lists, headings, code blocks
    (mermaid ones drawn), tables (cards when too wide). Everything is escaped first."""
    out, para, items, kind = [], [], [], None
    lines = text.replace("\r", "").split("\n")

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()

    def flush_list():
        nonlocal kind
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{kind}>")
            items.clear()
        kind = None

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush_para(); flush_list()
            lang = s[3:].strip().lower()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            code = "\n".join(block)
            img = mermaid_img(code) if lang == "mermaid" else None
            if img:
                out.append(img)
            else:
                if lang == "mermaid":
                    out.append('<p class="note">This diagram couldn\'t be drawn; its code:</p>')
                out.append("<pre>" + html.escape(code, quote=False) + "</pre>")
        elif not s:
            flush_para(); flush_list()
        elif s.startswith("|"):
            flush_para(); flush_list()
            rows, header = [], False
            while i < len(lines) and lines[i].strip().startswith("|"):
                if re.fullmatch(r"[|\s:-]+", lines[i].strip()):
                    header = len(rows) == 1  # the separator under the first row makes it a header
                else:
                    rows.append(_cells(lines[i]))
                i += 1
            out.append(_table(rows, header))
            continue
        elif re.match(r"#{1,6}\s", s):
            flush_para(); flush_list()
            out.append("<p><b>" + _inline(s.lstrip("#").strip()) + "</b></p>")
        elif re.match(r"[-*]\s", s) or re.match(r"\d+[.)]\s", s):
            flush_para()
            k = "ul" if s[0] in "-*" else "ol"
            if kind and kind != k:
                flush_list()
            kind = k
            # Nested bullets are flattened, their text kept.
            items.append(re.sub(r"^([-*]|\d+[.)])\s+", "", s))
        elif items and line.startswith(" "):
            items[-1] += " " + s
        else:
            flush_list()
            para.append(s)
        i += 1
    flush_para(); flush_list()
    return "".join(out)


def _tool_line(block):
    name, inp = block.get("name", ""), block.get("input") or {}
    if name == "AskUserQuestion":
        return None
    what = inp.get("description") or inp.get("file_path") or inp.get("pattern") or inp.get("url") \
        or inp.get("query") or inp.get("command") or inp.get("prompt") or ""
    what = str(what).strip().split("\n")[0]
    name = name.replace("mcp__", "").split("__")[-1]
    return f'<div class="tool">&rsaquo; {html.escape(name)}{" &middot; " + html.escape(what[:90]) if what else ""}</div>'


def _ask(block):
    """AskUserQuestion: the questions and their options, answered in the reply box."""
    parts = []
    for q in (block.get("input") or {}).get("questions") or []:
        opts = "".join(f"<li>{_inline(str(o.get('label', '')))}</li>" for o in q.get("options") or [])
        parts.append(f"<p><b>Needs you:</b> {_inline(str(q.get('question', '')))}</p>" + (f"<ul>{opts}</ul>" if opts else ""))
    return '<div class="ask">' + "".join(parts) + "</div>" if parts else None


def render(event):
    """(who, html) for an event worth showing, else None. who: "you" | "claude". An event of a shape this
    doesn't know is skipped, never an error: one odd message mustn't make a whole chat unreadable."""
    try:
        return _render(event)
    except (AttributeError, TypeError, ValueError):
        return None


def _blocks(content):
    """Content as a list of blocks: a plain string inside the list counts as a text block."""
    if not isinstance(content, list):
        return []
    return [b if isinstance(b, dict) else {"type": "text", "text": b} for b in content if isinstance(b, (dict, str))]


def _render(event):
    kind, msg = event.get("type"), event.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    if event.get("parent_tool_use_id"):
        return None  # a sub-agent's own conversation: its prompt isn't the user speaking
    if kind == "user":
        if event.get("origin") or event.get("isSynthetic") or event.get("isReplay"):
            return None
        if isinstance(content, str):
            text, images = content, 0
        else:
            blocks = _blocks(content)
            if any(b.get("type") == "tool_result" for b in blocks):
                return None
            text = "\n\n".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
            images = sum(1 for b in blocks if b.get("type") == "image")
        if text.lstrip().startswith("<") or text.startswith("[Request interrupted"):
            return None
        body = markdown(text) + ("<p><i>[image]</i></p>" * images)
        return ("you", body) if body else None
    if kind == "assistant":
        parts = []
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else _blocks(content)
        for b in blocks:
            if b.get("type") == "text" and str(b.get("text") or "").strip():
                parts.append(markdown(str(b["text"])))
            elif b.get("type") == "tool_use":
                parts.append(_ask(b) if b.get("name") == "AskUserQuestion" else _tool_line(b))
        parts = [p for p in parts if p]
        return ("claude", "".join(parts)) if parts else None
    return None


# ---- Per-chat cache of rendered messages -----------------------------------------------------------------

_chats = {}  # session -> {"items": [{"n", "who", "html"}], "next": n, "last_id", "who", "checked"}
_locks = {}
_locks_lock = threading.Lock()


def _lock(session):
    with _locks_lock:
        return _locks.setdefault(session, threading.Lock())


def _load(session):
    if session not in _chats:
        try:
            _chats[session] = json.loads((CACHE_DIR / f"{session}.json").read_text())
        except (OSError, ValueError):
            _chats[session] = {"items": [], "next": 0, "last_id": None, "who": None, "checked": 0}
    return _chats[session]


def _store(session, chat):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f".{session}.tmp"
    tmp.write_text(json.dumps(chat))
    tmp.replace(CACHE_DIR / f"{session}.json")


def refresh(session, api=None):
    """Fetch the chat's events since the last look and render the new ones. The first look at a long chat
    reads all of it (1000 events a request, ~20 s for 9000); after that it's one request."""
    api = api or claude.api
    with _lock(session):
        chat = _load(session)
        recent = chat.setdefault("recent", [])  # ids of the last OVERLAP events read
        seen = set(recent)
        after, changed = (recent[0] if len(recent) >= OVERLAP else chat["last_id"]), False
        while True:
            page = api("GET", f"/v1/sessions/{session}/events?limit=1000" + (f"&after_id={after}" if after else ""))
            for e in page.get("data") or []:
                if e.get("uuid") in seen:
                    continue
                if e.get("uuid"):
                    seen.add(e["uuid"])
                    recent.append(e["uuid"])
                    changed = True
                shown = render(e)
                if not shown:
                    continue
                who, body = shown
                head = who != chat["who"]
                label = "You" if who == "you" else "Claude"
                chat["items"].append({"n": chat["next"], "who": who,
                                      "html": (f'<div class="who">{label}</div>' if head else "")
                                      + f'<div class="msg{" me" if who == "you" else ""}">{body}</div>'})
                chat["next"] += 1
                chat["who"] = who
            if page.get("data"):
                after = page.get("last_id") or after
            if not page.get("has_more") or not page.get("data"):
                break
        chat["last_id"] = after
        del recent[:-OVERLAP]
        if len(chat["items"]) > KEEP:
            chat["items"] = chat["items"][-KEEP:]
        if changed:
            _store(session, chat)
        return chat


def state(session, api=None):
    """{"title", "status", "needs"} from the session itself: Claude's own status for the chat."""
    s = (api or claude.api)("GET", f"/v1/sessions/{session}")
    summary = s.get("post_turn_summary") or (s.get("external_metadata") or {}).get("post_turn_summary") or {}
    return {"title": s.get("title") or "Chat", "status": s.get("status_bucket") or "",
            "needs": summary.get("needs_action") or "", "context": _context(s)}


def _tokens(n):
    return f"{n / 1e6:.3g}M" if n >= 1e6 else f"{round(n / 1e3)}k"


def _model(name):
    """"claude-opus-5-5[1m]" -> "Opus 5.5"."""
    words = re.sub(r"\[.*?\]|-\d{8}$", "", name).removeprefix("claude-").split("-")
    names = [w.capitalize() for w in words if not w.isdigit()]
    return " ".join(names + [".".join(w for w in words if w.isdigit())]).strip()


def _context(s):
    """The reply box's second line under the chat's name, a status line: "Opus 5.5 · medium · 71k/1M"."""
    ctx, meta = s.get("session_context") or {}, s.get("external_metadata") or {}
    usage = meta.get("context_usage") or {}
    used, most = usage.get("used_tokens"), usage.get("max_tokens")
    parts = [_model(ctx["model"]) if ctx.get("model") else "", ctx.get("effort_level") or "",
             f"{_tokens(used)}/{_tokens(most)}" if isinstance(used, int) and isinstance(most, int) and most > 0 else ""]
    return " · ".join(p for p in parts if p) or "Reply..."


def chat_update(session, after):
    """A chat opens with its last OPENING messages, not all KEEP: a smaller download and much less for the
    Kindle to lay out. "older" says whether earlier ones can be fetched (chat_before)."""
    chat = refresh(session)
    items = chat["items"] if after < 0 else [i for i in chat["items"] if i["n"] > after]
    if after >= 0 and items and items[0]["n"] > after + 1:
        items = chat["items"]  # fell behind the kept window: start over
        after = -1
    if after < 0:
        items = items[-OPENING:]
    return {"items": items, "next": chat["next"] - 1, "reset": after < 0, "older": _older(chat, items),
            **state(session)}


def chat_before(session, before):
    chat = _load(session) if session in _chats else refresh(session)
    items = [i for i in chat["items"] if i["n"] < before][-OPENING:]
    return {"items": items, "older": _older(chat, items)}


def _older(chat, items):
    return bool(items and chat["items"] and items[0]["n"] > chat["items"][0]["n"])


def send(session, text):
    """The message into the chat, as the phone app sends it. A chat of this machine's folders that stopped
    (reboot, idle) is brought back first, so the message has a Claude to go to."""
    try:
        found = claude.lookup(session)
        if found and not found["running"]:
            claude.connect(session)
    except (KeyError, claude.Refused, RuntimeError):
        pass  # no sessions server, or it couldn't: the message still goes to claude.ai
    claude.api("POST", f"/v1/sessions/{session}/events", {"events": [{
        "type": "user", "message": {"role": "user", "content": text}, "parent_tool_use_id": None,
        "session_id": session, "uuid": str(uuid.uuid4())}]},
        # Ingress stamps client_platform from this header, ignoring body values.
        headers={"anthropic-client-platform": "web_claude_ai"})


def stop(session):
    """Interrupt Claude's current turn, as the phone app's stop button does."""
    claude.api("POST", f"/v1/sessions/{session}/events", {"events": [{
        "type": "control_request", "request_id": str(uuid.uuid4()), "request": {"subtype": "interrupt"},
        "uuid": str(uuid.uuid4())}]})


# ---- Artifacts -------------------------------------------------------------------------------------------

DOC_KINDS = ("markdown", "text")
MAX_DOC = 2_000_000
# PDFs and office files (which the hub turns into a PDF) show as page images: grey, the width of the Kindle's
# text column, 16 shades (all e-ink shows; it keeps a page to tens of KB). Made once per version, kept.
PAGED_KINDS = ("pdf", "office")
PAGE_W = 718  # the Kindle's 758 px less the reader's margins
MAX_PAGES = 80
MAX_PDF = 60_000_000
PAGES_DIR = config.CACHE_DIR / "pages"
_rendering = {}  # one render per version at a time: the Kindle asks for all its pages at once
_rendering_lock = threading.Lock()


def _hub(path, limit=MAX_DOC):
    """The artifact hub (KINDLE_ARTIFACT_HUB_URL). None for a missing artifact, or when there's no hub."""
    if not config.ARTIFACT_HUB_URL:
        return None
    try:
        with urllib.request.urlopen(config.ARTIFACT_HUB_URL + path, timeout=60) as r:
            return r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"artifact hub said {e.code}")
    except OSError as e:
        raise RuntimeError(f"artifact hub unreachable: {e}")


def artifact_doc(aid, number=None):
    """An artifact's title and readable HTML (title line, then the document or a note). None if missing."""
    data = _hub(f"/api/artifacts/{aid}/versions/{number or 'latest'}")
    if data is None:
        return None
    v = json.loads(data)
    head = (f'<h1 class="title">{html.escape(v["title"])}</h1>'
            f'<p class="note">{html.escape(v["type"])} &middot; v{v["version"]}</p>')
    if v["kind"] in PAGED_KINDS:
        body = _pages_html(aid, v)
    elif v["kind"] not in DOC_KINDS:
        body = '<p class="note">Only Markdown, text, PDF and office artifacts open on the Kindle for now.</p>'
    else:
        doc = _hub(f"/a/{aid}/v{v['version']}/file")
        if doc is None or len(doc) > MAX_DOC:
            body = '<p class="note">Too big to open here.</p>'
        elif v["kind"] == "markdown":
            body = markdown(doc.decode(errors="replace"))
        else:
            body = f"<pre>{html.escape(doc.decode(errors='replace'), quote=False)}</pre>"
    return {"title": v["title"], "html": head + body}


def _pages_html(aid, v):
    pages = (v.get("pages") or [])[:MAX_PAGES]
    if not pages:
        return '<p class="note">Its pages aren\'t ready yet. Try again in a minute.</p>'
    more = len(v["pages"]) - len(pages)
    imgs = "".join(f'<img class="pg" src="/artpg/{aid}/{v["version"]}/{i}.png" width="{PAGE_W}" '
                   f'height="{round(PAGE_W * h / w)}" alt="Page {i}">' for i, (w, h) in enumerate(pages, 1))
    return imgs + (f'<p class="note">{more} more pages not shown.</p>' if more > 0 else "")


def page_png(aid, number, page):
    """Page `page` of an artifact version as a PNG file, rendering the whole version the first time."""
    if not 1 <= page <= MAX_PAGES:
        return None
    folder = PAGES_DIR / f"{aid}-v{number}"
    path = folder / f"{page}.png"
    if path.exists():
        return path
    with _rendering_lock:
        lock = _rendering.setdefault(folder.name, threading.Lock())
    with lock:
        if not (folder / "done").exists():
            _render_pages(aid, number, folder)
    return path if path.exists() else None


def _render_pages(aid, number, folder):
    data = _hub(f"/api/artifacts/{aid}/versions/{number}")
    if data is None:
        return
    v = json.loads(data)
    if v["kind"] not in PAGED_KINDS:
        return
    pdf = _hub(f"/a/{aid}/v{number}/{'file' if v['kind'] == 'pdf' else 'preview.pdf'}", MAX_PDF)
    if pdf is None or len(pdf) > MAX_PDF:
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "doc.pdf"
        src.write_bytes(pdf)
        subprocess.run(["pdftoppm", "-gray", "-png", "-scale-to-x", str(PAGE_W), "-scale-to-y", "-1",
                        "-f", "1", "-l", str(MAX_PAGES), str(src), str(Path(tmp) / "p")],
                       check=True, timeout=300, capture_output=True)
        out = Path(tmp) / "out"
        out.mkdir()
        for f in Path(tmp).glob("p-*.png"):
            img = Image.open(f).convert("L").point(lambda x: x // 17 * 17)  # 16 shades
            img.save(out / f"{int(f.stem.split('-')[-1])}.png", optimize=True)
        (out / "done").write_text("")
        folder.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(folder, ignore_errors=True)
        shutil.copytree(out, folder)


# ---- Mermaid diagrams -------------------------------------------------------------------------------------
# A ```mermaid block becomes an <img> at once; the diagram is drawn only when the Kindle asks for the image
# (several in parallel), so a page with many diagrams loads as fast as one without. Drawing is mermaid-cli
# (Node + the installed Chrome, about a second each; deploy/install.sh puts it in KINDLE_MERMAID_DIR) into a grey PNG
# no wider than the text column, kept by the code's hash. A block known not to draw shows as code; one that
# fails while the Kindle waits gets an image saying so.

MERMAID_DIR = config.MERMAID_DIR
MERMAID_CACHE = config.CACHE_DIR / "mermaid"
MAX_MERMAID = 20_000
_drawing = threading.BoundedSemaphore(4)  # Chromes at once


def mermaid_img(code):
    """The <img> for a mermaid diagram (drawn later, on request), or None if it can't be drawn."""
    import hashlib
    if not code.strip() or len(code) > MAX_MERMAID:
        return None
    key = hashlib.sha256(code.encode()).hexdigest()[:32]
    if (MERMAID_CACHE / f"{key}.err").exists():
        return None
    png = MERMAID_CACHE / f"{key}.png"
    if png.exists():
        from PIL import Image
        w, h = Image.open(png).size
        size = f' width="{w}" height="{h}"'
    else:
        MERMAID_CACHE.mkdir(parents=True, exist_ok=True)
        (MERMAID_CACHE / f"{key}.mmd").write_text(code)
        size = ""  # the reader measures again once it arrives
    return f'<img class="pg mmd" src="/mmd/{key}.png"{size} alt="Diagram">'


def mermaid_png(key):
    """The drawn diagram, drawing it now if needed; a note as an image if it can't be drawn; None if unknown."""
    png, src = MERMAID_CACHE / f"{key}.png", MERMAID_CACHE / f"{key}.mmd"
    if not png.exists() and src.exists() and not (MERMAID_CACHE / f"{key}.err").exists():
        with _drawing:
            if not png.exists():
                _draw_mermaid(src.read_text(), key)
    if png.exists():
        return png.read_bytes()
    if (MERMAID_CACHE / f"{key}.err").exists():
        return _note_png("This diagram couldn't be drawn: its code has a mistake.")
    return None


def _note_png(text):
    import io
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    img = Image.new("L", (PAGE_W, 48), 255)
    ImageDraw.Draw(img).text((8, 12), text, fill=0, font=font)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _draw_mermaid(code, key):
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "d.mmd", Path(tmp) / "d.png"
        src.write_text(code)
        try:
            subprocess.run([str(MERMAID_DIR / "node_modules" / ".bin" / "mmdc"), "-i", str(src), "-o", str(out),
                            "-p", str(MERMAID_DIR / "puppeteer.json"), "-b", "white", "-t", "neutral", "-s", "2",
                            "-q"], check=True, timeout=60, capture_output=True)
            img = Image.open(out).convert("L")
        except subprocess.CalledProcessError as e:
            (MERMAID_CACHE / f"{key}.err").write_text((e.stderr or b"").decode(errors="replace")[:2000])
            return
        except (OSError, subprocess.SubprocessError) as e:
            (MERMAID_CACHE / f"{key}.err").write_text(str(e)[:2000])
            return
    if img.width > PAGE_W:  # drawn at twice the size, so it stays sharp when brought down to the column
        img = img.resize((PAGE_W, round(img.height * PAGE_W / img.width)), Image.LANCZOS)
    img.point(lambda x: x // 17 * 17).save(MERMAID_CACHE / f"{key}.png", optimize=True)


ARTS = 40


def arts_json():
    """The artifact hub's newest artifacts, newest first."""
    data = _hub(f"/api/artifacts?limit={ARTS}")
    now = time.time()
    arts = []
    for a in json.loads(data or b"[]"):
        m = max(0, int(now - a["updated"]) // 60)
        ago = "now" if m < 1 else f"{m} min" if m < 60 else f"{m // 60} h" if m < 1440 else f"{m // 1440} d"
        arts.append({"id": a["artifact_id"], "title": a["title"], "label": a["label"], "ago": ago})
    return {"arts": arts}


def artifact_page(aid, number=None):
    doc = artifact_doc(aid, number)
    if doc is None:
        return None
    page = f'<div class="page" id="page"><div class="flow" id="flow"><div class="msg doc">{doc["html"]}</div></div></div>'
    return _page(doc["title"], page, f'<script src="/chat.js?v={CSS_VERSION}"></script>')


# ---- Pages -----------------------------------------------------------------------------------------------

LABELS = {"working": ("WORKING", ""), "need_input": ("NEEDS YOU", "wait"), "blocked": ("NEEDS YOU", "wait"),
          "failed": ("FAILED", "wait")}


def _ago(stamp):
    try:
        t = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (TypeError, ValueError):
        return ""
    m = max(0, int(time.time() - t) // 60)
    return "now" if m < 1 else f"{m} min ago" if m < 60 else f"{m // 60} h ago" if m < 1440 else f"{m // 1440} d ago"


def _page(title, body, script=""):
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="/ink.css?v={CSS_VERSION}">
</head><body>
{body}
{script}
</body></html>"""


PINNED = config.PINNED_FILE  # "session_… name" lines: chats kept in the list however old
SHOWN = 30
SWITCHER = 8  # chats in the switcher inside a chat


def _pinned():
    try:
        return [line.split()[0] for line in PINNED.read_text().splitlines() if line.startswith("session_")]
    except OSError:
        return []


def _recent():
    """Chats, most recently active first. The API lists chats newest-created first (100 at most a page); a
    long-running pinned chat can be older than that, so pinned chats are fetched by id
    when missing."""
    data = claude.api("GET", "/v1/sessions?limit=100").get("data") or []
    have = {s["id"] for s in data}
    for p in _pinned():
        if p not in have:
            try:
                data.append(claude.api("GET", f"/v1/sessions/{p}"))
            except RuntimeError:
                pass
    data = [s for s in data if s.get("session_status") != "archived"]
    data.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return data


def _label(s):
    summary = s.get("post_turn_summary") or (s.get("external_metadata") or {}).get("post_turn_summary") or {}
    return LABELS.get(s.get("status_bucket") or summary.get("status_category") or "", ("", ""))


def chats_json(n=SWITCHER):
    """The switcher's list: id, title and a WORKING / NEEDS YOU label."""
    return {"chats": [{"id": s["id"], "title": s.get("title") or "Chat", "label": _label(s)[0]}
                      for s in _recent()[:n]]}


def list_page():
    data = _recent()[:SHOWN]
    rows = ['<a class="chat new" href="/new"><div class="name">+ New chat</div></a>']
    for s in data:
        summary = s.get("post_turn_summary") or (s.get("external_metadata") or {}).get("post_turn_summary") or {}
        label, cls = _label(s)
        line = summary.get("needs_action") or summary.get("recent_action") or summary.get("status_detail") \
            or ("Claude is working" if s.get("status_bucket") == "working" else "")
        rows.append(f"""<a class="chat" href="/c/{s['id']}">
  {f'<span class="state {cls}">{label}</span>' if label else ''}
  <div class="name">{html.escape(s.get('title') or 'Chat')}</div>
  <div class="last">{html.escape(line)}</div>
  <div class="meta">{_ago(s.get('updated_at'))}</div>
</a>""")
    rows.append('<a class="chat" href="/screen"><div class="meta">Screen size check</div></a>')
    return _page("Chats", '<div class="list">' + "\n".join(rows) + "</div>")


def _folders():
    """Folders a new chat can start in (none without a sessions server to start it)."""
    if not config.SESSIONS_URL:
        return []
    return [f for f in claude.folders() if "/.rc2/" not in f and Path(f).is_dir()]


def new_page():
    rows = ['<div class="chat"><div class="who">Start a chat in</div></div>']
    if not config.SESSIONS_URL:
        rows.append('<div class="chat"><div class="last">Starting chats needs a sessions server (KINDLE_SESSIONS_URL).</div></div>')
    for f in _folders():
        short = f.replace(str(Path.home()), "~", 1)
        rows.append(f'<a class="chat" href="#" onclick="return start(this)" data-cwd="{html.escape(f)}">'
                    f'<div class="name">{html.escape(short)}</div></a>')
    script = """<script>
function start(a) {
  if (window.busy) return false;
  window.busy = true;
  a.innerHTML = '<div class="name">Starting&hellip;</div>';
  var x = new XMLHttpRequest();
  x.open('POST', '/api/new', true);
  x.setRequestHeader('Content-Type', 'application/json');
  x.setRequestHeader('X-Ink', '1');
  x.onreadystatechange = function () {
    if (x.readyState !== 4) return;
    window.busy = false;
    if (x.status === 200) location.href = '/c/' + JSON.parse(x.responseText).session;
    else a.innerHTML = '<div class="name">Failed: ' + x.status + '</div>';
  };
  x.send(JSON.stringify({cwd: a.getAttribute('data-cwd')}));
  return false;
}
</script>"""
    return _page("New chat", '<div class="list">' + "\n".join(rows) + "</div>", script)


def chat_page(session):
    body = """<div class="split" id="split"><div class="bar"><button id="artback">&#8249; Back</button><button id="artx">&#215;</button><span id="arttitle"></span></div>
<div class="artpage" id="artpage"><div class="flow" id="artflow"></div></div></div>
<div class="page" id="page"><div class="flow" id="flow"><p class="note">Loading&hellip;</p></div></div>
<div class="switcher" id="switcher"></div>
<div class="reply" id="reply"><table><tr>
  <td class="box"><textarea id="text" rows="1"></textarea><div id="hint"><b id="hintname">Loading&hellip;</b><span id="hintctx"></span></div><button id="menub">&#9776;</button><button id="artb"><span class="docicon"></span></button></td>
  <td class="mic"><button id="micb">Mic</button></td>
  <td class="send"><button id="sendb">Send</button></td>
</tr></table></div>"""
    script = f'<script>var SESSION = "{session}";</script>\n<script src="/chat.js?v={CSS_VERSION}"></script>'
    return _page("Chat", body, script)


# ---- HTTP ------------------------------------------------------------------------------------------------

class InkHandler(BaseHTTPRequestHandler):
    server_version = "ink"

    def _allowed(self):
        try:
            check_access(self.headers)
            return True
        except (Denied, OSError, ValueError, KeyError) as e:
            self.log_message("denied: %s", e)
            self.send_error(HTTPStatus.FORBIDDEN)
            return False

    def do_GET(self):
        if not self._allowed():
            return
        url = urlparse(self.path)
        path = unquote(url.path)
        try:
            if path == "/":
                return self.send_html(list_page())
            if path == "/new":
                return self.send_html(new_page())
            if path == "/screen":
                return self.send_html((ASSETS / "screen.html").read_text())
            if path in ("/ink.css", "/chat.js"):
                ctype = "text/css" if path.endswith(".css") else "application/javascript"
                return self.send_bytes((ASSETS / path[1:]).read_bytes(), ctype, {"Cache-Control": "max-age=86400"})
            m = re.fullmatch(r"/c/(session_[A-Za-z0-9]+)", path)
            if m:
                return self.send_html(chat_page(m.group(1)))
            m = re.fullmatch(r"/art/([A-Za-z0-9_-]+)(?:/v(\d+))?", path)
            if m:
                page = artifact_page(m.group(1), m.group(2))
                if page:
                    return self.send_html(page)
            if path == "/api/dictate":
                return self.send_json(dictate.state())
            m = re.fullmatch(r"/api/chat/(session_[A-Za-z0-9]+)", path)
            if m:
                query = parse_qs(url.query)
                if query.get("before"):
                    return self.send_json(chat_before(m.group(1), int(query["before"][0])))
                return self.send_json(chat_update(m.group(1), int((query.get("after") or ["-1"])[0])))
            if path == "/api/chats":
                return self.send_json(chats_json())
            if path == "/api/arts":
                return self.send_json(arts_json())
            m = re.fullmatch(r"/mmd/([0-9a-f]{32})\.png", path)
            if m:
                png = mermaid_png(m.group(1))
                if png:
                    return self.send_bytes(png, "image/png", {"Cache-Control": "max-age=31536000"})
            m = re.fullmatch(r"/artpg/([A-Za-z0-9_-]+)/(\d+)/(\d+)\.png", path)
            if m:
                png = page_png(m.group(1), int(m.group(2)), int(m.group(3)))
                if png:
                    return self.send_bytes(png.read_bytes(), "image/png", {"Cache-Control": "max-age=31536000"})
            m = re.fullmatch(r"/api/art/([A-Za-z0-9_-]+)(?:/v(\d+))?", path)
            if m:
                doc = artifact_doc(m.group(1), m.group(2))
                if doc:
                    return self.send_json(doc)
        except RuntimeError as e:  # claude.ai unreachable or refused
            return self.send_text(HTTPStatus.BAD_GATEWAY, str(e))
        return self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self._allowed():
            return
        # Only the panel's own pages can POST: they add X-Ink, which a cross-site form can't, and a
        # cross-site script would need CORS, which this server never allows.
        origin = self.headers.get("Origin")
        if self.headers.get("X-Ink") != "1" or (origin and origin != config.ORIGIN and config.AUTH != "none"):
            return self.send_error(HTTPStatus.FORBIDDEN)
        path = urlparse(self.path).path
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 100_000)))
            m = re.fullmatch(r"/api/chat/(session_[A-Za-z0-9]+)/send", path)
            if m:
                text = str(body["text"]).strip()
                if not text:
                    raise ValueError
                send(m.group(1), text)
                return self.send_json({"ok": True})
            m = re.fullmatch(r"/api/chat/(session_[A-Za-z0-9]+)/stop", path)
            if m:
                stop(m.group(1))
                return self.send_json({"ok": True})
            if path == "/api/dictate/start":
                return self.send_json(dictate.start())
            if path == "/api/dictate/stop":
                return self.send_json(dictate.stop())
            if path == "/api/new":
                cwd = str(body["cwd"])
                if cwd not in _folders():
                    raise ValueError
                return self.send_json(claude.start(cwd))
        except (ValueError, KeyError, TypeError):
            return self.send_error(HTTPStatus.BAD_REQUEST)
        except claude.Refused:
            return self.send_error(HTTPStatus.FORBIDDEN)
        except dictate.Failed as e:
            self.log_message("dictation: %s", e)
            return self.send_text(HTTPStatus.CONFLICT, str(e))
        except RuntimeError as e:
            return self.send_text(HTTPStatus.BAD_GATEWAY, str(e))
        return self.send_error(HTTPStatus.NOT_FOUND)

    def send_json(self, data):
        self.send_bytes(json.dumps(data).encode(), "application/json", {"Cache-Control": "no-store"})

    def send_html(self, text):
        self.send_bytes(text.encode(), "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    def send_text(self, status, text):
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data, ctype, headers=None):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        import sys
        sys.stderr.write("ink %s\n" % (fmt % args))


def serve(listen=None):
    from http.server import ThreadingHTTPServer
    ThreadingHTTPServer(listen or config.LISTEN, InkHandler).serve_forever()
