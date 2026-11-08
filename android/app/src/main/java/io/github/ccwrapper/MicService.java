package io.github.ccwrapper;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.net.ConnectivityManager;
import android.net.Network;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

/**
 * Kindle dictation: this phone is the microphone of your hub's Kindle panel. While the switch in the settings
 * tray is on, this foreground service (type microphone) keeps a WebSocket open to <HUB_URL>/api/mic:
 *
 *   phone -> hub   text "ready <device name>" on connect
 *   hub -> phone   text "start" / "stop"
 *   phone -> hub   binary 16 kHz mono 16-bit little-endian PCM (~100 ms a frame) while recording,
 *                  text "stopped" after the last frame, or "error <message>" if the mic can't start
 *
 * The mic is only open between "start" and "stop". Android lets a service record in the background (phone locked)
 * only if it took the microphone type while the app was in front, so it is started from the tray switch or when
 * the app comes to the front with the switch on, never from the background.
 */
public class MicService extends Service {
    private static final String TAG = "CCMMic";
    private static final String PREFS = "settings";
    private static final String KEY_ON = "kindle_dictation";
    private static final String ACTION_OFF = "io.github.ccwrapper.MIC_OFF";
    private static final String CHANNEL = "dictation";
    private static final int NOTIFICATION = 2;
    private static final int RATE = 16000;
    private static final long MAX_RECORDING_MS = 35 * 60_000; // the hub stops at 30 minutes; this is a backstop

    /** "off", "connecting", "connected", "recording", or why it can't connect; shown in the tray. */
    static volatile String state = "off";
    private static final List<Runnable> listeners = new CopyOnWriteArrayList<>();

    private final Handler main = new Handler(Looper.getMainLooper());
    private OkHttpClient http;
    private WebSocket socket;
    private boolean running;
    private long backoffMs = 1000;
    private final Runnable reconnect = this::connect;
    private ConnectivityManager.NetworkCallback netCallback;
    private AudioRecord rec; // guarded by this

    // ---- the switch -----------------------------------------------------------------------------------------

    static boolean configured() {
        return !BuildConfig.HUB_URL.isEmpty();
    }

    static boolean switchedOn(Context c) {
        return c.getSharedPreferences(PREFS, MODE_PRIVATE).getBoolean(KEY_ON, false);
    }

    private static void setSwitch(Context c, boolean on) {
        c.getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean(KEY_ON, on).apply();
    }

    /** From the tray switch (the app is in front). Turning on needs RECORD_AUDIO already granted. */
    static void turn(Context c, boolean on) {
        setSwitch(c, on);
        if (on) ensureRunning(c);
        else c.stopService(new Intent(c, MicService.class));
    }

    /** Starts the service if the switch is on; only call while the app is in front. */
    static void ensureRunning(Context c) {
        if (!switchedOn(c) || !configured()) return;
        if (c.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            setSwitch(c, false);
            notifyListeners();
            return;
        }
        try {
            if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(new Intent(c, MicService.class));
            else c.startService(new Intent(c, MicService.class));
        } catch (RuntimeException e) { // e.g. Android refusing a foreground start
            Log.w(TAG, "couldn't start: " + e);
        }
    }

    static void listen(Runnable r) { listeners.add(r); }

    static void unlisten(Runnable r) { listeners.remove(r); }

    private static void notifyListeners() {
        for (Runnable r : listeners) r.run();
    }

