// One chat (or an artifact: no reply box, no SESSION) on the Kindle: pages like a book (tap the left third for back, anywhere else for next; swiping
// scrolls too), new messages polled from the hub, a reply box that grows. ES5 only: the Kindle browser is old.
(function () {
  var LINE = 32;
  var KEEP = 3; // lines of the previous page kept at the top of the next one, so you don't lose your place
  var page = document.getElementById('page');
  var flow = document.getElementById('flow');
  var reply = document.getElementById('reply');
  var text = document.getElementById('text');
  var sendb = document.getElementById('sendb');
  var micb = document.getElementById('micb');
  var menub = document.getElementById('menub');
  var switcher = document.getElementById('switcher');
  // The artifact hub over the chat area, so you can read an artifact while you dictate under it.
  // art: '' hidden, 'home' the list, else the artifact open ('<id>' or '<id>/v<n>').
  var split = document.getElementById('split');
  var artpage = document.getElementById('artpage');
  var artflow = document.getElementById('artflow');
  var arttitle = document.getElementById('arttitle');
  var artb = document.getElementById('artb');
  // In the empty reply box: which chat this is (so a message doesn't go to the wrong one), and its model line.
  var hint = document.getElementById('hint');
  var art = '', artLast = 'home', artH = 0, artStep = 0, artEnd = 0;
  var BAR = 42; // the hub's top bar with its border
  var h = 0, step = 0, end = 0;
  var last = -1, status = '', sending = false, timer = null;
  var offline = false; // the last check failed, and the toast said so
  var inflight = false, again = false; // one check at a time: two at once would both add the same messages
  var statusEl = null, statusMsg = '';
  var queued = []; // sent messages shown greyed at the bottom until the chat itself brings them back
  // The chat on screen. The switcher swaps it in place: gen tells a late answer for the previous chat to go.
  var chat = typeof SESSION === 'string' ? SESSION : '', gen = 0;
  var first = -1, olderEl = null; // the first message shown; the "Earlier messages" button above it
  // Battery: the Kindle sleeps its Wi-Fi only between requests, and redraws e-ink on any DOM change. So the
  // page is touched only when something changed, checks slow down as the chat goes quiet, and stop after
  // PAUSE_MS without a tap until you touch the screen again.
  var PAUSE_MS = 10 * 60 * 1000;
  var changed = new Date().getTime(), touched = changed, paused = false;
  var doc = !reply; // a document pages from its start; a chat from its newest message

  function layout(follow) {
    var bottom = reply ? reply.offsetHeight : 0;
    if (art) {
      // The hub covers the whole chat area; the chat underneath stays laid out but unpainted, so its updates
      // don't redraw the e-ink.
      artH = Math.floor((window.innerHeight - bottom - BAR) / LINE) * LINE; // whole lines under the bar
      artpage.style.height = artH + 'px';
      artStep = Math.max(LINE, artH - KEEP * LINE);
      artEnd = Math.max(0, artflow.offsetHeight - artH);
    }
    if (split) page.style.visibility = art ? 'hidden' : '';
    var avail = window.innerHeight - bottom;
    var atEnd = page.scrollTop >= end - 2;
    h = Math.floor(avail / LINE) * LINE; // whole lines only
    page.style.height = h + 'px';
    step = Math.max(LINE, h - KEEP * LINE);
    end = Math.max(0, flow.offsetHeight - h);
    // Where you are is kept: the hub polls and relays out often, and must never move the page under you.
    // A chat follows new messages only if you're already at the bottom.
    if (follow === false && (doc || !atEnd)) page.scrollTop = Math.min(page.scrollTop, end);
    else page.scrollTop = doc ? 0 : end;
  }

  // A tap moves one page from wherever you are (swiping can leave you between pages), keeping KEEP lines.
  function turn(dir) {
    page.scrollTop = Math.max(0, Math.min(end, page.scrollTop + dir * step));
  }

  page.onclick = function (e) {
    e = e || window.event;
    var t = e.target || e.srcElement;
    if (t.tagName === 'A' && split && artLink(t)) return false;
    if (t.tagName === 'BUTTON' || t.tagName === 'A') return;
    turn(e.clientX < window.innerWidth * 0.3 ? -1 : 1);
  };

  // An artifact link opens in the hub instead of leaving the chat.
  function artLink(a) {
    var m = /^\/art\/([A-Za-z0-9_-]+(\/v\d+)?)$/.exec(a.getAttribute('href') || '');
    if (!m) return false;
    showArt(m[1]);
    return true;
  }
  function remember() {
    try { if (art) localStorage.setItem('art', art); else localStorage.removeItem('art'); } catch (e) {}
  }
  function title(t) {
    arttitle.innerHTML = '';
    arttitle.appendChild(document.createTextNode(t));
  }
  function showArt(view) {
    art = artLast = view;
    remember();
    title(view === 'home' ? 'Artifacts' : '');
    split.className = view === 'home' ? 'split home' : 'split'; // the list has only × (Back would just hide)
    artflow.innerHTML = '<p class="note">Loading&hellip;</p>';
    split.style.display = 'block';
    artpage.scrollTop = 0;
    if (reply) label();
    layout(false);
    request('GET', view === 'home' ? '/api/arts' : '/api/art/' + view, null, function (code, body) {
      if (art !== view) return; // moved on meanwhile
      if (code !== 200) {
        artflow.innerHTML = '<p class="note">Couldn\'t load (' + code + ').</p>';
      } else if (view === 'home') {
        artflow.innerHTML = '';
        var list = JSON.parse(body).arts;
        for (var i = 0; i < list.length; i++) artflow.appendChild(artRow(list[i]));
        if (!list.length) artflow.innerHTML = '<p class="note">No artifacts yet.</p>';
      } else {
        var d = JSON.parse(body);
        title(d.title);
        artflow.innerHTML = '<div class="msg doc">' + d.html + '</div>';
      }
      layout(false);
    });
  }
  function artRow(a) {
    var r = document.createElement('a');
    r.className = 'row';
    r.href = '/art/' + a.id;
    var tag = document.createElement('span');
    tag.className = 'tag';
    tag.appendChild(document.createTextNode(a.label));
    r.appendChild(tag);
    r.appendChild(document.createTextNode(a.title));
    var ago = document.createElement('span');
    ago.className = 'ago';
    ago.appendChild(document.createTextNode(a.ago));
    r.appendChild(ago);
    return r;
  }
  function hideArt() {
    art = '';
    remember();
    split.style.display = 'none';
    artflow.innerHTML = '';
    if (reply) label();
    layout(false);
  }
  if (split) {
    document.getElementById('artx').onclick = hideArt;
    document.getElementById('artback').onclick = function () {
      showArt('home');
    };
    artpage.onclick = function (e) {
      e = e || window.event;
      var t = e.target || e.srcElement;
      while (t && t !== artpage && t.tagName !== 'A') t = t.parentNode; // a list row's tag or date
      if (t && t.tagName === 'A' && artLink(t)) return false;
      if (t && t.tagName === 'A') return;
      var dir = e.clientX < window.innerWidth * 0.3 ? -1 : 1;
      artpage.scrollTop = Math.max(0, Math.min(artEnd, artpage.scrollTop + dir * artStep));
    };
    // The hub button in the reply box shows the hub where you left it, and hides it again.
    artb.onclick = function () { if (art) hideArt(); else showArt(artLast); };
  }

  function grow() {
    text.style.height = 'auto';
    var max = 6 * 30 + 16;
    text.style.height = Math.min(text.scrollHeight, max) + 'px';
    text.style.overflowY = text.scrollHeight > max ? 'auto' : 'hidden';
    // Mic and Send as tall as the box, so a black (recording, Stop) button fills the whole bar.
    micb.style.height = sendb.style.height = text.offsetHeight + 'px';
    label();
    layout(false);
  }
  // Page images arrive after the text: lengths were set from their sizes, but measure again once they're in.
  document.addEventListener('load', function (e) {
    if ((e.target || {}).tagName === 'IMG') layout(false);
  }, true);
  if (doc) {
    window.onresize = function () { layout(false); };
    layout();
    return;
  }
  text.oninput = grow;
  text.onkeyup = grow;

  // While Claude works (or waits on a permission prompt) and the box is empty, Send is a Stop button.
  function stopping() {
    return (status === 'working' || status === 'blocked') && !text.value.replace(/^\s+|\s+$/g, '');
  }
  function label() {
    var stop = !sending && stopping();
    var t = sending ? '…' : stop ? 'Stop' : 'Send';
    var cls = dict ? 'off' : stop ? 'stop' : ''; // no sending while the mic is still writing into the box
    if (sendb.innerHTML !== t) sendb.innerHTML = t;
    if (sendb.className !== cls) sendb.className = cls;
    sendb.disabled = !!dict;
    // ☰ only while the box is empty, so a draft gets the box's full width.
    var menu = !text.value; // also while the mic records, until its first words arrive
    if ((menub.style.display !== 'none') !== menu) menub.style.display = artb.style.display = hint.style.display = menu ? '' : 'none';
    if (text.className !== (menu ? 'withmenu' : '')) text.className = menu ? 'withmenu' : '';
    if (artb.className !== (art ? 'on' : '')) artb.className = art ? 'on' : ''; // black while the hub is shown
  }

  function setHint(id, t) {
    var el = document.getElementById(id);
    if (el.innerText === t) return; // unchanged: don't redraw
    el.innerHTML = '';
    el.appendChild(document.createTextNode(t));
  }
  if (hint) hint.onclick = function () { text.focus(); };

  function setStatus(s, needs) {
    status = s;
    changed = new Date().getTime();
    label();
    showStatus(s === 'working' ? 'Claude is working…' : needs ? 'Needs you: ' + needs : '');
  }

  // The line under the last message; returns whether the page changed.
  function showStatus(msg) {
    if (msg === statusMsg && (!statusEl || statusEl === flow.lastChild)) return false;
    statusMsg = msg;
    if (!msg) {
      if (statusEl) { flow.removeChild(statusEl); statusEl = null; }
      return true;
    }
    if (!statusEl) { statusEl = document.createElement('p'); statusEl.className = 'status'; }
    statusEl.innerHTML = '';
    statusEl.appendChild(document.createTextNode(msg));
    if (statusEl !== flow.lastChild) flow.appendChild(statusEl); // always last
    return true;
  }

  // Errors show in a toast above the reply box: the Kindle browser doesn't show alert(). A tap on it, or 10 s,
  // closes it.
  var toastEl = null, toastTimer = null;
  function toast(msg) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'toast';
      toastEl.onclick = hideToast;
      document.body.appendChild(toastEl);
    }
    toastEl.innerHTML = '';
    toastEl.appendChild(document.createTextNode(msg));
    toastEl.style.bottom = (reply ? reply.offsetHeight : 0) + 8 + 'px';
    toastEl.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, 10000);
  }
  function hideToast() {
    clearTimeout(toastTimer);
    if (toastEl) toastEl.style.display = 'none';
  }
  // Why a request failed, in words: status 0 is no answer at all (Wi-Fi off, the panel unreachable).
  function why(code, body) {
    if (!code) return 'no connection (is the Kindle\'s Wi-Fi on?)';
    body = (body || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, '');
    return body && body.length < 300 ? body : 'error ' + code;
  }

  function request(method, url, body, done) {
    var x = new XMLHttpRequest();
    x.open(method, url, true);
    if (body) {
      x.setRequestHeader('Content-Type', 'application/json');
      x.setRequestHeader('X-Ink', '1');
    }
    x.onreadystatechange = function () {
      if (x.readyState === 4) done(x.status, x.responseText);
    };
    x.send(body ? JSON.stringify(body) : null);
  }

  // Every 2 s while Claude works; then 6 s, 20 s, 60 s the longer nothing changes.
  function delay() {
    var quiet = new Date().getTime() - changed;
    if (status === 'working' || sending) return 2000;
    return quiet < 60000 ? 6000 : quiet < 300000 ? 20000 : 60000;
  }

  function later(ms) {
    clearTimeout(timer);
    timer = setTimeout(poll, ms);
  }

  function poll() {
    clearTimeout(timer);
    timer = null;
    if (inflight) { again = true; return; }
    if (new Date().getTime() - touched > PAUSE_MS) {
      paused = true;
      if (showStatus('Paused to save battery. Tap to refresh.')) layout(false);
      return;
    }
    inflight = true;
    var g = gen;
    request('GET', '/api/chat/' + chat + '?after=' + last, null, function (code, body) {
      inflight = false;
      if (g !== gen) { again = false; poll(); return; } // switched chats meanwhile
      if (code === 200) {
        var d = JSON.parse(body);
        var was = status, dirty = false;
        if (d.reset) { flow.innerHTML = ''; statusEl = null; statusMsg = ''; queued = []; dirty = true; }
        if (document.title !== d.title) { document.title = d.title; setHint('hintname', d.title); }
        for (var i = 0; i < d.items.length; i++) {
          if (!d.reset && d.items[i].n <= last) continue; // already shown
          var div = document.createElement('div');
          div.innerHTML = d.items[i].html;
          if (queued.length) flow.insertBefore(div, queued[0]);
          else flow.appendChild(div);
          if (d.items[i].who === 'you' && queued.length && !d.reset) flow.removeChild(queued.shift()); // it's in the chat now
          dirty = true;
        }
        if (d.reset) {
          first = d.items.length ? d.items[0].n : -1;
          showOlder(d.older);
        }
        last = d.next;
        status = d.status;
        if (d.context) setHint('hintctx', d.context);
        label();
        if (showStatus(d.status === 'working' ? 'Claude is working…' : d.needs ? 'Needs you: ' + d.needs : '')) dirty = true;
        if (dirty || was !== d.status) changed = new Date().getTime();
        if (dirty) layout(d.reset ? undefined : false);
      } else if (last < 0) {
        flow.innerHTML = '<p class="note">Couldn\'t load the chat: ' + why(code, body).replace(/</g, '&lt;') + '. Reload to try again.</p>';
      } else if (!offline) {
        offline = true; // said once per outage; checks go on as usual
        toast('Lost touch with the chat: ' + why(code, body) + '. Still trying.');
      }
      if (code === 200 && offline) {
        offline = false;
        hideToast();
      }
      if (again) { again = false; poll(); } else later(delay());
    });
  }

  // Any touch counts as reading: it resumes a paused page and brings the next check forward.
  function touch() {
    touched = new Date().getTime();
    if (paused) {
      paused = false;
      changed = touched;
      poll();
    } else if (timer && delay() > 6000) {
      changed = touched;
      poll();
    }
  }
  document.addEventListener('click', touch, true);
  document.addEventListener('keydown', touch, true);
  page.addEventListener('scroll', function () { touched = new Date().getTime(); });
  artpage.addEventListener('scroll', function () { touched = new Date().getTime(); });

  sendb.onclick = function () {
    var t = text.value.replace(/^\s+|\s+$/g, '');
    if (sending || dict) return;
    if (!t && stopping()) {
      sending = true;
      label();
      request('POST', '/api/chat/' + chat + '/stop', {}, function (code, body) {
        sending = false;
        label();
        if (code !== 200) toast('Couldn\'t stop Claude: ' + why(code, body));
        later(1000);
      });
      return;
    }
    if (!t) return;
    sending = true;
    // Shown at once, greyed, below the chat; it goes when the chat itself brings the message back.
    var q = document.createElement('div');
    q.className = 'msg me queued';
    var para = document.createElement('p');
    para.appendChild(document.createTextNode(t));
    q.appendChild(para);
    flow.insertBefore(q, statusEl && statusEl.parentNode === flow ? statusEl : null);
    queued.push(q);
    text.value = '';
    draft(chat, '');
    grow();
    layout();
    var g = gen;
    request('POST', '/api/chat/' + chat + '/send', {text: t}, function (code, body) {
      sending = false;
      if (g !== gen) { // you switched chats while it went
        if (code !== 200) toast('Not sent (' + why(code, body) + '): ' + t);
        return label();
      }
      if (code === 200) {
        setStatus('working', '');
        layout(false);
        later(1000);
      } else {
        for (var i = 0; i < queued.length; i++) if (queued[i] === q) queued.splice(i, 1);
        if (q.parentNode) flow.removeChild(q);
        text.value = t + (text.value ? ' ' + text.value : '');
        grow();
        toast('Not sent: ' + why(code, body) + '. Your text is back in the box.');
      }
      label();
    });
  };

  // Dictation: the phone records (it has the mic), the server transcribes. Polled every second, only while
  // dictating; the text goes into the reply box after whatever was already there.
  var dict = null, dictBase = '';
  function clock(s) { return Math.floor(s / 60) + ':' + (s % 60 < 10 ? '0' : '') + s % 60; }
  function dictEnd(err) {
    dict = null;
    label();
    micb.innerHTML = 'Mic';
    micb.className = '';
    if (err) toast(err);
  }
  function dictPoll() {
    request('GET', '/api/dictate', null, function (code, body) {
      if (code !== 200) return dictEnd('Dictation failed: ' + why(code, body));
      var d = JSON.parse(body);
      if (d.text && text.value !== dictBase + d.text) {
        text.value = dictBase + d.text;
        grow();
        text.scrollTop = text.scrollHeight; // follow the newest words as they come in
      }
      if (d.state === 'error') return dictEnd(d.error);
      if (d.state !== 'recording' && d.state !== 'transcribing') {
        var note = d.ended ? 'Recording stopped: ' + d.ended + '.' : '';
        if (d.missed) note += (note ? ' ' : '') + 'Couldn\'t make out ' + d.missed + ' s of what you said.';
        return dictEnd(note);
      }
      if (d.state === 'recording' && dict === 'recording') micb.innerHTML = '&#9632; ' + clock(d.seconds);
      setTimeout(dictPoll, 1000);
    });
  }
  micb.onclick = function () {
    if (dict === 'recording') {
      dict = 'transcribing';
      micb.innerHTML = '…';
      request('POST', '/api/dictate/stop', {}, function (code, body) {
        if (code !== 200) toast('Couldn\'t stop the mic: ' + why(code, body));
      });
      return;
    }
    if (dict) return;
    dict = 'starting';
    label();
    micb.innerHTML = '…';
    dictBase = text.value.replace(/\s+$/, '');
    if (dictBase) dictBase += ' ';
    request('POST', '/api/dictate/start', {}, function (code, body) {
      if (code !== 200) return dictEnd('Mic didn\'t start: ' + why(code, body));
      dict = 'recording';
      micb.className = 'on';
      micb.innerHTML = '&#9632; 0:00';
      dictPoll();
    });
  };

  // Earlier messages: a chat opens with its last few; this button above them fetches the page before.
  function showOlder(has) {
    if (olderEl && olderEl.parentNode) olderEl.parentNode.removeChild(olderEl);
    olderEl = null;
    if (!has) return;
    olderEl = document.createElement('p');
    olderEl.className = 'older';
    var b = document.createElement('button');
    b.innerHTML = 'Earlier messages';
    b.onclick = loadOlder;
    olderEl.appendChild(b);
    flow.insertBefore(olderEl, flow.firstChild);
  }
  function loadOlder() {
    var g = gen;
    olderEl.firstChild.innerHTML = 'Loading&hellip;';
    request('GET', '/api/chat/' + chat + '?before=' + first, null, function (code, body) {
      if (g !== gen) return;
      if (code !== 200) { olderEl.firstChild.innerHTML = 'Earlier messages'; return toast('Couldn\'t load earlier messages: ' + why(code, body)); }
      var d = JSON.parse(body), at = olderEl.nextSibling, before = flow.offsetHeight, top = page.scrollTop;
      for (var i = 0; i < d.items.length; i++) {
        var div = document.createElement('div');
        div.innerHTML = d.items[i].html;
        flow.insertBefore(div, at);
      }
      if (d.items.length) first = d.items[0].n;
      showOlder(d.older);
      layout(false);
      page.scrollTop = top + flow.offsetHeight - before; // the page you were on stays put
    });
  }

  // Drafts per chat, so switching never loses what you typed.
  function draft(id, value) {
    try {
      if (value === undefined) return localStorage.getItem('draft:' + id) || '';
      if (value) localStorage.setItem('draft:' + id, value); else localStorage.removeItem('draft:' + id);
    } catch (e) {}
    return '';
  }
  text.onblur = function () { draft(chat, text.value); };

  // The switcher: ☰ lists recent chats over this one; tapping one swaps it in place, no page load.
  function closeMenu() { switcher.style.display = 'none'; menub.className = ''; }
  function row(c) {
    var a = document.createElement('a');
    a.className = 'row' + (c.id === chat ? ' cur' : '');
    a.href = '/c/' + c.id;
    if (c.label) {
      var tag = document.createElement('span');
      tag.className = 'tag';
      tag.appendChild(document.createTextNode(c.label));
      a.appendChild(tag);
    }
    a.appendChild(document.createTextNode(c.title));
    a.onclick = function () { openChat(c.id); return false; };
    return a;
  }
  menub.onclick = function () {
    if (switcher.style.display === 'block') return closeMenu();
    switcher.style.height = h + 'px';
    switcher.innerHTML = '<p class="links">Loading&hellip;</p>';
    switcher.style.display = 'block';
    menub.className = 'on'; // black while the list is shown, like the hub button
    request('GET', '/api/chats', null, function (code, body) {
      if (switcher.style.display !== 'block') return;
      switcher.innerHTML = '';
      if (code === 200) {
        var cs = JSON.parse(body).chats;
        for (var i = 0; i < cs.length; i++) switcher.appendChild(row(cs[i]));
      } else toast('Couldn\'t list the chats: ' + why(code, body));
      var links = document.createElement('p');
      links.className = 'links';
      links.innerHTML = '<a href="/">All chats</a><a href="/new">New chat</a>';
      switcher.appendChild(links);
    });
  };
  switcher.onclick = function (e) {
    e = e || window.event;
    if ((e.target || e.srcElement) === switcher) closeMenu(); // a tap below the list
  };
  function openChat(id) {
    closeMenu();
    if (id === chat) return;
    draft(chat, text.value);
    chat = id;
    gen++;
    last = -1; first = -1; status = ''; queued = []; olderEl = null;
    statusEl = null; statusMsg = '';
    flow.innerHTML = '<p class="note">Loading&hellip;</p>';
    text.value = draft(id);
    setHint('hintname', 'Loading…');
    setHint('hintctx', '');
    if (window.history && history.replaceState) history.replaceState(null, '', '/c/' + id);
    grow();
    layout();
    changed = new Date().getTime();
    poll();
  }

  if (!text.value) text.value = draft(chat);
  try { if (localStorage.getItem('art')) showArt(localStorage.getItem('art')); } catch (e) {} // still open after a reload
  window.onresize = function () { layout(false); };
  grow();
  layout();
  poll();
})();
