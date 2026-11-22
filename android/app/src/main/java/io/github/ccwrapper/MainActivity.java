package io.github.ccwrapper;

import android.Manifest;
import android.content.Intent;
import android.content.res.Configuration;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.widget.Toast;
import androidx.activity.OnBackPressedCallback;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.webkit.ScriptHandler;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;
import com.getcapacitor.WebViewListener;
import com.google.firebase.messaging.FirebaseMessaging;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import org.json.JSONObject;

public class MainActivity extends BridgeActivity implements SettingsTray.Host {
    private static final String TAG = "CCMobile";
    private static final String ORIGIN = "https://claude.ai";
    private static final int MIC_PERMISSION = 7;

    private SettingsTray tray;
    private final OnBackPressedCallback closeTray = new OnBackPressedCallback(false) {
        @Override
        public void handleOnBackPressed() {
            if (tray != null) tray.back();
        }
    };
    private android.view.View handle; // the pill at the top that shows and hides the overlay

    private String script;
    private String pendingSessionPath;
    private boolean pageLoaded;
    private ScriptHandler scriptHandler;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(LinkRouter.class);
        super.onCreate(savedInstanceState);
        // Google refuses sign-in from WebViews, which it spots by "; wv" in the user agent.
        // Without it the app passes as Chrome, so "Continue with Google" works in place.
        android.webkit.WebSettings settings = getBridge().getWebView().getSettings();
        settings.setUserAgentString(settings.getUserAgentString().replace("; wv)", ")").replaceFirst(" Version/[\\d.]+", ""));
        keepAboveKeyboard(getBridge().getWebView());
        getBridge().setWebViewClient(new OfflineOnlyWhenOffline(getBridge()));
        // "Continue with Google" runs in a popup that reports back to claude.ai through window.opener.
        getBridge().getWebView().setWebChromeClient(new PopupWindows(getBridge()));
        try {
            installScript(buildScript(readAsset("inject/inject.css"), readAsset("inject/inject.js")));
        } catch (IOException e) {
            Log.e(TAG, "inject assets missing", e);
        }
        openSessionLink(getIntent());
        addTray();
        getOnBackPressedDispatcher().addCallback(this, closeTray);

