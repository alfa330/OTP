const RECOVERY_KEY = 'otp_stale_bundle_recovery_v1';
const RECOVERY_COOLDOWN_MS = 20000;

const STALE_BUNDLE_PATTERNS = [
  /ChunkLoadError/i,
  /Loading chunk [\w-]+ failed/i,
  /Failed to fetch dynamically imported module/i,
  /Importing a module script failed/i,
  /tailwind is not defined/i,
  /Cannot read properties of undefined \(reading ['"]config['"]\)/i
];

const readState = () => {
  try {
    const raw = window.sessionStorage.getItem(RECOVERY_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return parsed;
  } catch {
    return null;
  }
};

const writeState = (state) => {
  try {
    window.sessionStorage.setItem(RECOVERY_KEY, JSON.stringify(state));
  } catch {}
};

const buildDiagnosticText = (parts) =>
  parts
    .map((part) => (typeof part === 'string' ? part : ''))
    .filter(Boolean)
    .join('\n');

const isLikelyStaleBundleIssue = (text) =>
  STALE_BUNDLE_PATTERNS.some((pattern) => pattern.test(text));

const tryRecoverFromStaleBundle = (diagnosticText) => {
  const now = Date.now();
  const previous = readState();
  if (previous?.ts && now - Number(previous.ts) < RECOVERY_COOLDOWN_MS) {
    return;
  }

  writeState({
    ts: now,
    reason: diagnosticText.slice(0, 400)
  });

  const url = new URL(window.location.href);
  url.searchParams.set('v', String(now));
  window.location.replace(url.toString());
};

if (typeof window !== 'undefined') {
  window.addEventListener(
    'error',
    (event) => {
      const text = buildDiagnosticText([
        event?.message,
        event?.error?.message,
        event?.error?.stack,
        event?.filename
      ]);
      if (isLikelyStaleBundleIssue(text)) {
        tryRecoverFromStaleBundle(text);
      }
    },
    true
  );

  window.addEventListener('unhandledrejection', (event) => {
    const reason = event?.reason;
    const text = buildDiagnosticText([
      typeof reason === 'string' ? reason : '',
      reason?.message,
      reason?.stack
    ]);
    if (isLikelyStaleBundleIssue(text)) {
      tryRecoverFromStaleBundle(text);
    }
  });
}
