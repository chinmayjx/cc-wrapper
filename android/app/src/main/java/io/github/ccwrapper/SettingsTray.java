package io.github.ccwrapper;

import android.content.Context;
import android.content.res.ColorStateList;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.VelocityTracker;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import androidx.appcompat.widget.SwitchCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;

/**
 * The settings tray: pulled down from the top bar of the page (MainActivity starts the pull), it slides over the
 * page from the top. Swipe it up, tap below it or press Back to close it. Built in code, following the system's
 * dark or light mode like claude.ai does. Each setting is a row in {@link #rows}; Kindle dictation is the first.
 *
 * Above the settings, your server's Claude servers (GET HUB_URL/api/sessions/servers): one Remote Control server per
 * folder, which starts a chat's Claude when a message comes for it, so a chat works whenever its folder's server is
 * up. First the chat on screen and whether its server is running (Start brings it up, with this chat); then every
 * folder's server, running or not (Start on a stopped one). Asked for each time the tray is pulled down, showing the
 * last answer meanwhile.
 */
final class SettingsTray extends FrameLayout {
    interface Host {
        /** The dictation switch was flipped by the user; the host deals with permissions, then calls refresh(). */
        void onDictationSwitch(boolean on);
        /** The tray opened or closed (for the Back handler). */
        void onTrayShown(boolean shown);
        /** The refresh button: reload the page. */
        void onReload();
        /** A chat in the list was tapped: show it (https://claude.ai/code/session_...). */
        void onOpenChat(String url);
        /** The page's address, to mark the chat on screen. */
        String currentUrl();
    }

    private static final int ANIM_MS = 220;
    private static final int SCRIM_ALPHA = 110;

    private final Host host;
    private final LinearLayout panel;
    private final LinearLayout rows;
    private final SwitchCompat dictation;
    private final TextView dictationStatus;
    private final int touchSlop;
    private final boolean dark;
    private boolean updating;
    private boolean open;
    private float panelHeight;
    private final Runnable stateListener = () -> post(this::refresh);

    // The servers: this chat's, the list, a line saying what's going on, and the last answer (shown while a new one comes).
    private static final int RUNNING = 0xFF5FB87F;
    private final LinearLayout thisChat, servers;
    private final android.widget.ScrollView serversScroll;
    private final TextView serversStatus;
    private final int surface, onSurface, muted, accent, field;
    private static org.json.JSONObject last;
    private int generation;

    // A drag on the open tray (swipe up to close).
    private float downY;
    private boolean dragging;
    private boolean downOutside;
    private boolean inList; // the touch began on the servers list
    private VelocityTracker velocity;

