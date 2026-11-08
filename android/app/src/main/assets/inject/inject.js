// Runs at document start on every claude.ai page inside the app (see MainActivity), and again when the
// dev loop pushes it into a running page, so each feature guards itself.
// window.__CCM_CSS holds inject.css.
(() => {
  const root = document.documentElement;
  root.dataset.ccmApp = '1';

  let style = document.getElementById('ccm-css');
  if (!style) {
    style = document.createElement('style');
    style.id = 'ccm-css';
    // <head> may not exist yet at document start; <html> works as a parent for <style> too.
    (document.head || root).appendChild(style);
  }
  if (window.__CCM_CSS != null) style.textContent = window.__CCM_CSS;

  // On Send: close the keyboard (the composer keeps focus otherwise, and the keyboard hides the reply as
  // it streams in), and report the session to the push server (if any) for notification routing.
  if (!window.__ccmBlurOnSend) {
    window.__ccmBlurOnSend = true;
    document.addEventListener('click', e => {
      if (!e.target.closest?.('button[aria-label="Send"]')) return;
      setTimeout(() => document.activeElement?.blur(), 0);
      // Tell the push server this install sent the session's latest message, so its notification comes here only.
      const session = /\/code\/(session_[A-Za-z0-9]+)/.exec(location.pathname)?.[1];
      if (session) window.Capacitor?.Plugins?.LinkRouter?.reportActivity?.({ session })?.catch(() => {});
    }, true);
  }

  // Side panels (files, progress...) open as a ~265px tile beside a squeezed chat on phones. Press the
  // panel's own Expand button once when each panel appears; Collapse still works after that.
  if (!window.__ccmAutoExpand) {
    window.__ccmAutoExpand = true;
    const seen = new WeakSet();
    let queued = false;
    const check = () => {
      queued = false;
      if (innerWidth >= 600) return;
      for (const pane of document.querySelectorAll('[data-pane-root]')) {
        if (seen.has(pane)) continue;
        const expand = pane.querySelector('button[aria-label="Expand"]');
        if (!expand) continue;
        seen.add(pane);
        expand.click();
      }
    };
    new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(check); } })
      .observe(document.documentElement, { childList: true, subtree: true });
  }

  // No keyboard unless asked: claude.ai focuses the composer when a chat loads or a new one starts, which
  // pops the keyboard over the reply you came to read (e.g. from a notification). Focus on a text field is
  // undone unless the user just touched that field (or something around it, like the composer's box) or
  // typed; tapping "New session" or a chat in the sidebar doesn't count.
  if (!window.__ccmNoAutoFocus) {
    window.__ccmNoAutoFocus = true;
    let lastDown = 0, downAt = null, lastKey = 0;
    for (const type of ['pointerdown', 'touchstart', 'mousedown']) document.addEventListener(type, e => { lastDown = Date.now(); downAt = e.target; }, true);
    document.addEventListener('keydown', () => { lastKey = Date.now(); }, true);
    document.addEventListener('focusin', e => {
      const t = e.target;
      const editable = t.isContentEditable || t.tagName === 'TEXTAREA' || (t.tagName === 'INPUT' && !/^(button|checkbox|radio|submit|range|color|file)$/.test(t.type));
      if (!editable) return;
      const now = Date.now();
      const touched = now - lastDown < 1000 && downAt instanceof Node && (t.contains(downAt) || (downAt.contains(t) && downAt !== document.body && downAt !== document.documentElement));
      if (!touched && now - lastKey > 1000) t.blur();
    }, true);
  }

  // Header: on phones only ⋯ ("View options") stays. claude.ai folds the header's buttons into ⋯ itself when the
  // title looks squeezed (its scrollWidth over its clientWidth), so the title always asks for more room than it
  // has: Session rail, Changes, Files, Plan... all fold. Share it keeps out; that one stays in the page, invisible
  // (inject.css), and a menu entry clicks it, so its popup still opens from the header's corner. The entry is a
  // clone of claude.ai's own (no React handlers come with a clone), so it follows the theme.
  if (!window.__ccmHeader) {
    window.__ccmHeader = true;
    const nativeScrollWidth = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollWidth').get;
    const SHARE = 'button:is([aria-label="Share"], [data-titlebar-share-slot])';

    const extendMenu = menu => {
      const share = [...document.querySelectorAll(`.epitaxy-titlebar-end ${SHARE}`)].find(e => e.isConnected);
      // The last row people see (the sheet ends in a 1px Close row for screen readers).
      const plan = [...menu.querySelectorAll('[role^="menuitem"]')].filter(i => i.offsetHeight > 8).at(-1);
      if (!share || !plan || menu.querySelector('[data-ccm-moved]')) return;
      const sep = document.createElement('div');
      sep.dataset.ccmMoved = 'sep';
      const row = plan.cloneNode(true);
      row.dataset.ccmMoved = 'Share';
      row.setAttribute('role', 'menuitem');
      for (const a of ['id', 'aria-checked', 'data-checked', 'data-unchecked', 'data-highlighted']) row.removeAttribute(a);
      row.querySelector('.truncate').textContent = 'Share';
      const icon = share.querySelector('[data-cds="Icon"]');
      if (icon) row.querySelector('[data-cds="Icon"]')?.replaceWith(icon.cloneNode(true));
      [...row.children].slice(2).forEach(c => c.remove()); // the shortcut / check mark of the cloned row
      row.addEventListener('click', e => {
        e.stopPropagation();
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        setTimeout(() => share.click(), 120); // once the menu has closed
      });
      plan.after(sep, row);
    };

    let queued = false;
    const scan = () => {
      queued = false;
      if (innerWidth >= 600) return;
      for (const t of document.querySelectorAll('.epitaxy-titlebar [data-pane-title]')) {
        if (!Object.hasOwn(t, 'scrollWidth')) {
          Object.defineProperty(t, 'scrollWidth', { get: () => innerWidth < 600 ? t.clientWidth + 4000 : nativeScrollWidth.call(t) });
        }
      }
      if (document.querySelector('button[aria-label="View options"][aria-expanded="true"]') && !document.documentElement.dataset.ccmQuietMenu) {
        for (const menu of document.querySelectorAll('[role="menu"]')) extendMenu(menu);
      }
    };
    new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(scan); } })
      .observe(document.documentElement, { childList: true, subtree: true });

    // Picks the ⋯ menu's item whose text matches, with the menu hidden (html[data-ccm-quiet-menu]) so it
    // doesn't flash. Items are toggles: one already on is left alone.
    window.__ccmMenuItem = async match => {
      const more = [...document.querySelectorAll('button[aria-label="View options"]')].find(e => e.offsetParent);
      if (!more) return false;
      root.dataset.ccmQuietMenu = '1';
      try {
        more.click();
        for (let i = 0; i < 40; i++) {
          await new Promise(r => setTimeout(r, 25));
          const item = [...document.querySelectorAll('[role="menu"] [role^="menuitem"]')].find(e => match.test(e.textContent));
          if (!item) continue;
          if (item.getAttribute('aria-checked') === 'true') document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
          else item.click();
          return true;
        }
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        return false;
      } finally {
        setTimeout(() => delete root.dataset.ccmQuietMenu, 300);
      }
    };
  }

  // Session messages: what claude.ai receives for a session, from its websocket (live) and its events
  // history (fetched on load), handed to the listeners in window.__ccmSessionListeners as (session id,
  // message). Wrapping WebSocket and fetch only works at document start, before claude.ai uses them.
  if (!window.__ccmSessionListeners) {
    const listeners = window.__ccmSessionListeners = [];
    const deliver = (session, m, at) => { for (const l of listeners) try { l(session, m, at); } catch {} };
    const NativeWS = window.WebSocket;
    window.WebSocket = class extends NativeWS {
      constructor(url, protocols) {
        super(url, protocols);
        const session = /\/sessions\/ws\/(session_[A-Za-z0-9]+)\/subscribe/.exec(String(url))?.[1];
        if (!session) return;
        this.addEventListener('message', e => {
          if (typeof e.data !== 'string') return;
          for (const line of e.data.split('\n')) {
            if (!line) continue;
            try { const m = JSON.parse(line); deliver(session, m, m.created_at); } catch {}
          }
        });
      }
    };
    const nativeFetch = window.fetch;
    window.fetch = async function (input, init) {
      const res = await nativeFetch.apply(this, arguments);
      const url = typeof input === 'string' ? input : input?.url || '';
      const id = /\/v1\/(?:code\/)?sessions\/(?:session|cse)_([A-Za-z0-9]+)\/events\?/.exec(url)?.[1];
      if (id && res.ok) {
        res.clone().json().then(d => {
          for (const ev of d?.data || []) if (ev?.payload) deliver('session_' + id, ev.payload, ev.created_at);
        }).catch(() => {});
      }
      return res;
    };
  }

  // Background tasks: the phone layout lists them only behind ⋯ → Background tasks. While any run, show a
  // "3 background tasks" pill in the composer dock, above the input; a tap opens that panel directly. The
  // list comes from the session's own websocket, which sends `background_tasks_changed` with the running
  // tasks on connect and whenever they change (see "Session messages").
  if (!window.__ccmBgTasks) {
    window.__ccmBgTasks = {}; // session id -> running tasks
    const running = window.__ccmBgTasks;
    window.__ccmSessionListeners.push((session, m) => {
      if (m.subtype === 'background_tasks_changed') { running[session] = m.tasks || []; queue(); }
    });

    // ⋯ → Background tasks.
    const openPanel = () => window.__ccmMenuItem(/Background tasks/);
    let queued = false;
    const queue = () => { if (!queued) { queued = true; requestAnimationFrame(render); } };
    function render() {
      queued = false;
      const session = /\/code\/(session_[A-Za-z0-9]+)/.exec(location.pathname)?.[1];
      const n = ((session && running[session]) || []).length;
      const prompt = document.querySelector('.group\\/approval-dock > .epitaxy-prompt');
      let pill = document.getElementById('ccm-bg-tasks');
      if (!n || !prompt) return pill?.remove();
      if (pill?.nextElementSibling !== prompt) {
        pill?.remove();
        pill = document.createElement('button');
        pill.id = 'ccm-bg-tasks';
        pill.type = 'button';
        pill.addEventListener('click', openPanel);
        prompt.before(pill);
      }
      const label = n === 1 ? '1 background task' : `${n} background tasks`;
      if (pill.textContent !== label) pill.textContent = label;
    }
    new MutationObserver(queue).observe(document.documentElement, { childList: true, subtree: true });
  }

  // Context used, in the composer's bottom strip next to the model (the phone layout shows it nowhere), so
  // it's clear when to /compact. Worked out from the session's messages: the latest top-level reply's
  // usage (input + cached + output tokens) over the model's context window, which comes from claude.ai's
  // own get_context_usage replies whenever the page asks (and is remembered per model; until then 1M for
  // Claude 5 models, 200k otherwise). A tap opens Session details, with claude.ai's full breakdown.
  if (!window.__ccmContext) {
    const ctx = window.__ccmContext = {}; // session id -> { tokens, at, model, exact: { percentage, at } }
    const windows = JSON.parse(localStorage.getItem('ccm.contextWindows') || '{}');
    const windowFor = model => windows[model] || (/claude-\w+-5/.test(model || '') ? 1_000_000 : 200_000);
    window.__ccmSessionListeners.push((session, m, at = '') => {
      const c = ctx[session] ||= { tokens: 0, at: '' };
      const usage = m.type === 'assistant' && !m.parent_tool_use_id && m.message?.usage;
      if (usage && at >= c.at) {
        c.tokens = (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0) + (usage.cache_creation_input_tokens || 0) + (usage.output_tokens || 0);
        c.model = m.message.model || c.model;
        c.at = at;
        queue();
      }
      const r = m.response?.response;
      if (r && typeof r.percentage === 'number' && r.maxTokens) {
        if (r.model) { windows[r.model] = r.maxTokens; localStorage.setItem('ccm.contextWindows', JSON.stringify(windows)); c.model = r.model; }
        c.exact = { percentage: r.percentage, at: at || new Date().toISOString() };
        queue();
      }
    });

    const RING = p => `<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" stroke-opacity=".25" stroke-width="2"/><circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-dasharray="${(p / 100 * 39.27).toFixed(2)} 39.27" transform="rotate(-90 8 8)"/></svg>`;
    let queued = false;
    const queue = () => { if (!queued) { queued = true; requestAnimationFrame(render); } };
    function render() {
      queued = false;
      const session = /\/code\/(session_[A-Za-z0-9]+)/.exec(location.pathname)?.[1];
      const c = session && ctx[session];
      const model = [...document.querySelectorAll('button[aria-label^="Model:"]')].find(b => b.offsetParent);
      let chip = document.getElementById('ccm-context');
      if (!c || !model || !(c.tokens || c.exact)) return chip?.remove();
      const pct = c.exact && c.exact.at >= c.at ? c.exact.percentage : Math.round(c.tokens / windowFor(c.model) * 100);
      if (chip?.nextElementSibling !== model) {
        chip?.remove();
        chip = document.createElement('button');
        chip.id = 'ccm-context';
        chip.type = 'button';
        chip.addEventListener('click', () => {
          const rail = [...document.querySelectorAll('.epitaxy-titlebar-end button[aria-label="Session rail"]')].find(e => e.offsetParent);
          if (rail) rail.click(); else window.__ccmMenuItem(/Show session rail/); // folded into ⋯ on phones
        });
        model.before(chip);
      }
      const key = String(pct);
      if (chip.dataset.pct === key) return;
      chip.dataset.pct = key;
      chip.dataset.level = pct >= 80 ? 'high' : pct >= 60 ? 'mid' : 'low';
      chip.setAttribute('aria-label', `Context: ${pct}% used`);
      chip.innerHTML = RING(Math.min(pct, 100)) + `<span>${pct}%</span>`;
    }
    new MutationObserver(queue).observe(document.documentElement, { childList: true, subtree: true });
  }
})();
