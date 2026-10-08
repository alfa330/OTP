import React, { useEffect, useRef, useState } from 'react';
import { Download, Loader2, Pause, Play, RotateCcw } from 'lucide-react';
import './chatAudioPlayer.css';

const SPEEDS = [1, 1.5, 2];
const MAX_SEEKABLE_COPY_BYTES = 32 * 1024 * 1024;
let activeAudio = null;

async function boundedAudioCopy(response, signal) {
    if (!response.ok || Number(response.headers.get('Content-Length')) > MAX_SEEKABLE_COPY_BYTES) return null;
    const reader = response.body?.getReader();
    if (!reader) return null;
    const chunks = [];
    let size = 0, complete = false;
    try {
        while (!signal.aborted) {
            const { done, value } = await reader.read();
            if (done) { complete = true; break; }
            size += value.byteLength;
            if (size > MAX_SEEKABLE_COPY_BYTES) return null;
            chunks.push(value);
        }
        return complete && size && !signal.aborted
            ? new Blob(chunks, { type: response.headers.get('Content-Type') || '' }) : null;
    } finally {
        if (!complete) await reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}

const timeLabel = (value) => {
    const seconds = Math.max(0, Math.floor(Number(value) || 0));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};

function AudioPlayer({ src, label = 'Аудиосообщение' }) {
    const audioRef = useRef(null);
    const alive = useRef(true);
    const playAttempt = useRef(0);
    const requested = useRef(false);
    const scrub = useRef(null);
    const pendingSeek = useRef(false);
    const seekableCopy = useRef({ attempted: false, controller: null, url: null, timer: null });
    const replacement = useRef(null);
    const [playing, setPlaying] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(false);
    const [position, setPosition] = useState(0);
    const [duration, setDuration] = useState(0);
    const [speed, setSpeed] = useState(1);

    useEffect(() => {
        alive.current = true;
        const audio = audioRef.current;
        return () => {
            alive.current = false;
            playAttempt.current += 1;
            requested.current = false;
            seekableCopy.current.controller?.abort();
            clearTimeout(seekableCopy.current.timer);
            if (activeAudio === audio) activeAudio = null;
            audio.pause();
            // Release the request and decoded media when changing conversations.
            audio.removeAttribute('src');
            audio.load();
            if (seekableCopy.current.url) URL.revokeObjectURL(seekableCopy.current.url);
        };
    }, []);

    const recoverSeekableCopy = async () => {
        const copy = seekableCopy.current;
        if (copy.attempted || typeof fetch !== 'function' || /^(blob:|data:)/i.test(src)) return;
        copy.attempted = true;
        const controller = new AbortController();
        copy.controller = controller;
        copy.timer = setTimeout(() => controller.abort(), 15000);
        try {
            // Some media hosts ignore Range; Chromium then reports OGG duration
            // and seekable.end as Infinity. A complete local copy is seekable.
            // Normal audio never takes this path or downloads a second time.
            const response = await fetch(src, { signal: controller.signal, credentials: 'omit' });
            const blob = await boundedAudioCopy(response, controller.signal);
            if (!alive.current || controller.signal.aborted || !blob) return;
            const audio = audioRef.current;
            // The original may have become seekable while its bytes arrived.
            if (Number.isFinite(audio.duration) && audio.duration > 0) return;
            copy.url = URL.createObjectURL(blob);
            replacement.current = {
                position: Number.isFinite(audio.currentTime) ? audio.currentTime : 0,
                resume: requested.current && activeAudio === audio,
            };
            playAttempt.current += 1; // load() aborts the original play promise.
            pendingSeek.current = false;
            setLoading(replacement.current.resume);
            audio.preload = 'metadata';
            audio.src = copy.url;
            audio.load();
        } catch {
            // CORS/network failures must leave the original playback intact.
        } finally {
            clearTimeout(copy.timer);
            controller.abort();
            copy.controller = null;
        }
    };

    const syncTime = () => {
        if (!alive.current) return;
        const audio = audioRef.current;
        const restore = replacement.current;
        if (restore && !(Number.isFinite(audio.duration) && audio.duration > 0)) return;
        if (restore && Number.isFinite(audio.duration) && audio.duration > 0) {
            replacement.current = null;
            const next = Math.min(restore.position, audio.duration);
            if (Math.abs(audio.currentTime - next) > 0.01) audio.currentTime = next;
            audio.playbackRate = speed;
            if (restore.resume && requested.current && activeAudio === audio) {
                const attempt = ++playAttempt.current;
                audio.play().catch((reason) => {
                    if (alive.current && attempt === playAttempt.current) {
                        stop();
                        if (reason?.name !== 'AbortError') setError(true);
                    }
                });
            } else {
                stop();
            }
        }
        // Range inputs dispatch a change for every pointer movement. While the
        // user chooses a position, playback/buffering must not move its thumb or
        // change its scale. Keep the requested position until the seek finishes.
        if (scrub.current) return;
        if (!pendingSeek.current && !audio.seeking) {
            setPosition(Number.isFinite(audio.currentTime) ? audio.currentTime : 0);
        }
        const total = Number.isFinite(audio.duration) ? audio.duration
            : audio.seekable?.length ? audio.seekable.end(audio.seekable.length - 1) : 0;
        if (total === Infinity) {
            setDuration(0);
            void recoverSeekableCopy();
        }
        if (Number.isFinite(total) && total > 0) {
            setDuration((previous) => Number.isFinite(audio.duration) ? total : Math.max(previous, total));
        }
    };

    const stop = () => {
        if (replacement.current) replacement.current.resume = false;
        requested.current = false;
        playAttempt.current += 1;
        if (!alive.current) return;
        setPlaying(false);
        setLoading(false);
    };

    const fail = () => {
        scrub.current = null;
        pendingSeek.current = false;
        replacement.current = null;
        stop();
        if (alive.current) setError(true);
    };

    const togglePlay = async () => {
        const audio = audioRef.current;
        if (requested.current || !audio.paused) {
            stop();
            audio.pause();
            return;
        }
        if (activeAudio && activeAudio !== audio) activeAudio.pause();
        activeAudio = audio;
        requested.current = true;
        const attempt = ++playAttempt.current;
        setError(false);
        setLoading(true);
        if (replacement.current) {
            replacement.current.resume = true;
            return;
        }
        // Assign the source only on demand: opening a thread downloads no audio.
        if (!audio.getAttribute('src')) audio.src = src;
        else if (error || audio.error) audio.load();
        if (audio.ended) audio.currentTime = 0;
        audio.playbackRate = speed;
        try {
            await audio.play();
        } catch (reason) {
            if (alive.current && attempt === playAttempt.current) {
                stop();
                if (reason?.name !== 'AbortError') setError(true);
            }
        }
    };

    const changeSpeed = () => {
        const next = SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length];
        audioRef.current.playbackRate = next;
        setSpeed(next);
    };

    const commitSeek = (value, total = duration) => {
        if (!Number.isFinite(total) || total <= 0) return;
        const next = Math.max(0, Math.min(total, Number(value) || 0));
        const audio = audioRef.current;
        if (!audio.seeking && Math.abs(audio.currentTime - next) < 0.01) {
            pendingSeek.current = false;
            setPosition(next);
            return;
        }
        // Set this before currentTime: the media may emit timeupdate while its
        // decoder is still seeking through the beginning of an OGG recording.
        pendingSeek.current = true;
        audio.currentTime = next;
        setPosition(next);
    };

    const seek = (event) => {
        if (scrub.current) {
            const next = Math.max(0, Math.min(scrub.current.duration, Number(event.target.value) || 0));
            scrub.current.position = next;
            setPosition(next);
        } else {
            // Native keyboard/screen-reader changes do not start pointer drags.
            commitSeek(event.target.value);
        }
    };

    const startScrub = (event) => {
        if (!duration || (event.button != null && event.button !== 0)) return;
        scrub.current = { pointerId: event.pointerId, duration, position };
        event.currentTarget.setPointerCapture?.(event.pointerId);
    };

    const finishScrub = (event) => {
        const selection = scrub.current;
        if (!selection || selection.pointerId !== event.pointerId) return;
        scrub.current = null;
        commitSeek(selection.position, selection.duration);
    };

    const cancelScrub = (event) => {
        if (!scrub.current || scrub.current.pointerId !== event.pointerId) return;
        scrub.current = null;
        syncTime();
    };

    const nextSpeed = SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length];
    const playLabel = error ? 'Повторить загрузку аудио'
        : loading && !playing ? 'Остановить загрузку аудио'
            : playing ? 'Приостановить аудио' : 'Воспроизвести аудио';
    const progress = duration > 0 ? Math.min(100, (position / duration) * 100) : 0;

    return <div className="wazzup-audio" role="group" aria-label={label}
        onDoubleClick={(event) => event.stopPropagation()}>
        <audio ref={audioRef} preload="none" className="hidden" aria-hidden="true"
            onLoadedMetadata={syncTime} onDurationChange={syncTime} onTimeUpdate={syncTime}
            onSeeked={() => { pendingSeek.current = false; syncTime(); }}
            onPlay={() => { if (alive.current) setPlaying(true); }}
            onPlaying={() => { if (alive.current) { setPlaying(true); setLoading(false); } }}
            onWaiting={() => { if (alive.current && requested.current) setLoading(true); }}
            onPause={() => { if (!replacement.current) stop(); }}
            onEnded={() => { syncTime(); stop(); }} onError={fail} />
        <button type="button" className="wazzup-audio-play" onClick={togglePlay}
            aria-label={playLabel} title={playLabel}>
            {error ? <RotateCcw size={19} aria-hidden="true" />
                : loading ? <Loader2 size={21} className="animate-spin" aria-hidden="true" />
                    : playing ? <Pause size={19} fill="currentColor" aria-hidden="true" />
                        : <Play size={19} fill="currentColor" className="translate-x-px" aria-hidden="true" />}
        </button>
        <div className="wazzup-audio-timeline">
            <input type="range" className="wazzup-audio-seek" min="0" max={duration || 1}
                step="0.1" value={Math.min(position, duration || 0)} onChange={seek}
                onPointerDown={startScrub} onPointerUp={finishScrub}
                onPointerCancel={cancelScrub} onLostPointerCapture={cancelScrub}
                disabled={!duration || error} aria-label="Позиция воспроизведения аудио"
                title={!duration ? 'Перемотка пока недоступна для этой записи' : undefined}
                aria-valuetext={`${timeLabel(position)} из ${duration ? timeLabel(duration) : 'неизвестной длительности'}`}
                style={{ '--audio-progress': `${progress}%` }} />
            <div className="wazzup-audio-caption">
                <span className="tabular-nums">{timeLabel(position)} <span aria-hidden="true">/</span> {duration ? timeLabel(duration) : '—:—'}</span>
                <span role="status">{error ? 'Ошибка загрузки' : loading ? 'Загрузка…' : ''}</span>
            </div>
        </div>
        <button type="button" className="wazzup-audio-speed" onClick={changeSpeed}
            aria-label={`Скорость ${speed}×. Переключить на ${nextSpeed}×`}
            title={`Скорость воспроизведения: ${speed}×`}>
            {speed}×
        </button>
        {error && <a className="wazzup-audio-original" href={src} target="_blank" rel="noopener noreferrer"
            aria-label="Открыть исходное аудио" title="Открыть исходное аудио">
            <Download size={17} aria-hidden="true" />
        </a>}
    </div>;
}

export default function ChatAudioPlayer(props) {
    // A replaced URL gets a fresh player; the old one stops and releases media.
    return <AudioPlayer key={props.src} {...props} />;
}
