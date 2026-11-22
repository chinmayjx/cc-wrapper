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
 * Above the settings, the chats on your server's machine (GET HUB_URL/api/sessions/chats), newest first: each says
 * whether its Claude is running there now, and a stopped one has Connect, which brings it back. A tap on one opens
 * it. The list is asked for each time the tray is pulled down, showing the last one meanwhile.
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

    // The chats: the list, a line saying what's going on, and the last list fetched (shown while a new one comes).
    private static final int RUNNING = 0xFF5FB87F;
    private final LinearLayout chats;
    private final android.widget.ScrollView chatsScroll;
    private final TextView chatsStatus;
    private final int surface, onSurface, muted, accent, field;
    private static org.json.JSONArray lastChats;
    private int generation;

    // A drag on the open tray (swipe up to close).
    private float downY;
    private boolean dragging;
    private boolean downOutside;
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
        TextView title = text(context, "Chats", 20, onSurface);
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

        chatsStatus = text(context, "", 13, muted);
        panel.addView(chatsStatus, margins(dp(20), 0, dp(20), dp(4)));
        chatsScroll = new android.widget.ScrollView(context);
        chats = new LinearLayout(context);
        chats.setOrientation(LinearLayout.VERTICAL);
        chatsScroll.addView(chats);
        panel.addView(chatsScroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

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
        loadChats();
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
                track(e, true);
                break;
            case MotionEvent.ACTION_MOVE:
                track(e, false);
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

    // ---- the chats ---------------------------------------------------------------------------------------------

    private void loadChats() {
        int g = ++generation;
        if (lastChats != null) showChats(lastChats);
        else { chats.removeAllViews(); chatsStatus.setText("Loading your chats…"); chatsStatus.setVisibility(VISIBLE); }
        new Thread(() -> {
            try {
                org.json.JSONArray list = LinkRouter.chats();
                post(() -> { if (g == generation) { lastChats = list; showChats(list); } });
            } catch (Exception e) {
                post(() -> {
                    if (g != generation) return;
                    chatsStatus.setText(e.getMessage());
                    chatsStatus.setVisibility(VISIBLE);
                });
            }
        }).start();
    }

    private void showChats(org.json.JSONArray list) {
        chats.removeAllViews();
        chatsStatus.setVisibility(list.length() == 0 ? VISIBLE : GONE);
        chatsStatus.setText("No chats on your server.");
        String current = host.currentUrl();
        for (int i = 0; i < list.length(); i++) {
            org.json.JSONObject c = list.optJSONObject(i);
            if (c != null) chats.addView(chatRow(c, current != null && current.startsWith(c.optString("url"))));
        }
        // At most half the screen: the rest scrolls.
        int max = getResources().getDisplayMetrics().heightPixels / 2;
        chats.measure(MeasureSpec.makeMeasureSpec(Math.max(1, getWidth()), MeasureSpec.EXACTLY), MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED));
        ViewGroup.LayoutParams lp = chatsScroll.getLayoutParams();
        lp.height = Math.min(chats.getMeasuredHeight(), max);
        chatsScroll.setLayoutParams(lp);
    }

    private View chatRow(org.json.JSONObject c, boolean here) {
        Context ctx = getContext();
        boolean running = c.optBoolean("running");
        LinearLayout row = new LinearLayout(ctx);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(20), dp(9), dp(12), dp(9));
        if (here) row.setBackgroundColor(field);
        else {
            TypedValue ripple = new TypedValue();
            ctx.getTheme().resolveAttribute(android.R.attr.selectableItemBackground, ripple, true);
            row.setBackgroundResource(ripple.resourceId);
        }
        String url = c.optString("url");
        row.setOnClickListener(v -> host.onOpenChat(url));

        View dot = new View(ctx);
        dot.setBackground(round(running ? RUNNING : muted, dp(5)));
        dot.setAlpha(running ? 1f : 0.5f);
        LinearLayout.LayoutParams dp9 = new LinearLayout.LayoutParams(dp(9), dp(9));
        dp9.rightMargin = dp(12);
        row.addView(dot, dp9);

        LinearLayout words = new LinearLayout(ctx);
        words.setOrientation(LinearLayout.VERTICAL);
        String t = c.optString("title");
        TextView name = text(ctx, t.isEmpty() ? "Untitled" : t, 15, onSurface);
        name.setSingleLine(true);
        name.setEllipsize(android.text.TextUtils.TruncateAt.END);
        words.addView(name);
        TextView sub = text(ctx, describe(c, running), 12, muted);
        sub.setSingleLine(true);
        words.addView(sub);
        row.addView(words, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        if (!running) {
            TextView connect = text(ctx, "Connect", 13, accent);
            connect.setTypeface(Typeface.DEFAULT_BOLD);
            connect.setPadding(dp(14), dp(7), dp(14), dp(7));
            connect.setBackground(round(field, dp(16)));
            connect.setOnClickListener(v -> {
                connect.setEnabled(false);
                connect.setText("Connecting…");
                new Thread(() -> {
                    try {
                        LinkRouter.connect(c.optString("session"));
                        post(() -> {
                            try { c.put("running", true); } catch (Exception ignored) {}
                            dot.setBackground(round(RUNNING, dp(5)));
                            dot.setAlpha(1f);
                            sub.setText(describe(c, true));
                            connect.setText("Connected");
                            postDelayed(() -> connect.setVisibility(GONE), 1500);
                        });
                    } catch (Exception e) {
                        post(() -> {
                            connect.setText(e.getMessage());
                            postDelayed(() -> { connect.setEnabled(true); connect.setText("Connect"); }, 4000);
                        });
                    }
                }).start();
            });
            LinearLayout.LayoutParams cp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            cp.leftMargin = dp(10);
            row.addView(connect, cp);
        }
        return row;
    }

    /** "cc-mobile · running · 5 min ago": the folder's name, the state, when it was last active. */
    private static String describe(org.json.JSONObject c, boolean running) {
        String cwd = c.optString("cwd"), folder = cwd.substring(cwd.lastIndexOf('/') + 1);
        String when = "";
        try {
            long s = (System.currentTimeMillis() - java.time.Instant.parse(c.optString("updated")).toEpochMilli()) / 1000;
            when = s < 60 ? "just now" : s < 3600 ? (s / 60) + " min ago" : s < 86400 ? (s / 3600) + " h ago" : (s / 86400) + " d ago";
        } catch (Exception ignored) {}
        return (folder.isEmpty() ? "" : folder + " · ") + (running ? "running" : "stopped") + (when.isEmpty() ? "" : " · " + when);
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
