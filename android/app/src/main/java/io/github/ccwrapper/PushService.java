package io.github.ccwrapper;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.json.JSONObject;

/**
 * Session notifications, sent by your own server through Firebase Cloud Messaging as data messages
 * (see README, "Push notifications"). A tap opens the session in this app (MainActivity routes it without
 * reloading claude.ai).
 */
public class PushService extends FirebaseMessagingService {
    private static final String TAG = "CCMPush";
    static final String CHANNEL_SESSIONS = "sessions";

    @Override
    public void onNewToken(String token) {
        register(this, token);
    }

    @Override
    public void onMessageReceived(RemoteMessage message) {
        Map<String, String> data = message.getData();
        // The release and the debug build can share one Firebase registration, and so one push token on a phone;
        // a push may name the build it is for.
        String to = data.get("app");
        if (to != null && !to.equals(getPackageName())) return;
        String title = data.getOrDefault("title", "Claude Code");
        String body = data.getOrDefault("body", "");
        // A link may use either build's scheme; point it at this build's own.
        String link = data.get("link");
        if (link != null) link = link.replaceFirst("^ccm(-dev)?://", BuildConfig.LINK_SCHEME + "://");
        // One notification per session: a newer push for it replaces the older one.
        String group = data.getOrDefault("group", link != null ? link : title);

        NotificationCompat.Builder n = new NotificationCompat.Builder(this, CHANNEL_SESSIONS)
            .setSmallIcon(R.drawable.ic_stat_claude)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
            .setPriority("high".equals(data.get("priority")) ? NotificationCompat.PRIORITY_HIGH : NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true);
        if (link != null) {
            Intent open = new Intent(Intent.ACTION_VIEW, Uri.parse(link)).setPackage(getPackageName());
            n.setContentIntent(PendingIntent.getActivity(this, group.hashCode(), open,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
        }
        getSystemService(NotificationManager.class).notify(group.hashCode(), n.build());
    }

    static void createChannel(Context context) {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL_SESSIONS, "Sessions", NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription("A Claude Code session finished or needs you");
        context.getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    /** Tell the push server where to send pushes (POST <HUB_URL>/api/devices). Off the main thread; retried each launch. */
    static void register(Context context, String token) {
        if (BuildConfig.HUB_URL.isEmpty()) {
            Log.i(TAG, "FCM token (no HUB_URL set, so not registered anywhere): " + token);
            return;
        }
        String app = context.getPackageName();
        new Thread(() -> post("/api/devices", "token", token, "app", app, "name", Build.MANUFACTURER + " " + Build.MODEL)).start();
    }

    /** POST a small JSON object to the push server; if it can't be reached it just fails quietly. */
    static void post(String path, String... keyValues) {
        try {
            JSONObject json = new JSONObject();
            for (int i = 0; i + 1 < keyValues.length; i += 2) json.put(keyValues[i], keyValues[i + 1]);
            HttpURLConnection c = (HttpURLConnection) new URL(BuildConfig.HUB_URL.replaceAll("/+$", "") + path).openConnection();
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            c.setRequestProperty("Content-Type", "application/json");
            try (OutputStream out = c.getOutputStream()) {
                out.write(json.toString().getBytes(StandardCharsets.UTF_8));
            }
            Log.i(TAG, path + " -> " + c.getResponseCode());
            c.disconnect();
        } catch (Exception e) {
            Log.w(TAG, path + " failed: " + e);
        }
    }
}