        // Session notifications (when this build has Firebase values): show them (asks once on Android 13+) and
        // keep the push server's copy of our token fresh.
        if (CCMApplication.pushReady(this)) {
            PushService.createChannel(this);
            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[] { Manifest.permission.POST_NOTIFICATIONS }, 1);
            }
            FirebaseMessaging.getInstance().getToken().addOnSuccessListener(token -> PushService.register(this, token));
        }

        boolean injectOnLoad = !WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT);
        getBridge().addWebViewListener(new WebViewListener() {
            @Override
            public void onPageLoaded(WebView view) {
                boolean onClaude = view.getUrl() != null && view.getUrl().startsWith(ORIGIN);
                pageLoaded = onClaude;
                if (injectOnLoad && script != null && onClaude) view.evaluateJavascript(script, null);
                if (pendingSessionPath != null && onClaude) {
                    routeTo(view, pendingSessionPath);
                    pendingSessionPath = null;
                }
            }
        });
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        openSessionLink(intent);
    }

    /**
     * A notification tap (or any ccm://session/<id> link) opens its session without reloading claude.ai, which keeps
     * itself in sync: nothing to do if that session is already showing; another session is switched to with
     * client-side routing, like claude.ai's own sidebar. Only a cold start loads the URL.
     */
    private void openSessionLink(Intent intent) {
        String url = LinkRouter.sessionUrl(intent);
        if (url != null) onOpenChat(url);
    }

    /** A chat from the tray's list, or a notification: https://claude.ai/code/session_... */
    @Override
    public void onOpenChat(String url) {
        if (tray != null) tray.close();
        WebView webView = getBridge().getWebView();
        String current = webView.getUrl();
        String path = Uri.parse(url).getPath();
        if (!pageLoaded || current == null || !current.startsWith(ORIGIN + "/")) {
            // Cold start: claude.ai is still loading, so switch once it has.
            pendingSessionPath = path;
        } else if (!path.equals(Uri.parse(current).getPath())) {
            routeTo(webView, path);
        }
    }

    private static void routeTo(WebView webView, String path) {
        webView.evaluateJavascript("history.pushState(history.state,'','" + path + "');"
            + "dispatchEvent(new PopStateEvent('popstate',{state:history.state}));", null);
    }

    /**
     * Capacitor swaps in offline.html for any HTTP error status on a page load, not only for a dead network.
     * claude.ai sometimes answers the first load with an error status (a Cloudflare check its own page then
     * clears), which showed "Can't reach claude.ai" until Retry. Only real network failures do that now.
     */
    private static class OfflineOnlyWhenOffline extends BridgeWebViewClient {
        OfflineOnlyWhenOffline(Bridge bridge) {
            super(bridge);
        }

        @Override
        public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
            if (request.isForMainFrame()) {
                Log.i(TAG, "page load answered " + response.getStatusCode() + ": " + request.getUrl() + " (left to the page)");
            }
        }
    }

    /**
     * Edge-to-edge (Android 15+) ignores adjustResize, and Capacitor's own insets listener only reserves the
     * system bars, so the keyboard covered the composer. Same margins as Capacitor's, but the bottom one is
     * whichever is taller: the navigation bar or the keyboard.
     */
    private static void keepAboveKeyboard(WebView webView) {
        ViewCompat.setOnApplyWindowInsetsListener(webView, (v, windowInsets) -> {
            Insets bars = windowInsets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            Insets ime = windowInsets.getInsets(WindowInsetsCompat.Type.ime());
            ViewGroup.MarginLayoutParams mlp = (ViewGroup.MarginLayoutParams) v.getLayoutParams();
            mlp.leftMargin = bars.left;
            mlp.topMargin = bars.top;
            mlp.rightMargin = bars.right;
            mlp.bottomMargin = Math.max(bars.bottom, ime.bottom);
            v.setLayoutParams(mlp);
            return WindowInsetsCompat.CONSUMED;
        });
    }

    @Override
    public void onResume() {
        super.onResume();
        // Kindle dictation, if switched on: its foreground service may only take the mic while the app is in front.
        MicService.ensureRunning(this);
        if (tray != null) tray.refresh();
        // Debug build with INJECT_DEV_URL set (android/.env): pick up inject.js/inject.css edits from that server each time the app comes to the front.
        if (!BuildConfig.INJECT_DEV_URL.isEmpty()) new Thread(this::pullDevScript).start();
    }

    private void pullDevScript() {
        try {
            String fresh = buildScript(fetch(BuildConfig.INJECT_DEV_URL + "inject.css"), fetch(BuildConfig.INJECT_DEV_URL + "inject.js"));
            if (fresh.equals(script)) return;
            runOnUiThread(() -> {
                installScript(fresh);
                getBridge().getWebView().reload();
            });
            Log.i(TAG, "dev inject updated, reloading");
        } catch (IOException e) {
            Log.w(TAG, "dev inject fetch failed, keeping current: " + e);
        }
    }

    /** Runs the script before claude.ai's own scripts on every page load, so the CSS is in place before first paint. */
    private void installScript(String js) {
        script = js;
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) return;
        if (scriptHandler != null) scriptHandler.remove();
        scriptHandler = WebViewCompat.addDocumentStartJavaScript(getBridge().getWebView(), js, Collections.singleton(ORIGIN));
    }

    /** inject.css is handed to inject.js as window.__CCM_CSS. */
    private static String buildScript(String css, String js) {
        return "window.__CCM_CSS=" + JSONObject.quote(css) + ";\n" + js;
    }

    private String readAsset(String path) throws IOException {
        try (InputStream in = getAssets().open(path)) {
            return readAll(in);
        }
    }

    private static String fetch(String url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(4000);
        c.setReadTimeout(4000);
        c.setUseCaches(false);
        try {
            if (c.getResponseCode() != 200) throw new IOException(url + " -> " + c.getResponseCode());
            try (InputStream in = c.getInputStream()) {
                return readAll(in);
            }
        } finally {
            c.disconnect();
        }
    }

    private static String readAll(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
        return out.toString(StandardCharsets.UTF_8.name());
    }

    // ---- the settings tray --------------------------------------------------------------------------------------

    private void addTray() {
        tray = new SettingsTray(this, this);
        addContentView(tray, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        addHandle();
    }

    /**
     * The handle: a small pill at the top centre of the page, just below the status bar, above the page and the
     * overlay. A tap shows the overlay, the next hides it. A tap, not a pull: pulls from the top edge are the
     * system's (the notification shade), and a pull on the page fought its own scrolling.
     */
    private void addHandle() {
        if (handle != null && handle.getParent() != null) ((ViewGroup) handle.getParent()).removeView(handle);
        float d = getResources().getDisplayMetrics().density;
        android.widget.FrameLayout h = new android.widget.FrameLayout(this);
        android.view.View pill = new android.view.View(this);
        android.graphics.drawable.GradientDrawable g = new android.graphics.drawable.GradientDrawable();
        boolean dark = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        g.setColor(dark ? 0x99A6A39A : 0x996B6A65);
        g.setCornerRadius(3 * d);
        pill.setBackground(g);
        h.addView(pill, new android.widget.FrameLayout.LayoutParams(Math.round(36 * d), Math.round(5 * d),
            android.view.Gravity.TOP | android.view.Gravity.CENTER_HORIZONTAL));
        ((android.widget.FrameLayout.LayoutParams) pill.getLayoutParams()).topMargin = Math.round(4 * d);
        h.setContentDescription("Claude servers");
        h.setOnClickListener(v -> tray.toggle());
        android.widget.FrameLayout.LayoutParams lp = new android.widget.FrameLayout.LayoutParams(Math.round(72 * d), Math.round(22 * d),
            android.view.Gravity.TOP | android.view.Gravity.CENTER_HORIZONTAL);
        addContentView(h, lp);
        h.setElevation(20 * d);
        ViewCompat.setOnApplyWindowInsetsListener(h, (v, in) -> {
            Insets bars = in.getInsets(WindowInsetsCompat.Type.statusBars() | WindowInsetsCompat.Type.displayCutout());
            android.view.ViewGroup.MarginLayoutParams m = (android.view.ViewGroup.MarginLayoutParams) v.getLayoutParams();
            if (m.topMargin != bars.top) { m.topMargin = bars.top; v.setLayoutParams(m); }
            return in;
        });
        ViewCompat.requestApplyInsets(h);
        handle = h;
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        // Dark/light or size changed (handled without recreating the activity): rebuild the tray to match.
        if (tray != null) {
            ((ViewGroup) tray.getParent()).removeView(tray);
            closeTray.setEnabled(false);
            addTray();
        }
    }

    @Override
    public String currentUrl() {
        return getBridge().getWebView().getUrl();
    }

    @Override
    public void onReload() {
        tray.close();
        getBridge().getWebView().reload();
    }

    @Override
    public void onTrayShown(boolean shown) {
        closeTray.setEnabled(shown);
    }

    @Override
    public void onDictationSwitch(boolean on) {
        if (!on) {
            MicService.turn(this, false);
        } else if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            askForNotifications();
            MicService.turn(this, true);
        } else {
            requestPermissions(new String[] { Manifest.permission.RECORD_AUDIO }, MIC_PERMISSION);
            return; // the switch stays on until the answer (onRequestPermissionsResult)
        }
        tray.refresh();
    }

    /** The service's notification (Android 13+); dictation works without it, the notification just stays hidden. */
    private void askForNotifications() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] { Manifest.permission.POST_NOTIFICATIONS }, 1);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != MIC_PERMISSION) return;
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            askForNotifications();
            MicService.turn(this, true);
        } else {
            Toast.makeText(this, "Kindle dictation needs the microphone", Toast.LENGTH_SHORT).show();
        }
        if (tray != null) tray.refresh();
    }
}
