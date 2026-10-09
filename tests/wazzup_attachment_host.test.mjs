import test from 'node:test';
import assert from 'node:assert/strict';
import { moveAttachmentHost } from '../src/components/wazzup/moveAttachmentHost.js';

const makeTimers = () => {
    const callbacks = new Map(); let index = 0;
    return { callbacks,
        setTimeout(callback, delay) { assert.equal(delay, 50); callbacks.set(++index, callback); return index; },
        clearTimeout(id) { callbacks.delete(id); },
        tick() { const pending = [...callbacks.values()]; callbacks.clear(); pending.forEach((callback) => callback()); },
    };
};
const makeMedia = (values = {}) => {
    const listeners = new Map();
    return {
        src: 'https://example.invalid/movie.mp4', currentSrc: '', currentTime: 12.5, playbackRate: 1.5,
        paused: false, muted: true, volume: 0.4, autoplay: true, readyState: 1, isConnected: true,
        playCalls: 0, pauseCalls: 0, canAutoplay: false, listeners,
        getAttribute(name) { return this[name] ?? null; },
        querySelectorAll() { return []; },
        addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(listener); },
        removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
        emit(type) { [...(listeners.get(type) || [])].forEach((listener) => listener()); },
        play() { this.playCalls += 1; this.paused = false; this.canAutoplay = false; return Promise.resolve(); },
        pause() { this.pauseCalls += 1; this.paused = true; this.canAutoplay = false; },
        reset() { this.currentTime = 0; this.playbackRate = 1; this.readyState = 0; this.paused = true;
            this.canAutoplay = true; this.emit('emptied'); },
        metadata() { this.readyState = 1; this.emit('loadedmetadata');
            if (this.autoplay && this.canAutoplay) { this.play(); } },
        ...values,
    };
};
const fixture = ({ media = [makeMedia()], reset = 'sync' } = {}) => {
    const home = { ownerDocument: { name: 'home', defaultView: makeTimers() }, moves: 0 };
    const detached = { ownerDocument: { name: 'pip', defaultView: makeTimers() }, moves: 0 };
    const host = { parentNode: home, ownerDocument: home.ownerDocument, isConnected: true, querySelectorAll: () => media };
    media.forEach((item) => { item.ownerDocument = home.ownerDocument; });
    for (const body of [home, detached]) body.appendChild = (node) => {
        body.moves += 1; node.parentNode = body; node.ownerDocument = body.ownerDocument;
        media.forEach((item) => { item.ownerDocument = body.ownerDocument; if (reset === 'sync') item.reset(); });
    };
    return { home, detached, host, media };
};

test('playing video retains position, rate, mute, volume and autoplay after metadata reload', async () => {
    const h = fixture(), video = h.media[0];
    const cancel = moveAttachmentHost(h.host, h.detached);
    try {
        assert.equal(video.currentTime, 0);
        assert.equal(video.autoplay, false);
        assert.equal(video.playCalls, 0);
        video.volume = 1; video.muted = false;
        video.metadata();
        await Promise.resolve();
        assert.equal(video.currentTime, 12.5);
        assert.equal(video.playbackRate, 1.5);
        assert.equal(video.muted, true);
        assert.equal(video.volume, 0.4);
        assert.equal(video.autoplay, true);
        assert.equal(video.paused, false);
        assert.equal(video.playCalls, 1);
    } finally { cancel(); }
});

test('initial attachment in the same document preserves native autoplay without restore listeners', () => {
    const h = fixture(), video = h.media[0];
    h.host.parentNode = null;
    video.readyState = 0; video.currentTime = 0; video.paused = true;
    h.home.appendChild = (node) => { h.home.moves += 1; node.parentNode = h.home; };
    const cancel = moveAttachmentHost(h.host, h.home);
    assert.equal(h.home.moves, 1);
    assert.equal(video.pauseCalls, 0);
    assert.equal(video.autoplay, true);
    assert.equal(video.listeners.size, 0);
    video.canAutoplay = true;
    video.metadata();
    assert.equal(video.paused, false);
    cancel();
    assert.equal(video.paused, false);
});

