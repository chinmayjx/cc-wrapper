package io.github.ccwrapper;

import android.app.Dialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Message;
import android.graphics.Color;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.FrameLayout;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeWebChromeClient;

/**
 * window.open() support. "Continue with Google" opens Google in a popup that reports back to
 * claude.ai through window.opener; without real popups Google loads in place of claude.ai and fails.
 * Popups for sign-in hosts open in a full-screen dialog; any other new window goes to the browser.
 */
class PopupWindows extends BridgeWebChromeClient {
    private final Bridge bridge;

    PopupWindows(Bridge bridge) {
        super(bridge);
        this.bridge = bridge;
        WebView main = bridge.getWebView();
        // Google's sign-in (in the popup and on claude.ai) relies on its third-party cookies.
        CookieManager.getInstance().setAcceptThirdPartyCookies(main, true);
        WebSettings s = main.getSettings();
        s.setSupportMultipleWindows(true);
        s.setJavaScriptCanOpenWindowsAutomatically(true);
    }

    static boolean staysInApp(Uri uri) {
        String host = uri.getHost();
        if (host == null) return false;
        // Same set as allowNavigation in capacitor.config.ts.
        return host.equals("claude.ai") || host.endsWith(".claude.ai") || host.endsWith(".anthropic.com")
            || host.endsWith(".claude.com") || host.endsWith(".google.com") || host.equals("accounts.youtube.com")
            || host.equals("accounts.google.co.in");
    }

    @Override
    public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture, Message resultMsg) {
        WebView popup = new WebView(view.getContext());
        WebSettings s = popup.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setUserAgentString(view.getSettings().getUserAgentString());
        CookieManager.getInstance().setAcceptThirdPartyCookies(popup, true);

        Dialog dialog = new Dialog(view.getContext(), android.R.style.Theme_DeviceDefault_NoActionBar);
        popup.setWebViewClient(new WebViewClient() {
            private boolean firstLoad = true;

            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                Uri uri = req.getUrl();
                if (staysInApp(uri)) {
                    firstLoad = false;
                    return false;
                }
                // A plain target=_blank link (GitHub, docs...): hand it to the browser and drop the empty popup.
                bridge.getActivity().startActivity(new Intent(Intent.ACTION_VIEW, uri));
                if (firstLoad) dialog.dismiss();
                return true;
            }
        });
        popup.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onCloseWindow(WebView w) {
                dialog.dismiss();
            }
        });
        // Full screen, clear of the status/navigation bars and the keyboard (password fields).
        FrameLayout root = new FrameLayout(view.getContext());
        root.setBackgroundColor(Color.parseColor("#1f1e1d"));
        root.addView(popup, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        ViewCompat.setOnApplyWindowInsetsListener(root, (v, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout() | WindowInsetsCompat.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsetsCompat.CONSUMED;
        });
        dialog.setContentView(root, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        dialog.setOnDismissListener(d -> popup.destroy());
        Window w = dialog.getWindow();
        if (w != null) {
            w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
            WindowCompat.setDecorFitsSystemWindows(w, false);
        }
        dialog.show();

        ((WebView.WebViewTransport) resultMsg.obj).setWebView(popup);
        resultMsg.sendToTarget();
        return true;
    }
}