    SettingsTray(Context context, Host host) {
        super(context);
        this.host = host;
        touchSlop = ViewConfiguration.get(context).getScaledTouchSlop();
        dark = (context.getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        surface = dark ? 0xFF30302E : 0xFFFAF9F5;
        onSurface = dark ? 0xFFF5F4EE : 0xFF141413;
        muted = dark ? 0xFFA6A39A : 0xFF6B6A65;
        accent = 0xFFD97757;
        field = dark ? 0xFF3D3D3A : 0xFFEDEBE3;

        setVisibility(GONE);
        setClickable(true);

        panel = new LinearLayout(context);
        panel.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(surface);
        float r = dp(20);
        bg.setCornerRadii(new float[] { 0, 0, 0, 0, r, r, r, r });
        panel.setBackground(bg);
        panel.setElevation(dp(8));
        panel.setClickable(true); // touches on the panel don't fall through to "tap outside"
        addView(panel, new LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP));
        // The list fills in after the pull starts: the tray's height (for closing and the scrim) follows it.
        panel.addOnLayoutChangeListener((v, l, t, r2, b, ol, ot, or, ob) -> { if (b - t > 0) panelHeight = b - t; });

        // The header: the title, and at its right edge a button that reloads the page.
        LinearLayout header = new LinearLayout(context);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = text(context, "Claude servers", 20, onSurface);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        header.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        android.widget.ImageButton reload = new android.widget.ImageButton(context);
        reload.setImageResource(R.drawable.ic_refresh);
        reload.setColorFilter(onSurface);
        reload.setContentDescription("Reload the page");
        android.util.TypedValue ripple = new android.util.TypedValue();
        context.getTheme().resolveAttribute(android.R.attr.selectableItemBackgroundBorderless, ripple, true);
        reload.setBackgroundResource(ripple.resourceId);
        reload.setOnClickListener(v -> host.onReload());
        header.addView(reload, new LinearLayout.LayoutParams(dp(48), dp(48)));
        panel.addView(header, margins(dp(20), dp(4), dp(8), dp(4)));

        serversStatus = text(context, "", 13, muted);
        panel.addView(serversStatus, margins(dp(20), 0, dp(20), dp(4)));
        thisChat = new LinearLayout(context);
        thisChat.setOrientation(LinearLayout.VERTICAL);
        panel.addView(thisChat, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        serversScroll = new android.widget.ScrollView(context);
        servers = new LinearLayout(context);
        servers.setOrientation(LinearLayout.VERTICAL);
        serversScroll.addView(servers);
        panel.addView(serversScroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView settings = text(context, "Settings", 13, muted);
        settings.setTypeface(Typeface.DEFAULT_BOLD);
        panel.addView(settings, margins(dp(20), dp(14), dp(20), dp(2)));

        rows = new LinearLayout(context);
        rows.setOrientation(LinearLayout.VERTICAL);
        panel.addView(rows, margins(dp(20), 0, dp(20), 0));

        // Kindle dictation
        dictation = new SwitchCompat(context);
        dictation.setText("Kindle dictation (use this phone's mic)");
        dictation.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        dictation.setTextColor(onSurface);
        dictation.setMinHeight(dp(48));
        int[][] states = { { android.R.attr.state_checked }, {} };
        dictation.setThumbTintList(new ColorStateList(states, new int[] { accent, dark ? 0xFFC2C0B6 : 0xFFFFFFFF }));
        dictation.setTrackTintList(new ColorStateList(states, new int[] { (accent & 0x00FFFFFF) | 0x80000000, dark ? 0xFF5A5954 : 0xFFC9C7BE }));
        dictation.setOnCheckedChangeListener((b, on) -> {
            if (!updating) host.onDictationSwitch(on);
        });
        rows.addView(dictation, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        TextView explain = text(context, MicService.configured()
            ? "Your hub's Kindle panel records through this phone, locked or not; the mic is only on while you dictate there."
            : "Needs HUB_URL in android/.env at build time (see the README).", 13, muted);
        rows.addView(explain);
        dictationStatus = text(context, "", 13, accent);
        rows.addView(dictationStatus, margins(0, dp(4), 0, 0));
        if (!MicService.configured()) dictation.setEnabled(false);

        // A grab handle: the tray closes by swiping it up.
        View handle = new View(context);
        GradientDrawable hb = new GradientDrawable();
        hb.setColor(muted);
        hb.setCornerRadius(dp(2));
        handle.setBackground(hb);
        handle.setAlpha(0.5f);
        LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(dp(36), dp(4));
        hp.gravity = Gravity.CENTER_HORIZONTAL;
        hp.setMargins(0, dp(16), 0, dp(10));
        panel.addView(handle, hp);
        refresh();
    }

    boolean isShowing() {
        return getVisibility() == VISIBLE;
    }

    /** Shows the switch as the setting stands. Safe to call any time on the UI thread. */
    void refresh() {
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

    // ---- pulling down (driven by MainActivity) ----------------------------------------------------------------

    /** A pull from the top bar started: show the tray just above the screen's top. */
    void beginPull() {
        panel.animate().cancel();
        refresh();
        loadServers();
        WindowInsetsCompat insets = ViewCompat.getRootWindowInsets(this);
        Insets bars = insets != null
            ? insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout())
            : Insets.NONE;
        panel.setPadding(bars.left, bars.top + dp(12), bars.right, 0);
        int width = ((View) getParent()).getWidth();
        panel.measure(MeasureSpec.makeMeasureSpec(width, MeasureSpec.EXACTLY), MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED));
        panelHeight = panel.getMeasuredHeight();
        setVisibility(VISIBLE);
        place(-panelHeight);
        host.onTrayShown(true);
    }

    /** The finger is dy pixels below where the pull started. */
    void pullTo(float dy) {
        place(Math.min(0, -panelHeight + dy));
    }

    /** The finger lifted: open if pulled a third of the way or flung down, else back up. */
    void endPull(float velocityY) {
        float shown = panelHeight + panel.getTranslationY();
        if (velocityY > dp(600) || (velocityY > -dp(600) && shown > panelHeight / 3)) open();
        else close();
    }

    void open() {
        open = true;
        settle(0, null);
    }

    void close() {
        open = false;
        if (!isShowing()) return;
        settle(-panelHeight, () -> {
            if (!open) {
                setVisibility(GONE);
                host.onTrayShown(false);
            }
        });
    }

    private void settle(float to, Runnable end) {
        panel.animate().cancel();
        panel.animate().translationY(to).setDuration(ANIM_MS)
            .setUpdateListener(a -> scrim())
            .withEndAction(end)
            .start();
    }

    private void place(float y) {
        panel.setTranslationY(y);
        scrim();
    }

    private void scrim() {
        float shown = panelHeight <= 0 ? 0 : Math.max(0, Math.min(1, 1 + panel.getTranslationY() / panelHeight));
        setBackgroundColor(Color.argb((int) (SCRIM_ALPHA * shown), 0, 0, 0));
    }

    // ---- the open tray: swipe up, or tap below it, to close -----------------------------------------------------

    @Override
    public boolean onInterceptTouchEvent(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downY = e.getY();
                dragging = false;
                android.graphics.Rect r = new android.graphics.Rect();
                inList = serversScroll.getGlobalVisibleRect(r) && r.contains((int) e.getRawX(), (int) e.getRawY());
                track(e, true);
                break;
            case MotionEvent.ACTION_MOVE:
                track(e, false);
                if (inList && serversScroll.canScrollVertically(1)) break; // the list scrolls; it isn't a close
                if (downY - e.getY() > touchSlop) { // upward: take the gesture from the switch
                    dragging = true;
                    return true;
                }
                break;
        }
        return false;
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downY = e.getY();
                downOutside = e.getY() > panel.getBottom() + panel.getTranslationY();
                dragging = false;
                track(e, true);
                return true;
            case MotionEvent.ACTION_MOVE:
                track(e, false);
                if (!dragging && downY - e.getY() > touchSlop) dragging = true;
                if (dragging) place(Math.min(0, e.getY() - downY + touchSlop));
                return true;
            case MotionEvent.ACTION_UP:
                track(e, false);
                if (dragging) {
                    velocity.computeCurrentVelocity(1000);
                    float v = velocity.getYVelocity();
                    if (v < -dp(600) || -panel.getTranslationY() > panelHeight / 4) close();
                    else open();
                } else if (downOutside) {
                    close();
                }
                dragging = false;
                return true;
            case MotionEvent.ACTION_CANCEL:
                if (dragging) open();
                dragging = false;
                return true;
        }
        return true;
    }

