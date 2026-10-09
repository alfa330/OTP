import { lazy } from 'react';
import { recoverFromStaleBundle } from '../staleBundleRecovery.js';

// Native module imports can remember a rejected load. Recover with a fresh
// document instead of repeatedly importing the same missing URL in this realm.
const lazyWithRetry = (importer) => lazy(async () => {
    try {
        return await importer();
    } catch (error) {
        if (recoverFromStaleBundle(error)) {
            // Keep Suspense visible while navigation is in progress.
            return new Promise(() => {});
        }
        throw error;
    }
});

export default lazyWithRetry;
