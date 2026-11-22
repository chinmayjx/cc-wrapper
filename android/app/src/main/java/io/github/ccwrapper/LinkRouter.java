package io.github.ccwrapper;

import android.content.Intent;
import android.net.Uri;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.firebase.messaging.FirebaseMessaging;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

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

    /**
     * For the settings tray's chats: every chat on the server's machine, newest first, from GET
     * HUB_URL/api/sessions/chats: [{"session", "title", "cwd", "url", "running", "updated"}]. Throws with what's wrong.
     */
    static org.json.JSONArray chats() throws Exception {
        if (BuildConfig.HUB_URL.isEmpty()) throw new Exception("Needs HUB_URL in android/.env at build time (see the README).");
        String[] r = http("GET", BuildConfig.HUB_URL + "/api/sessions/chats", 5000, 20000);
        if (r == null) throw new Exception("Can't reach your server. Is the VPN on?");
        if (!r[0].equals("200")) throw new Exception("Your server couldn't list the chats (" + r[0] + ")");
        return new org.json.JSONArray(r[1]);
    }

    /** Connect: bring a chat back, with POST HUB_URL/api/sessions/<id>/connect. Throws with what's wrong. */
    static void connect(String session) throws Exception {
        if (session == null || !session.matches("session_[A-Za-z0-9]+") || BuildConfig.HUB_URL.isEmpty()) throw new Exception("Not a chat");
        String[] r = http("POST", BuildConfig.HUB_URL + "/api/sessions/" + session + "/connect", 8000, 60000);
        if (r == null) throw new Exception("Can't reach your server");
        if (!r[0].equals("200")) throw new Exception(r[0].equals("404") ? "Not one of your server's chats" : "Couldn't reconnect");
    }

    /** {status code, body}, or null when the server doesn't answer. A POST sends an empty JSON object. */
    private static String[] http(String method, String url, int connectMs, int readMs) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setRequestMethod(method);
            c.setConnectTimeout(connectMs);
            c.setReadTimeout(readMs);
            if (method.equals("POST")) {
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                try (OutputStream out = c.getOutputStream()) { out.write("{}".getBytes(StandardCharsets.UTF_8)); }
            }
            int code = c.getResponseCode();
            InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
            String body = in == null ? "" : new String(in.readAllBytes(), StandardCharsets.UTF_8);
            c.disconnect();
            return new String[] { String.valueOf(code), body };
        } catch (Exception e) {
            return null;
        }
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
