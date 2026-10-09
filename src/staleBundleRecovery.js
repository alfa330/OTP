const RECOVERY_KEY = 'otp_stale_bundle_recovery_v2';
const RECOVERY_COOLDOWN_MS = 30000;
// Vite hashes the entry script together with its dependencies. Read that hash
// instead of injecting a timestamp, which would invalidate every shared chunk.
const BUILD_ID = typeof document === 'undefined' ? 'non-browser'
  : document.querySelector('script[type="module"][src]')?.src || 'development';

const STALE_BUNDLE_PATTERNS = [
  /ChunkLoadError/i,
  /Loading chunk [\w-]+ failed/i,
  /Failed to fetch dynamically imported module/i,
  /Importing a module script failed/i,
  /error loading dynamically imported module/i,
  /Unable to preload CSS/i,
  /tailwind is not defined/i,
  /Cannot read properties of undefined \(reading ['"]config['"]\)/i,
];

const diagnosticText = (error) => typeof error === 'string'
  ? error
  : [error?.message, error?.stack].filter(Boolean).join('\n');

export const isStaleBundleError = (error) =>
  STALE_BUNDLE_PATTERNS.some((pattern) => pattern.test(diagnosticText(error)));

const validState = (state) => state && typeof state.build === 'string'
  && Number.isFinite(state.ts) && state.ts > 0;

/** One shared guard for React.lazy, Vite preload errors and the root boundary.
 * A successful unrelated import must NOT reset it: that used to cause reload loops.
 * The URL marker also survives a navigation when sessionStorage is unavailable.
 * Capture it before main.jsx cleans technical query parameters from the address.
 */
export const createStaleBundleRecovery = (browser, build = BUILD_ID, now = Date.now) => {
  let previous = null;
  let navigating = false;
  if (browser) {
    try {
      const stored = JSON.parse(browser.sessionStorage.getItem(RECOVERY_KEY));
      if (validState(stored)) previous = stored;
    } catch { /* Storage can be blocked by browser policy. */ }
    try {
      const marker = new URL(browser.location.href).searchParams.get('v') || '';
      const match = /^chunk\.(\d+)\.(.+)$/.exec(marker);
      const state = match && { ts: Number(match[1]), build: match[2] };
      if (validState(state) && (!previous || state.ts >= previous.ts)) previous = state;
    } catch { /* No usable location in a non-browser environment. */ }
  }

  const recover = (error) => {
    if (!browser || !isStaleBundleError(error)) return false;
    if (navigating) return true;
    // A reload cannot repair an offline connection and would discard the open UI.
    if (browser.navigator?.onLine === false) return false;
    const ts = now();
    if (previous && (previous.build === build || ts - previous.ts < RECOVERY_COOLDOWN_MS)) {
      return false;
    }

    try {
      const url = new URL(browser.location.href);
      url.searchParams.set('v', `chunk.${ts}.${build}`);
      previous = { ts, build };
      try {
        browser.sessionStorage.setItem(RECOVERY_KEY, JSON.stringify(previous));
      } catch { /* The URL carries the guard across the reload too. */ }
      navigating = true;
      browser.location.replace(url.toString());
      return true;
    } catch {
      navigating = false;
      return false;
    }
  };

  const install = () => {
    if (!browser?.addEventListener) return () => {};
    const onPreloadError = (event) => {
      // Do not preventDefault: Vite would resolve the import as undefined, which
      // breaks React.lazy and masks the real error when recovery is exhausted.
      recover(event?.payload);
    };
    const onError = (event) => recover([
      event?.message, event?.error?.message, event?.error?.stack,
    ].filter(Boolean).join('\n'));
    const onRejection = (event) => recover(event?.reason);
    browser.addEventListener('vite:preloadError', onPreloadError);
    browser.addEventListener('error', onError, true);
    browser.addEventListener('unhandledrejection', onRejection);
    return () => {
      browser.removeEventListener('vite:preloadError', onPreloadError);
      browser.removeEventListener('error', onError, true);
      browser.removeEventListener('unhandledrejection', onRejection);
    };
  };
  return { recover, install };
};

const recovery = createStaleBundleRecovery(typeof window === 'undefined' ? null : window);
export const recoverFromStaleBundle = recovery.recover;
recovery.install();
