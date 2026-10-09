const transfers = new WeakMap();

const mediaSource = (media) => media.getAttribute('src')
    || Array.from(media.querySelectorAll('source')).map((source) => source.getAttribute('src')).join('\n')
    || media.currentSrc;

const snapshot = (media) => ({
    source: mediaSource(media),
    time: media.currentTime,
    rate: media.playbackRate,
    paused: media.paused,
    muted: media.muted,
    volume: media.volume,
    autoplay: media.autoplay,
});

/** Move the existing portal subtree without losing playback to document adoption. */
export function moveAttachmentHost(host, destinationBody) {
    const previous = transfers.get(host);
    if (host.parentNode === destinationBody) return previous?.cancel || (() => {});
    if (host.ownerDocument === destinationBody.ownerDocument) {
        // Initial attachment has no adoption reset; preserve native autoplay.
        destinationBody.appendChild(host);
        return previous?.cancel || (() => {});
    }

    const records = Array.from(host.querySelectorAll('video, audio')).map((media) => {
        const earlier = previous?.records.find((record) => record.media === media);
        const waiting = earlier?.waitingForMetadata && earlier.state.source === mediaSource(media);
        return { media, state: waiting ? earlier.state : snapshot(media), waitingForMetadata: true,
            resumePending: false, cancelled: false, stop: null };
    });
    previous?.cancel();
    let cancelled = false;

    const transfer = {
        records,
        cancel() {
            if (cancelled) return;
            cancelled = true;
            records.forEach((record) => record.stop?.());
            if (transfers.get(host) === transfer) transfers.delete(host);
        },
    };
    transfers.set(host, transfer);

    records.forEach((record) => {
        const { media, state } = record;
        const timerWindow = destinationBody.ownerDocument.defaultView;
        let pollTimer = null, pollAttempts = 0;
        const stopPoll = () => {
            if (pollTimer !== null) timerWindow.clearTimeout(pollTimer);
            pollTimer = null;
        };
        const valid = () => !cancelled && !record.cancelled && host.isConnected && media.isConnected
            && host.parentNode === destinationBody && media.ownerDocument === destinationBody.ownerDocument
            && mediaSource(media) === state.source;
        const resume = () => {
            record.resumePending = true;
            try {
                const playback = media.play();
                Promise.resolve(playback).then(() => { record.resumePending = false; }, () => {
                    record.resumePending = false;
                });
            } catch { record.resumePending = false; }
        };
        const restore = () => {
            if (!valid() || media.readyState < 1) return;
            try {
                media.muted = state.muted;
                media.volume = state.volume;
                media.playbackRate = state.rate;
                if (Number.isFinite(state.time)) media.currentTime = state.time;
                media.autoplay = state.autoplay;
                // pause() also clears the browser's autoplay eligibility after adoption.
                if (state.paused) media.pause();
                else resume();
                record.waitingForMetadata = false;
                stopPoll();
            } catch { /* A new load can briefly invalidate duration/currentTime. */ }
        };
        const startPoll = () => {
            if (pollTimer !== null || pollAttempts >= 200 || !record.waitingForMetadata || !valid()) return;
            // Chromium can suppress media events when the old PiP document closes.
            // Run in the destination window, which remains alive after that close.
            pollTimer = timerWindow.setTimeout(() => {
                pollTimer = null;
                pollAttempts += 1;
                if (!valid()) return;
                if (media.readyState >= 1) restore();
                startPoll();
            }, 50);
        };
        const loadedMetadata = () => { if (record.waitingForMetadata) restore(); };
        const emptied = () => {
            if (!valid()) return;
            record.waitingForMetadata = true;
            media.autoplay = false;
            startPoll();
        };
        record.stop = () => {
            if (record.cancelled) return;
            record.cancelled = true;
            stopPoll();
            media.removeEventListener('loadedmetadata', loadedMetadata);
            media.removeEventListener('emptied', emptied);
            if (mediaSource(media) !== state.source) return;
            media.autoplay = state.autoplay;
            // A pending play promise must not start detached or replaced media later.
            if (record.resumePending || record.waitingForMetadata || state.paused) media.pause();
        };
        media.addEventListener('loadedmetadata', loadedMetadata);
        media.addEventListener('emptied', emptied);
        media.autoplay = false;
        media.pause();
        record.restore = restore;
        record.startPoll = startPoll;
    });

    try {
        destinationBody.appendChild(host);
        records.forEach((record) => { record.restore(); record.startPoll(); });
    } catch (error) {
        transfer.cancel();
        throw error;
    }
    return transfer.cancel;
}
