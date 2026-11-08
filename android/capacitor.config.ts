import type { CapacitorConfig } from '@capacitor/cli'

// A thin native shell around claude.ai/code. The site itself is loaded live; the app only adds
// what the site lacks on a phone (see app/src/main/assets/inject/).
// This directory is both the Capacitor project and the Gradle project, hence android.path '.'.
const config: CapacitorConfig = {
  appId: 'io.github.ccwrapper',
  appName: 'CC Wrapper',
  webDir: 'www',
  backgroundColor: '#faf9f5',
  server: {
    url: 'https://claude.ai/code',
    errorPath: 'offline.html',
    // Hosts the app may navigate to in place; any other link opens in the system browser.
    // Google sign-in stays inside the app so the session cookie lands here. After 2-step verification
    // Google hops through accounts.youtube.com (and regional google domains) to set its own cookies;
    // handing those to the browser broke the sign-in with "400 bad request".
    allowNavigation: ['claude.ai', '*.claude.ai', '*.anthropic.com', '*.claude.com', '*.google.com',
      'accounts.youtube.com', 'accounts.google.co.in'],
  },
  android: { path: '.', backgroundColor: '#faf9f5', adjustMarginsForEdgeToEdge: 'auto' },
}

export default config