test('paused video stays paused even when its original autoplay attribute was enabled', () => {
    const h = fixture({ media: [makeMedia({ paused: true })] }), video = h.media[0];
    const cancel = moveAttachmentHost(h.host, h.detached);
    try {
        video.metadata();
        assert.equal(video.currentTime, 12.5);
        assert.equal(video.paused, true);
        assert.equal(video.autoplay, true);
        assert.equal(video.playCalls, 0);
        assert.equal(video.canAutoplay, false);
    } finally { cancel(); }
});

test('an asynchronous adoption reset is restored after the initial immediate restoration', async () => {
    const h = fixture({ reset: 'async' }), video = h.media[0];
    const cancel = moveAttachmentHost(h.host, h.detached);
    try {
        assert.equal(video.currentTime, 12.5);
        assert.equal(video.playCalls, 1);
        video.reset();
        assert.equal(video.currentTime, 0);
        assert.equal(video.autoplay, false);
        video.metadata();
        await Promise.resolve();
        assert.equal(video.currentTime, 12.5);
        assert.equal(video.playbackRate, 1.5);
        assert.equal(video.paused, false);
        assert.equal(video.playCalls, 2);
    } finally { cancel(); }
});

test('successful immediate restoration never overwrites the user playing, seeking or changing speed afterward', () => {
    const h = fixture({ reset: 'async', media: [makeMedia({ paused: true, currentTime: 2, playbackRate: 1.5 })] });
    const video = h.media[0], timers = h.detached.ownerDocument.defaultView;
    const cancel = moveAttachmentHost(h.host, h.detached);
    try {
        assert.equal(timers.callbacks.size, 0);
        video.currentTime = 1; video.playbackRate = 2; video.play();
        timers.tick();
        video.emit('loadedmetadata');
        assert.equal(video.currentTime, 1);
        assert.equal(video.playbackRate, 2);
        assert.equal(video.paused, false);
    } finally { cancel(); }
});

test('returning before metadata loads inherits the original pending position and playing state', async () => {
    const h = fixture(), video = h.media[0];
    const firstCancel = moveAttachmentHost(h.host, h.detached);
    assert.equal(video.currentTime, 0);
    const cancel = moveAttachmentHost(h.host, h.home);
    try {
        firstCancel();
        assert.equal(video.listeners.get('loadedmetadata').size, 1);
        video.metadata();
        await Promise.resolve();
        assert.equal(video.currentTime, 12.5);
        assert.equal(video.playbackRate, 1.5);
        assert.equal(video.paused, false);
        assert.equal(video.playCalls, 1);
    } finally { cancel(); }
});

test('returning after playback progressed saves the current position and current pause state', async () => {
    const h = fixture(), video = h.media[0];
    moveAttachmentHost(h.host, h.detached);
    video.metadata();
    await Promise.resolve();
    video.currentTime = 27; video.playbackRate = 2; video.pause();
    const cancel = moveAttachmentHost(h.host, h.home);
    try {
        video.metadata();
        assert.equal(video.currentTime, 27);
        assert.equal(video.playbackRate, 2);
        assert.equal(video.paused, true);
        assert.equal(video.playCalls, 1);
    } finally { cancel(); }
});

test('cleanup before metadata prevents a late playback and removes event handlers', () => {
    const h = fixture(), video = h.media[0];
    const cancel = moveAttachmentHost(h.host, h.detached);
    cancel(); cancel();
    assert.equal(video.listeners.get('loadedmetadata').size, 0);
    assert.equal(video.listeners.get('emptied').size, 0);
    video.metadata();
    assert.equal(video.currentTime, 0);
    assert.equal(video.playCalls, 0);
    assert.equal(video.paused, true);
});

test('switching the media source prevents stale restore or pause of the replacement', () => {
    const h = fixture(), video = h.media[0];
    const cancel = moveAttachmentHost(h.host, h.detached);
    video.src = 'https://example.invalid/replacement.mp4';
    video.currentTime = 3; video.playbackRate = 2; video.autoplay = false; video.paused = false;
    video.metadata();
    assert.equal(video.currentTime, 3);
    assert.equal(video.playbackRate, 2);
    assert.equal(video.playCalls, 0);
    cancel();
    assert.equal(video.paused, false);
    assert.equal(video.autoplay, false);
});

