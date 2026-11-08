package io.github.ccwrapper;

import android.content.Intent;
import android.net.Uri;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.firebase.messaging.FirebaseMessaging;

/**
 * The app's small native bridge for inject.js, and the parsing of "<scheme>://session/<id>" links that open a
 * Claude Code session in the app (from a notification tap or any other app).
 *
 * Links to other sites need nothing here: hosts outside capacitor.config.ts' allowNavigation open through a
 * normal ACTION_VIEW intent, so the browser (or whichever app claims the link) handles them.
 */
@CapacitorPlugin(name = "LinkRouter")
public class LinkRouter extends Plugin {

    /**
     * From inject.js: a message was just sent in this session. Tells the push server (HUB_URL in
     * android/.env) which install the session was last used from, so it can send that session's notifications
     * to this phone only. Does nothing without a push server or Firebase.
     */
    @PluginMethod
    public void reportActivity(PluginCall call) {
        String session = call.getString("session");
        if (session == null || !session.matches("session_[A-Za-z0-9]+")) {
            call.reject("no session");
            return;
        }
        call.resolve();
        if (BuildConfig.HUB_URL.isEmpty() || !CCMApplication.pushReady(getContext())) return;
        String app = getContext().getPackageName();
        FirebaseMessaging.getInstance().getToken().addOnSuccessListener(token ->
            new Thread(() -> PushService.post("/api/activity", "session", session, "token", token, "app", app)).start());
    }

    /** "<scheme>://session/<claude.ai session id>", e.g. from a notification tap, as the claude.ai URL to load. */
    static String sessionUrl(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data == null || !BuildConfig.LINK_SCHEME.equals(data.getScheme()) || !"session".equals(data.getHost())) return null;
        String id = data.getLastPathSegment();
        if (id == null || !id.matches("session_[A-Za-z0-9]+")) return null;
        return "https://claude.ai/code/" + id;
    }
}