    private void track(MotionEvent e, boolean reset) {
        if (velocity == null || reset) {
            if (velocity != null) velocity.recycle();
            velocity = VelocityTracker.obtain();
        }
        // Raw coordinates: the panel moves under the finger.
        MotionEvent copy = MotionEvent.obtain(e);
        copy.setLocation(e.getRawX(), e.getRawY());
        velocity.addMovement(copy);
        copy.recycle();
    }

    // ---- the servers ------------------------------------------------------------------------------------------

    /** The chat on screen's claude.ai id, or null. */
    private String session() {
        String url = host.currentUrl();
        java.util.regex.Matcher m = url == null ? null : java.util.regex.Pattern.compile("/code/(session_[A-Za-z0-9]+)").matcher(url);
        return m != null && m.find() ? m.group(1) : null;
    }

    private void loadServers() {
        int g = ++generation;
        String session = session();
        if (last != null) show(last, session);
        else { thisChat.removeAllViews(); servers.removeAllViews(); serversStatus.setText("Asking your server…"); serversStatus.setVisibility(VISIBLE); }
        new Thread(() -> {
            try {
                org.json.JSONObject r = LinkRouter.servers(session);
                post(() -> { if (g == generation) { last = r; show(r, session); } });
            } catch (Exception e) {
                post(() -> {
                    if (g != generation) return;
                    serversStatus.setText(e.getMessage());
                    serversStatus.setVisibility(VISIBLE);
                });
            }
        }).start();
    }