test('detached or adopted elsewhere media cannot be restarted by late metadata', () => {
    for (const mutate of [
        (h) => { h.host.isConnected = false; },
        (h) => { h.media[0].isConnected = false; },
        (h) => { h.media[0].ownerDocument = h.home.ownerDocument; },
    ]) {
        const h = fixture(), video = h.media[0];
        const cancel = moveAttachmentHost(h.host, h.detached);
        mutate(h); video.autoplay = false; video.metadata();
        assert.equal(video.playCalls, 0);
        assert.equal(video.currentTime, 0);
        cancel();
    }
});

test('all audio/video elements restore independently and same-parent calls do not move or pause them', () => {
    const h = fixture({ media: [makeMedia(), makeMedia({ paused: true, currentTime: 4, playbackRate: 2, autoplay: false })] });
    const initial = moveAttachmentHost(h.host, h.home);
    initial();
    assert.equal(h.home.moves, 0);
    assert.equal(h.media[0].pauseCalls, 0);
    const cancel = moveAttachmentHost(h.host, h.detached);
    assert.equal(moveAttachmentHost(h.host, h.detached), cancel);
    assert.equal(h.detached.moves, 1);
    h.media.forEach((item) => item.metadata());
    assert.equal(h.media[0].currentTime, 12.5);
    assert.equal(h.media[0].paused, false);
    assert.equal(h.media[1].currentTime, 4);
    assert.equal(h.media[1].playbackRate, 2);
    assert.equal(h.media[1].paused, true);
    cancel();
});

test('cleanup cancels a pending native play attempt', async () => {
    let resolvePlay;
    const media = makeMedia({ play() { this.playCalls += 1; this.paused = false; this.canAutoplay = false;
        return new Promise((resolve) => { resolvePlay = resolve; }); } });
    const h = fixture({ media: [media] });
    const cancel = moveAttachmentHost(h.host, h.detached);
    media.metadata();
    assert.equal(media.paused, false);
    cancel();
    assert.equal(media.paused, true);
    resolvePlay();
    await Promise.resolve();
    assert.equal(media.paused, true);
    assert.equal(media.playCalls, 1);
});

test('destination-window polling restores metadata when closing PiP suppresses media events', () => {
    const h = fixture(), video = h.media[0];
    const cancel = moveAttachmentHost(h.host, h.detached);
    try {
        const timers = h.detached.ownerDocument.defaultView;
        assert.equal(h.home.ownerDocument.defaultView.callbacks.size, 0);
        assert.equal(timers.callbacks.size, 1);
        timers.tick();
        assert.equal(video.currentTime, 0);
        assert.equal(timers.callbacks.size, 1);
        video.readyState = 4;
        timers.tick();
        assert.equal(video.currentTime, 12.5);
        assert.equal(video.playbackRate, 1.5);
        assert.equal(video.paused, false);
        assert.equal(timers.callbacks.size, 0);
        video.currentTime = 15;
        video.emit('loadedmetadata');
        assert.equal(video.currentTime, 15, 'a delayed event does not seek back after polling restored playback');
    } finally { cancel(); }
});

test('metadata events and cleanup both cancel polling, and no metadata times out after ten seconds', () => {
    for (const finish of ['event', 'cleanup', 'timeout']) {
        const h = fixture(), video = h.media[0], timers = h.detached.ownerDocument.defaultView;
        const cancel = moveAttachmentHost(h.host, h.detached);
        if (finish === 'event') video.metadata();
        else if (finish === 'cleanup') cancel();
        else {
            for (let index = 0; index < 199; index += 1) timers.tick();
            assert.equal(timers.callbacks.size, 1);
            timers.tick();
        }
        assert.equal(timers.callbacks.size, 0);
        cancel();
    }
});

test('polling cannot restore a replaced source or a detached viewer', () => {
    for (const mutate of [
        (h) => { h.host.isConnected = false; },
        (h) => { h.media[0].src = 'https://example.invalid/another.mp4'; },
    ]) {
        const h = fixture(), video = h.media[0], timers = h.detached.ownerDocument.defaultView;
        const cancel = moveAttachmentHost(h.host, h.detached);
        mutate(h); video.readyState = 4;
        timers.tick();
        assert.equal(timers.callbacks.size, 0);
        assert.equal(video.currentTime, 0);
        assert.equal(video.playCalls, 0);
        cancel();
    }
});
