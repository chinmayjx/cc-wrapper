package io.github.ccwrapper;

import android.app.Application;
import android.content.Context;
import android.util.Log;
import com.google.firebase.FirebaseApp;
import com.google.firebase.FirebaseOptions;

/**
 * Starts Firebase by hand (no google-services plugin) from the values build.gradle reads out of android/.env
 * (resValue strings firebase_*). Here rather than in MainActivity so a push that starts the app's process can be
 * handled before any activity exists. Without those values Firebase is left off and the app runs without push.
 */
public class CCMApplication extends Application {
    private static final String TAG = "CCMPush";

    @Override
    public void onCreate() {
        super.onCreate();
        if (!pushConfigured(this)) {
            Log.w(TAG, "No Firebase values in this build (android/.env): push notifications are off");
            return;
        }
        if (FirebaseApp.getApps(this).isEmpty()) {
            FirebaseApp.initializeApp(this, new FirebaseOptions.Builder()
                .setProjectId(getString(R.string.firebase_project_id))
                .setGcmSenderId(getString(R.string.firebase_sender_id))
                .setApplicationId(getString(R.string.firebase_app_id))
                .setApiKey(getString(R.string.firebase_api_key))
                .build());
        }
    }

    /** Whether this build carries a complete Firebase configuration. */
    static boolean pushConfigured(Context c) {
        return !c.getString(R.string.firebase_project_id).isEmpty()
            && !c.getString(R.string.firebase_sender_id).isEmpty()
            && !c.getString(R.string.firebase_app_id).isEmpty()
            && !c.getString(R.string.firebase_api_key).isEmpty();
    }

    /** Whether Firebase is running in this process (configured and started). */
    static boolean pushReady(Context c) {
        return pushConfigured(c) && !FirebaseApp.getApps(c).isEmpty();
    }
}