    private void show(org.json.JSONObject r, String session) {
        thisChat.removeAllViews();
        servers.removeAllViews();
        serversStatus.setVisibility(GONE);
        org.json.JSONObject chat = r.optJSONObject("chat");
        // An answer from before a switch of chat says nothing of this one.
        if (chat != null && !chat.optString("session").equals(session)) chat = null;
        String here = chat == null ? null : chat.optString("cwd");
        if (chat != null) thisChat.addView(chatCard(chat));
        else if (session != null && r == last && r.has("servers")) {
            TextView none = text(getContext(), "This chat isn't on your server.", 13, muted);
            thisChat.addView(none, margins(dp(20), 0, dp(20), dp(6)));
        }
        org.json.JSONArray list = r.optJSONArray("servers");
        if (list == null) return;
        for (int i = 0; i < list.length(); i++) {
            org.json.JSONObject s = list.optJSONObject(i);
            if (s != null) servers.addView(serverRow(s, s.optString("cwd").equals(here)));
        }
        // At most 55% of the screen: the rest scrolls.
        int max = getResources().getDisplayMetrics().heightPixels * 55 / 100;
        servers.measure(MeasureSpec.makeMeasureSpec(Math.max(1, getWidth()), MeasureSpec.EXACTLY), MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED));
        ViewGroup.LayoutParams lp = serversScroll.getLayoutParams();
        lp.height = Math.min(servers.getMeasuredHeight(), max);
        serversScroll.setLayoutParams(lp);
    }

    /** This chat: its folder, and whether its server is up (then messages reach it); Start if it isn't. */
    private View chatCard(org.json.JSONObject chat) {
        Context ctx = getContext();
        boolean up = chat.optBoolean("server");
        LinearLayout card = new LinearLayout(ctx);
        card.setGravity(Gravity.CENTER_VERTICAL);
        card.setPadding(dp(14), dp(10), dp(10), dp(10));
        card.setBackground(round(field, dp(12)));
        View dot = dot(ctx, up);
        card.addView(dot, dotParams());
        LinearLayout words = new LinearLayout(ctx);
        words.setOrientation(LinearLayout.VERTICAL);
        words.addView(text(ctx, "This chat · " + folder(chat.optString("cwd")), 15, onSurface));
        TextView sub = text(ctx, up ? "Its server is running: messages reach it" : "Its server is stopped", 12, muted);
        words.addView(sub);
        card.addView(words, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        if (!up) card.addView(iconButton(ctx, R.drawable.ic_play, accent, "Start", () -> LinkRouter.connect(chat.optString("session")), null));
        LinearLayout.LayoutParams lp = margins(dp(12), dp(2), dp(12), dp(8));
        card.setLayoutParams(lp);
        return card;
    }

    /** A folder's server: running or not, how many chats it's serving now; Start if stopped. */
    private View serverRow(org.json.JSONObject s, boolean here) {
        Context ctx = getContext();
        boolean up = s.optBoolean("running");
        int n = s.optInt("chats");
        LinearLayout row = new LinearLayout(ctx);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(20), dp(8), dp(12), dp(8));
        if (here) row.setBackgroundColor(field);
        row.addView(dot(ctx, up), dotParams());
        LinearLayout words = new LinearLayout(ctx);
        words.setOrientation(LinearLayout.VERTICAL);
        TextView name = text(ctx, folder(s.optString("cwd")) + (here ? "  (this chat)" : ""), 15, onSurface);
        name.setSingleLine(true);
        name.setEllipsize(android.text.TextUtils.TruncateAt.MIDDLE);
        words.addView(name);
        String when = ago(s.optLong("active"));
        words.addView(text(ctx, (up ? "running" + (n > 0 ? " · " + n + (n == 1 ? " chat" : " chats") + " live" : "") : "stopped")
            + (when.isEmpty() ? "" : " · " + when), 12, muted));
        row.addView(words, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        String cwd = s.optString("cwd");
        if (!up) row.addView(iconButton(ctx, R.drawable.ic_play, accent, "Start", () -> LinkRouter.startServer(cwd), null));
        row.addView(iconButton(ctx, R.drawable.ic_delete, muted, "Delete",
            () -> LinkRouter.deleteServer(cwd),
            "Delete the server of " + folder(cwd) + "?\n\nIt stops, with its chats' Claudes" + (here ? ", this chat's too" : "")
                + ", and leaves the list until a chat starts there again."));
        return row;
    }

    interface Action { void run() throws Exception; }

    /**
     * An icon button on a row: runs `action` off the UI thread (starting a server takes a while), with a spinner in
     * its place meanwhile, then asks for the servers again. With `confirm`, asks first. A failure shows as a toast.
     */
    private View iconButton(Context ctx, int icon, int color, String label, Action action, String confirm) {
        FrameLayout box = new FrameLayout(ctx);
        android.widget.ImageButton b = new android.widget.ImageButton(ctx);
        b.setImageResource(icon);
        b.setColorFilter(color);
        b.setContentDescription(label);
        TypedValue ripple = new TypedValue();
        ctx.getTheme().resolveAttribute(android.R.attr.selectableItemBackgroundBorderless, ripple, true);
        b.setBackgroundResource(ripple.resourceId);
        android.widget.ProgressBar spin = new android.widget.ProgressBar(ctx);
        spin.setIndeterminateTintList(ColorStateList.valueOf(color));
        spin.setVisibility(GONE);
        box.addView(b, new LayoutParams(dp(44), dp(44)));
        box.addView(spin, new LayoutParams(dp(22), dp(22), Gravity.CENTER));
        Runnable run = () -> {
            b.setVisibility(INVISIBLE);
            spin.setVisibility(VISIBLE);
            new Thread(() -> {
                Exception failed = null;
                try { action.run(); } catch (Exception e) { failed = e; }
                Exception f = failed;
                post(() -> {
                    spin.setVisibility(GONE);
                    b.setVisibility(VISIBLE);
                    if (f != null) android.widget.Toast.makeText(ctx, f.getMessage(), android.widget.Toast.LENGTH_LONG).show();
                    loadServers();
                });
            }).start();
        };
        b.setOnClickListener(v -> {
            if (confirm == null) { run.run(); return; }
            new android.app.AlertDialog.Builder(ctx)
                .setMessage(confirm)
                .setPositiveButton(label, (d, w) -> run.run())
                .setNegativeButton("Cancel", null)
                .show();
        });
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(dp(44), dp(44));
        p.leftMargin = dp(2);
        box.setLayoutParams(p);
        return box;
    }

    private View dot(Context ctx, boolean up) {
        View d = new View(ctx);
        d.setBackground(round(up ? RUNNING : muted, dp(5)));
        d.setAlpha(up ? 1f : 0.5f);
        return d;
    }

    private LinearLayout.LayoutParams dotParams() {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(dp(9), dp(9));
        p.rightMargin = dp(12);
        return p;
    }

    /** "5 min ago", "3 h ago", "2 d ago"; empty for 0 (never). */
    private static String ago(long ms) {
        if (ms <= 0) return "";
        long s = Math.max(0, (System.currentTimeMillis() - ms) / 1000);
        return s < 60 ? "just now" : s < 3600 ? (s / 60) + " min ago" : s < 86400 ? (s / 3600) + " h ago" : (s / 86400) + " d ago";
    }

    /** A folder as shown: its path under the home folder (~/code/personal/vault -> code/personal/vault). */
    private static String folder(String cwd) {
        String s = cwd.replaceFirst("^/home/[^/]+/?", "").replaceFirst("^/root/?", "");
        return s.isEmpty() ? "~ (home)" : s;
    }

    private static GradientDrawable round(int color, int radius) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(radius);
        return d;
    }

    // ---- small helpers ----------------------------------------------------------------------------------------

    private int dp(float v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private static TextView text(Context c, String s, int sp, int color) {
        TextView t = new TextView(c);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        return t;
    }

    private static LinearLayout.LayoutParams margins(int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.setMargins(l, t, r, b);
        return p;
    }
}
