# cc-wrapper

[Claude Code on the web](https://claude.ai/code), wrapped as an Android app for phones.

The app loads claude.ai/code itself in a WebView (nothing is reimplemented, and you sign in with your own
Claude account) and injects a small script and stylesheet before the page renders
(`android/app/src/main/assets/inject/`). Over the plain website in a mobile browser it adds:

- **More room for the chat:** tighter side gutters on narrow screens, a header reduced to its ⋯ menu (Share
  moves into that menu), and side panels (files, progress...) opened full width instead of as a narrow tile.
- **A composer that behaves on a phone:** it stays above the keyboard (also on Android 15's edge-to-edge), the
  keyboard closes when you send, and opening a chat doesn't pop the keyboard over the reply you came to read.
- **Status the phone layout hides:** a "context used" percentage next to the model picker (tap for the session
  details) and a pill while background tasks are running (tap to open them).
- **App plumbing:** "Continue with Google" works in place (sign-in popups are real windows), links to other sites
  open in your browser or whichever app handles them, `ccm://session/<id>` links open that session in the app,
  the microphone works for dictation, and the offline page only appears when the network is really down.
- **Push notifications** (needs your own Firebase project and a server that sends them; see below): a
  notification per session that opens it when tapped.
- **A settings tray:** swipe down from the top bar of the page to pull it down. Its first setting is Kindle
  dictation: the phone as the microphone of a Kindle (or any) frontend your server runs (see below).

This is an unofficial, personal project. It is not affiliated with or endorsed by Anthropic. The injected fixes
depend on claude.ai's current markup and can break when the site changes.

## Build

Needs Node 20+, JDK 21 and the Android SDK (`sdk.dir=...` in `android/local.properties`, or `ANDROID_HOME`).

```sh
cd android
cp .env.example .env   # then fill in your values, see Configuration
npm ci
npx cap sync android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

`android/` is both the Capacitor project (`package.json`, `capacitor.config.ts`, `www/`) and the Gradle project
(`android.path: '.'` in the Capacitor config).

The debug build installs as **Claude Code Dev** (`io.github.ccwrapper.dev`, link scheme `ccm-dev://`) next to
the release build (`io.github.ccwrapper`, `ccm://`). CI (`.github/workflows/android.yml`) builds a debug APK on
every push and pull request; it has no `.env`, so that APK has no push notifications.

## Configuration

Per-user settings are read at build time from `android/.env`, which is gitignored; `android/.env.example` lists
every key, with where to find each Firebase value. A key can also come from an environment variable of the same
name, e.g. a CI secret; `.env` wins. Rebuild after changing it.

The build works without a `.env` (Gradle then warns that push notifications won't work), and at runtime an app
built without the Firebase values simply skips Firebase.

| Key | What it is | If empty |
| --- | --- | --- |
| `FIREBASE_PROJECT_ID` | Firebase console → Project settings → General → Project ID | no push |
| `FIREBASE_SENDER_ID` | Project settings → Cloud Messaging → Sender ID (= the project number) | no push |
| `FIREBASE_APP_ID` | Project settings → General → Your apps → your Android app's App ID (`1:…:android:…`) | no push |
| `FIREBASE_API_KEY` | Project settings → General → Web API key | no push |
| `HUB_URL` | Base URL of your server: the app registers its FCM token there, and Kindle dictation connects to it. `PUSH_SERVER_URL`, its old name, still works | token only logged, no Kindle dictation |
| `INJECT_DEV_URL` | Debug build only: a URL serving `inject.js` and `inject.css`, re-fetched whenever the app comes to the front, so edits need no reinstall | bundled files only |
| `CCM_KEYSTORE`, `CCM_KEYSTORE_PASSWORD`, `CCM_KEY_ALIAS` | Release signing: keystore path, its (and the key's) password, key alias (default `cc-mobile`) | release signed with the debug key |

### Push notifications

The app receives Firebase Cloud Messaging (FCM) data messages and shows them; something on your side has to
send them, for example a Claude Code `Stop` or `Notification` hook on the machine your sessions run on, calling
the FCM HTTP v1 API with a service account of the same Firebase project.

1. Create a Firebase project (or use one you have) and add an Android app to it with the package name
   `io.github.ccwrapper` (add `io.github.ccwrapper.dev` too if you'll use the debug build; one app's values
   also work for the other).
2. Put its four values in `android/.env` and rebuild. The app then starts Firebase, asks for the notification
   permission and gets an FCM token.
3. Get the token to your server. With `HUB_URL` set, the app posts JSON to it:
   - `POST <HUB_URL>/api/devices` with `{"token", "app", "name"}` on every launch and when the token
     changes (`app` is the package name, `name` the phone model);
   - `POST <HUB_URL>/api/activity` with `{"session", "token", "app"}` when you send a message in a
     session, so a server that serves several phones can notify the one you last used for that session.

   Without `HUB_URL` the token is only logged (`adb logcat -s CCMPush`), for you to copy by hand.
4. Send data messages (not notification messages) to that token with these fields, all optional:
   `title`, `body`, `link` (`ccm://session/<claude.ai session id>`, opened on tap), `group` (a later push with
   the same group replaces the earlier notification; defaults to the link), `priority` (`high` to pop up),
   and `app` (a package name; other builds on the same phone ignore the push).

The Firebase values are client configuration rather than secrets, but they identify your project, so they stay
out of the repo.

## Settings tray

Swipe down on the page's top bar (the strip just below the status bar, about 48dp tall) to pull the tray down
over the page. Only a downward drag that starts in that strip opens it: taps on the page's header, scrolling and
sideways swipes go to the page as before, and a swipe that starts on the status bar itself is still Android's
notification shade. Swipe the tray up, tap below it or press Back to close it. It follows the system's dark or
light mode.

### Kindle dictation

A Kindle has no microphone. With **Kindle dictation (use this phone's mic)** switched on in the tray, the phone
records for a frontend your server runs (e.g. a Kindle panel's Mic button), with the phone locked or not. The
first time, the app asks for the microphone (and on Android 13+ for notifications, for the service's ongoing
notification, which also has a Turn off button). The switch is remembered: the service starts again whenever
the app comes to the front. Needs `HUB_URL`.

While it's on, a foreground service (type microphone) keeps a WebSocket open to `<HUB_URL as ws(s)>/api/mic`,
reconnecting with backoff when it drops. The mic itself is only open between the server's `start` and `stop`:

| Direction | Frame | Meaning |
| --- | --- | --- |
| phone → server | text `ready <device name>` | sent on connect |
| server → phone | text `start` | start a recording |
| phone → server | binary | 16 kHz mono 16-bit little-endian PCM, ~100 ms per frame, while recording |
| server → phone | text `stop` | stop recording |
| phone → server | text `stopped` | after the recording's last PCM frame |
| phone → server | text `error <message>` | the mic couldn't start (busy, no permission) |

The app sends WebSocket pings every 30 s. A recording ends if the connection drops.

## Kindle

`kindle/` is a placeholder for a lightweight web frontend for Claude Code sessions on a Kindle's e-ink browser.

## Works well with Otto

[Otto](https://github.com/chinmayjx/otto) is a separate project that adds a second screen next to the chat on
the phone (a browser your Claude sessions can drive, and other tools), and handles artifact links that this app
hands off.

## License

MIT, see [LICENSE](LICENSE).
