package io.github.ccwrapper;

import android.content.Context;
import android.content.res.ColorStateList;
import android.content.res.Configuration;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import androidx.appcompat.widget.SwitchCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import java.util.ArrayList;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * The servers overlay: the handle at the top of the page (MainActivity) fades it in over the whole screen; the
 * handle again, or Back, fades it out. It doesn't move, so its lists scroll on their own. Built in code,
 * following the system's dark or light mode like claude.ai does.
 *
 * Its screens, one at a time (a new one fades in over the last; Back or the back arrow returns):
 * - Claude servers: the chat on screen (its folder, and whether that folder's server is running, with Start if not),
 *   then every folder's Remote Control server on your server's machine, most recently active first (running or
 *   stopped, chats it's serving, when; Start on a stopped one). A folder's server starts a chat's Claude whenever a
 *   message comes for it, so a chat works as long as its server is up. GET HUB_URL/api/sessions/servers.
 * - A server: its chats, the live ones (working or idle) and its earlier ones; a tap opens one. Stop and Delete for
 *   the server. GET HUB_URL/api/sessions/servers/chats?cwd=.
 * - Settings: Kindle dictation.
 */
final class SettingsTray extends FrameLayout {
    interface Host {
        /** The dictation switch was flipped by the user; the host deals with permissions, then calls refresh(). */
        void onDictationSwitch(boolean on);
        /** The overlay opened or closed (for the Back handler). */
        void onTrayShown(boolean shown);
        /** The reload button: reload the page. */
        void onReload();
        /** A chat was tapped: show it (https://claude.ai/code/session_...). */
        void onOpenChat(String url);
        /** The page's address, to know the chat on screen. */
        String currentUrl();
    }

    interface Action { void run() throws Exception; }

    private static final int ANIM_MS = 220;
    private static final int RUNNING = 0xFF5FB87F;

    private final Host host;
    private final LinearLayout frame;
    private final FrameLayout pages;
    private final ArrayList<View> stack = new ArrayList<>();
    private final int bg, card, line, onSurface, muted, accent;
    private final boolean dark;
    private boolean open;
    private float shown; // 0 (gone) .. 1 (open), as the pull or the grab bar leaves it

    // Settings: Kindle dictation (made when the settings screen opens).
    private SwitchCompat dictation;
    private TextView dictationStatus;
    private boolean updating;
    private final Runnable stateListener = () -> post(this::refresh);

    // The servers screen, and the last answer (shown while a new one comes).
    private LinearLayout serversBody;
    private TextView serversStatus;
    private static JSONObject lastServers;
    private int generation;

    SettingsTray(Context context, Host host) {
        super(context);
        this.host = host;
        dark = (context.getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        bg = dark ? 0xFF262624 : 0xFFFAF9F5;
        card = dark ? 0xFF30302E : 0xFFF0EEE6;
        line = dark ? 0xFF3A3936 : 0xFFE2DFD5;
        onSurface = dark ? 0xFFF5F4EE : 0xFF141413;
        muted = dark ? 0xFFA6A39A : 0xFF6B6A65;
        accent = 0xFFD97757;

        setVisibility(GONE);
        setClickable(true);

        frame = new LinearLayout(context);
        frame.setOrientation(LinearLayout.VERTICAL);
        frame.setBackgroundColor(bg);
        addView(frame, new LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        pages = new FrameLayout(context);
        frame.addView(pages, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        View servers = serversPage(context);
        stack.add(servers);
        pages.addView(servers);
        ViewCompat.setOnApplyWindowInsetsListener(this, (v, in) -> {
            Insets bars = in.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            frame.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return in;
        });
    }

    boolean isShowing() {
        return getVisibility() == VISIBLE;
    }

    /** Shows the dictation switch as the setting stands. Safe to call any time on the UI thread. */
    void refresh() {
        if (dictation == null) return;
        updating = true;
        boolean on = MicService.switchedOn(getContext());
        dictation.setChecked(on);
        updating = false;
        dictationStatus.setVisibility(on ? VISIBLE : GONE);
        dictationStatus.setText(MicService.describe(MicService.state));
    }

    @Override
    protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        MicService.listen(stateListener);
    }

    @Override
    protected void onDetachedFromWindow() {
        MicService.unlisten(stateListener);
        super.onDetachedFromWindow();
    }

    // ---- showing and hiding: the handle (MainActivity), Back ---------------------------------------------------

    /** The handle was tapped: shows the overlay, or hides it when it's showing. */
    void toggle() {
        if (isShowing() && open) close();
        else { prepare(); open(); }
    }

    /** About to show: the servers screen, asked for afresh, still invisible. */
    private void prepare() {
        while (stack.size() > 1) pop(false);
        loadServers();
        ViewCompat.requestApplyInsets(this);
        setVisibility(VISIBLE);
        place(0);
        host.onTrayShown(true);
    }

    void open() {
        open = true;
        settle(1, null);
    }

    void close() {
        open = false;
        if (!isShowing()) return;
        settle(0, () -> {
            if (!open) {
                setVisibility(GONE);
                host.onTrayShown(false);
            }
        });
    }

    /** Back: the screen before, or closes the overlay on the first. */
    void back() {
        if (stack.size() > 1) pop(true);
        else close();
    }

    private android.animation.ValueAnimator settling;

    private void settle(float to, Runnable end) {
        if (settling != null) settling.cancel();
        settling = android.animation.ValueAnimator.ofFloat(shown, to);
        settling.setDuration(ANIM_MS);
        settling.addUpdateListener(x -> place((float) x.getAnimatedValue()));
        if (end != null) settling.addListener(new android.animation.AnimatorListenerAdapter() {
            boolean cancelled;
            @Override public void onAnimationCancel(android.animation.Animator a) { cancelled = true; }
            @Override public void onAnimationEnd(android.animation.Animator a) { if (!cancelled) end.run(); }
        });
        settling.start();
    }

    /** 0: gone; 1: open. It only fades. */
    private void place(float f) {
        shown = f;
        setAlpha(f);
    }

    // ---- screens ------------------------------------------------------------------------------------------------

    /** A screen: a header (a back arrow when it isn't the first, the title, buttons) over a body that scrolls. */
    private LinearLayout page(Context ctx, String title, boolean backArrow, LinearLayout body, View... buttons) {
        LinearLayout p = new LinearLayout(ctx);
        p.setOrientation(LinearLayout.VERTICAL);
        p.setBackgroundColor(bg);
        p.setClickable(true);
        LinearLayout head = new LinearLayout(ctx);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.setPadding(dp(backArrow ? 4 : 20), dp(16), dp(6), dp(6)); // the handle sits above it
        if (backArrow) head.addView(icon(ctx, R.drawable.ic_back, onSurface, "Back", () -> pop(true)));
        TextView t = text(ctx, title, 20, onSurface);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setSingleLine(true);
        t.setEllipsize(android.text.TextUtils.TruncateAt.START);
        head.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        for (View b : buttons) head.addView(b);
        p.addView(head);
        View rule = new View(ctx);
        rule.setBackgroundColor(line);
        p.addView(rule, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, Math.max(1, dp(0.5f))));
        ScrollView scroll = new ScrollView(ctx);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(0, dp(8), 0, dp(16));
        scroll.addView(body);
        p.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        return p;
    }

    private void push(View p) {
        View under = stack.get(stack.size() - 1);
        stack.add(p);
        pages.addView(p);
        p.setAlpha(0f);
        p.animate().alpha(1f).setDuration(ANIM_MS).withEndAction(() -> under.setVisibility(INVISIBLE)).start();
    }

    private void pop(boolean animate) {
        if (stack.size() < 2) return;
        View top = stack.remove(stack.size() - 1), under = stack.get(stack.size() - 1);
        if (top == settingsPage) { dictation = null; settingsPage = null; }
        under.setVisibility(VISIBLE);
        if (!animate) { top.animate().cancel(); pages.removeView(top); return; }
        top.animate().alpha(0f).setDuration(ANIM_MS).withEndAction(() -> pages.removeView(top)).start();
    }

    // The servers screen.

    private View serversPage(Context ctx) {
        serversBody = new LinearLayout(ctx);
        serversStatus = text(ctx, "", 13, muted);
        return page(ctx, "Claude servers", false, serversBody,
            icon(ctx, R.drawable.ic_refresh, onSurface, "Reload the page", host::onReload),
            icon(ctx, R.drawable.ic_settings, onSurface, "Settings", () -> push(settingsPage(ctx))));
    }

    /** The chat on screen's claude.ai id, or null. */
    private String session() {
        String url = host.currentUrl();
        java.util.regex.Matcher m = url == null ? null : java.util.regex.Pattern.compile("/code/(session_[A-Za-z0-9]+)").matcher(url);
        return m != null && m.find() ? m.group(1) : null;
    }

    private void loadServers() {
        int g = ++generation;
        String session = session();
        if (lastServers != null) showServers(lastServers, session, false);
        else status(serversBody, serversStatus, "Asking your server…");
        new Thread(() -> {
            try {
                JSONObject r = LinkRouter.servers(session);
                post(() -> { if (g == generation) { lastServers = r; showServers(r, session, true); } });
            } catch (Exception e) {
                post(() -> { if (g == generation) status(serversBody, serversStatus, e.getMessage()); });
            }
        }).start();
    }

    private void showServers(JSONObject r, String session, boolean fresh) {
        Context ctx = getContext();
        serversBody.removeAllViews();
        JSONObject chat = r.optJSONObject("chat");
        if (chat != null && !chat.optString("session").equals(session)) chat = null; // an answer about another chat
        String here = chat == null ? null : chat.optString("cwd");
        if (chat != null) {
            boolean up = chat.optBoolean("server");
            String sid = chat.optString("session");
            serversBody.addView(card(ctx, up, "This chat · " + folder(here),
                up ? "Its server is running: messages reach it" : "Its server is stopped",
                up ? null : icon(ctx, R.drawable.ic_play, accent, "Start", work(() -> LinkRouter.connect(sid), this::loadServers))));
        } else if (session != null && fresh) {
            serversBody.addView(note(ctx, "This chat isn't on your server."));
        }
        serversBody.addView(label(ctx, "Servers"));
        JSONArray list = r.optJSONArray("servers");
        for (int i = 0; list != null && i < list.length(); i++) {
            JSONObject s = list.optJSONObject(i);
            if (s == null) continue;
            String cwd = s.optString("cwd");
            boolean up = s.optBoolean("running");
            int n = s.optInt("chats");
            String sub = (up ? "running" + (n > 0 ? " · " + n + " live" : "") : "stopped") + when(s.optLong("active"));
            LinearLayout row = row(ctx, up, folder(cwd) + (cwd.equals(here) ? "  (this chat)" : ""), sub, cwd.equals(here));
            if (!up) row.addView(icon(ctx, R.drawable.ic_play, accent, "Start", work(() -> LinkRouter.startServer(cwd), this::loadServers)));
            ImageButton chev = icon(ctx, R.drawable.ic_chevron, muted, null, null);
            chev.setClickable(false);
            chev.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
            row.addView(chev);
            row.setOnClickListener(v -> push(serverPage(ctx, cwd)));
            serversBody.addView(row);
        }
    }

    // A server's screen: its state and its chats.

    private View serverPage(Context ctx, String cwd) {
        LinearLayout body = new LinearLayout(ctx);
        TextView status = text(ctx, "", 13, muted);
        Runnable[] load = new Runnable[1];
        load[0] = () -> {
            if (body.getChildCount() == 0) status(body, status, "Asking your server…");
            new Thread(() -> {
                try {
                    JSONObject r = LinkRouter.serverChats(cwd);
                    post(() -> showServer(ctx, body, r, load[0]));
                } catch (Exception e) {
                    post(() -> status(body, status, e.getMessage()));
                }
            }).start();
        };
        String name = folder(cwd);
        View page = page(ctx, name, true, body,
            icon(ctx, R.drawable.ic_stop, onSurface, "Stop", confirm(ctx,
                "Stop the server of " + name + "?\n\nIts chats' Claudes stop with it; a message to one of them waits until it's started again.",
                "Stop", work(() -> LinkRouter.stopServer(cwd), () -> { load[0].run(); loadServers(); }))),
            icon(ctx, R.drawable.ic_delete, onSurface, "Delete", confirm(ctx,
                "Delete the server of " + name + "?\n\nIt stops, and leaves the list until a chat starts there again.",
                "Delete", work(() -> LinkRouter.deleteServer(cwd), () -> { pop(true); loadServers(); }))));
        load[0].run();
        return page;
    }

    private void showServer(Context ctx, LinearLayout body, JSONObject r, Runnable reload) {
        body.removeAllViews();
        boolean up = r.optBoolean("running");
        JSONArray live = r.optJSONArray("live"), earlier = r.optJSONArray("earlier");
        int n = live == null ? 0 : live.length();
        String cwd = r.optString("cwd");
        long active = r.optLong("active");
        body.addView(card(ctx, up, up ? "Server running" : "Server stopped",
            (up ? n + (n == 1 ? " chat" : " chats") + " live" : "Its chats wait for it") + (active > 0 ? " · last active " + agoMs(active) : ""),
            up ? null : icon(ctx, R.drawable.ic_play, accent, "Start", work(() -> LinkRouter.startServer(cwd), () -> { reload.run(); loadServers(); }))));
        if (n > 0) {
            body.addView(label(ctx, "Live"));
            for (int i = 0; i < n; i++) body.addView(chatRow(ctx, live.optJSONObject(i), true));
        }
        if (earlier != null && earlier.length() > 0) {
            body.addView(label(ctx, "Earlier"));
            for (int i = 0; i < earlier.length(); i++) body.addView(chatRow(ctx, earlier.optJSONObject(i), false));
        }
        if (n == 0 && (earlier == null || earlier.length() == 0)) body.addView(note(ctx, "No chats here yet."));
    }

    private View chatRow(Context ctx, JSONObject c, boolean live) {
        String t = c.optString("title");
        String sub = live ? (c.optBoolean("working") ? "working" : "idle") : "";
        String w = ago(c.optString("updated"));
        if (!w.isEmpty()) sub = sub.isEmpty() ? w : sub + " · " + w;
        LinearLayout row = row(ctx, live, t.isEmpty() ? "Untitled" : t, sub, false);
        String url = c.optString("url");
        row.setOnClickListener(v -> host.onOpenChat(url));
        return row;
    }

    // Settings.

    private View settingsPage;

    private View settingsPage(Context ctx) {
        LinearLayout body = new LinearLayout(ctx);
        LinearLayout box = new LinearLayout(ctx);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(4), dp(20), 0);
        dictation = new SwitchCompat(ctx);
        dictation.setText("Kindle dictation (use this phone's mic)");
        dictation.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        dictation.setTextColor(onSurface);
        dictation.setMinHeight(dp(48));
        int[][] states = { { android.R.attr.state_checked }, {} };
        dictation.setThumbTintList(new ColorStateList(states, new int[] { accent, dark ? 0xFFC2C0B6 : 0xFFFFFFFF }));
        dictation.setTrackTintList(new ColorStateList(states, new int[] { (accent & 0x00FFFFFF) | 0x80000000, dark ? 0xFF5A5954 : 0xFFC9C7BE }));
        dictation.setOnCheckedChangeListener((b, on) -> { if (!updating) host.onDictationSwitch(on); });
        box.addView(dictation);
        box.addView(text(ctx, MicService.configured()
            ? "Your hub's Kindle panel records through this phone, locked or not; the mic is only on while you dictate there."
            : "Needs HUB_URL in android/.env at build time (see the README).", 13, muted));
        dictationStatus = text(ctx, "", 13, accent);
        box.addView(dictationStatus);
        if (!MicService.configured()) dictation.setEnabled(false);
        body.addView(box);
        refresh();
        settingsPage = page(ctx, "Settings", true, body);
        return settingsPage;
    }

    // ---- pieces -------------------------------------------------------------------------------------------------

    /** A box at the top of a screen: a dot, a title, a line under it, and a button (or none). */
    private View card(Context ctx, boolean up, String title, String sub, View button) {
        LinearLayout c = row(ctx, up, title, sub, false);
        c.setBackground(round(card, dp(14)));
        c.setPadding(dp(14), dp(8), dp(6), dp(8));
        if (button != null) c.addView(button);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(12), dp(4), dp(12), dp(6));
        c.setLayoutParams(lp);
        return c;
    }

    /** A list row: a dot (green when up), a title, a line under it. */
    private LinearLayout row(Context ctx, boolean up, String title, String sub, boolean here) {
        LinearLayout row = new LinearLayout(ctx);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setMinimumHeight(dp(56));
        row.setPadding(dp(20), dp(6), dp(6), dp(6));
        if (here) row.setBackgroundColor(card);
        else {
            TypedValue ripple = new TypedValue();
            ctx.getTheme().resolveAttribute(android.R.attr.selectableItemBackground, ripple, true);
            row.setBackgroundResource(ripple.resourceId);
        }
        View dot = new View(ctx);
        dot.setBackground(round(up ? RUNNING : muted, dp(5)));
        dot.setAlpha(up ? 1f : 0.45f);
        LinearLayout.LayoutParams dp9 = new LinearLayout.LayoutParams(dp(9), dp(9));
        dp9.rightMargin = dp(14);
        row.addView(dot, dp9);
        LinearLayout words = new LinearLayout(ctx);
        words.setOrientation(LinearLayout.VERTICAL);
        TextView t = text(ctx, title, 15, onSurface);
        t.setSingleLine(true);
        t.setEllipsize(android.text.TextUtils.TruncateAt.END);
        words.addView(t);
        if (!sub.isEmpty()) {
            TextView s = text(ctx, sub, 12.5f, muted);
            s.setSingleLine(true);
            words.addView(s);
        }
        row.addView(words, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        return row;
    }

    private TextView label(Context ctx, String s) {
        TextView t = text(ctx, s.toUpperCase(java.util.Locale.ROOT), 12, muted);
        t.setLetterSpacing(0.06f);
        t.setPadding(dp(20), dp(14), dp(20), dp(4));
        return t;
    }

    private TextView note(Context ctx, String s) {
        TextView t = text(ctx, s, 13, muted);
        t.setPadding(dp(20), dp(6), dp(20), dp(6));
        return t;
    }

    /** A screen's body showing only a line of status ("Asking…", an error). */
    private void status(LinearLayout body, TextView status, String s) {
        body.removeAllViews();
        status.setText(s);
        status.setPadding(dp(20), dp(10), dp(20), dp(10));
        body.addView(status);
    }

    private ImageButton icon(Context ctx, int res, int color, String label, Runnable click) {
        ImageButton b = new ImageButton(ctx);
        b.setImageResource(res);
        b.setColorFilter(color);
        if (label != null) b.setContentDescription(label);
        TypedValue ripple = new TypedValue();
        ctx.getTheme().resolveAttribute(android.R.attr.selectableItemBackgroundBorderless, ripple, true);
        b.setBackgroundResource(ripple.resourceId);
        if (click != null) b.setOnClickListener(v -> click.run());
        b.setLayoutParams(new LinearLayout.LayoutParams(dp(44), dp(44)));
        return b;
    }

    /** Runs `action` off the UI thread (starting a server takes a while), saying so meanwhile and on a failure,
     * then `then`. */
    private Runnable work(Action action, Runnable then) {
        return () -> {
            android.widget.Toast.makeText(getContext(), "Working on it…", android.widget.Toast.LENGTH_SHORT).show();
            new Thread(() -> {
                Exception failed = null;
                try { action.run(); } catch (Exception e) { failed = e; }
                Exception f = failed;
                post(() -> {
                    if (f != null) android.widget.Toast.makeText(getContext(), f.getMessage(), android.widget.Toast.LENGTH_LONG).show();
                    then.run();
                });
            }).start();
        };
    }

    private Runnable confirm(Context ctx, String message, String yes, Runnable go) {
        return () -> new android.app.AlertDialog.Builder(ctx)
            .setMessage(message)
            .setPositiveButton(yes, (d, w) -> go.run())
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** A folder as shown: its path under the home folder (/home/me/code/x -> code/x). */
    private static String folder(String cwd) {
        String s = cwd.replaceFirst("^/home/[^/]+/?", "").replaceFirst("^/root/?", "");
        return s.isEmpty() ? "~ (home)" : s;
    }

    /** " · 5 min ago" for a time in ms; empty for 0. */
    private static String when(long ms) {
        return ms <= 0 ? "" : " · " + agoMs(ms);
    }

    private static String ago(String iso) {
        try { return agoMs(java.time.Instant.parse(iso).toEpochMilli()); } catch (Exception e) { return ""; }
    }

    private static String agoMs(long ms) {
        long s = Math.max(0, (System.currentTimeMillis() - ms) / 1000);
        return s < 60 ? "just now" : s < 3600 ? (s / 60) + " min ago" : s < 86400 ? (s / 3600) + " h ago" : (s / 86400) + " d ago";
    }

    private static GradientDrawable round(int color, int radius) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(radius);
        return d;
    }

    private int dp(float v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private static TextView text(Context c, String s, float sp, int color) {
        TextView t = new TextView(c);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        return t;
    }
}