    // ---- the service ----------------------------------------------------------------------------------------

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_OFF.equals(intent.getAction())) {
            setSwitch(this, false);
            stopSelf();
            notifyListeners();
            return START_NOT_STICKY;
        }
        try {
            if (Build.VERSION.SDK_INT >= 30) {
                startForeground(NOTIFICATION, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
            } else {
                startForeground(NOTIFICATION, notification());
            }
        } catch (RuntimeException e) {
            // No mic permission after all, or restarted from the background, where Android won't allow the
            // microphone type: wait for the app to come to the front again.
            Log.w(TAG, "no foreground mic: " + e);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!running) {
            running = true;
            http = new OkHttpClient.Builder()
                .pingInterval(30, TimeUnit.SECONDS)
                .readTimeout(0, TimeUnit.MILLISECONDS)
                .build();
            netCallback = new ConnectivityManager.NetworkCallback() {
                @Override public void onAvailable(Network n) {
                    main.post(() -> { if (socket == null && running) { main.removeCallbacks(reconnect); backoffMs = 1000; connect(); } });
                }
            };
            try {
                getSystemService(ConnectivityManager.class).registerDefaultNetworkCallback(netCallback);
            } catch (RuntimeException e) {
                netCallback = null;
            }
            connect();
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        main.removeCallbacksAndMessages(null);
        if (netCallback != null) {
            try { getSystemService(ConnectivityManager.class).unregisterNetworkCallback(netCallback); } catch (RuntimeException ignored) {}
        }
        stopRecording(null);
        WebSocket s = socket;
        socket = null;
        if (s != null) s.close(1000, "dictation off");
        if (http != null) http.dispatcher().executorService().shutdown();
        setState("off");
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    /** On the main thread. */
    private void connect() {
        if (!running) return;
        setState("connecting");
        Request req = new Request.Builder().url(BuildConfig.HUB_URL + "/api/mic").build();
        socket = http.newWebSocket(req, new Link());
    }

    private final class Link extends WebSocketListener {
        @Override
        public void onOpen(WebSocket ws, Response response) {
            ws.send("ready " + Build.MANUFACTURER + " " + Build.MODEL);
            main.post(() -> {
                if (socket != ws) return;
                backoffMs = 1000;
                setState("connected");
            });
        }

        @Override
        public void onMessage(WebSocket ws, String text) {
            if ("start".equals(text)) startRecording(ws);
            else if ("stop".equals(text)) stopRecording(ws);
        }

        @Override
        public void onClosing(WebSocket ws, int code, String reason) {
            ws.close(1000, null);
            dropped(ws, "closed by the hub");
        }

        @Override
        public void onClosed(WebSocket ws, int code, String reason) {
            dropped(ws, "closed by the hub");
        }

        @Override
        public void onFailure(WebSocket ws, Throwable t, Response response) {
            dropped(ws, response != null ? "the hub answered " + response.code() : "can't reach the hub");
            Log.d(TAG, "link: " + t);
        }
    }

    /** The connection went: stop any recording (it belonged to it) and try again after a backoff. */
    private void dropped(WebSocket ws, String why) {
        stopRecording(null);
        main.post(() -> {
            if (socket != ws) return;
            socket = null;
            if (!running) return;
            setState(why);
            main.postDelayed(reconnect, backoffMs);
            backoffMs = Math.min(backoffMs * 2, 30_000);
        });
    }

    // ---- recording (as Device agent's Mic did) --------------------------------------------------------------

    private synchronized void startRecording(WebSocket ws) {
        if (rec != null) return; // already recording: the hub's buffer was cleared, carry on
        int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord r;
        try {
            r = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, Math.max(min, RATE * 2));
        } catch (SecurityException e) {
            ws.send("error no microphone permission: turn Kindle dictation on again in the app's tray");
            return;
        } catch (IllegalArgumentException e) {
            ws.send("error " + e.getMessage());
            return;
        }
        if (r.getState() != AudioRecord.STATE_INITIALIZED) {
            r.release();
            ws.send("error the microphone is busy or not allowed");
            return;
        }
        try {
            r.startRecording();
        } catch (IllegalStateException e) {
            r.release();
            ws.send("error the microphone is busy");
            return;
        }
        rec = r;
        new Thread(() -> pump(r, ws), "mic").start();
        main.post(() -> setState("recording"));
        Log.i(TAG, "mic on");
    }

    private void pump(AudioRecord r, WebSocket ws) {
        byte[] b = new byte[3200]; // 100 ms
        long started = System.currentTimeMillis();
        while (true) {
            int n = r.read(b, 0, b.length);
            synchronized (this) {
                if (rec != r) break;
                if (n < 0 || System.currentTimeMillis() - started > MAX_RECORDING_MS) {
                    release();
                    break;
                }
                if (n > 0) ws.send(ByteString.of(b, 0, n));
            }
        }
        r.release(); // here, not in release(): the read above may still have been running then
        ws.send("stopped"); // after the last frame: the hub's stop() waits for it
    }

    /** Stops recording; ws (the connection asking) gets "stopped" from the pump once its last frame is sent. */
    private synchronized void stopRecording(WebSocket ws) {
        if (rec == null) {
            if (ws != null) ws.send("stopped");
            return;
        }
        release();
    }

    private synchronized void release() {
        AudioRecord r = rec;
        rec = null;
        if (r == null) return;
        try { r.stop(); } catch (RuntimeException ignored) {} // ends the pump's read; the pump releases it
        Log.i(TAG, "mic off");
        main.post(() -> { if (running && socket != null) setState("connected"); });
    }

    // ---- state and notification -----------------------------------------------------------------------------

    private void setState(String s) {
        state = s;
        if (running) {
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.notify(NOTIFICATION, notification());
        }
        notifyListeners();
    }

    private Notification notification() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Kindle dictation", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Shown while this phone is the Kindle's microphone");
            nm.createNotificationChannel(ch);
        }
        PendingIntent open = PendingIntent.getActivity(this, 0,
            new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE);
        PendingIntent off = PendingIntent.getService(this, 1,
            new Intent(this, MicService.class).setAction(ACTION_OFF), PendingIntent.FLAG_IMMUTABLE);
        return new NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_claude)
            .setContentTitle("Kindle dictation")
            .setContentText(describe(state))
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .addAction(0, "Turn off", off)
            .build();
    }

    /** One line for the notification and the tray. */
    static String describe(String s) {
        switch (s) {
            case "off": return "Off";
            case "connecting": return "Connecting to the hub…";
            case "connected": return "Ready: the Kindle's Mic button records through this phone";
            case "recording": return "Recording for the Kindle";
            default: return s.substring(0, 1).toUpperCase() + s.substring(1) + ", retrying…";
        }
    }
}
