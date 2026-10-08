import React, { useEffect, useRef, useState } from 'react';
import { Download, Loader2, Pause, Play, RotateCcw } from 'lucide-react';
import './chatAudioPlayer.css';

const SPEEDS = [1, 1.5, 2];
let activeAudio = null;

const timeLabel = (value) => {
    const seconds = Math.max(0, Math.floor(Number(value) || 0));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};

function AudioPlayer({ src, label = 'Аудиосообщение' }) {
    const audioRef = useRef(null);
    const alive = useRef(true);
    const playAttempt = useRef(0);
    const requested = useRef(false);
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
            if (activeAudio === audio) activeAudio = null;
            audio.pause();
            // Release the request and decoded media when changing conversations.
            audio.removeAttribute('src');
            audio.load();
        };
    }, []);

    const syncTime = () => {
        if (!alive.current) return;
        const audio = audioRef.current;
        setPosition(Number.isFinite(audio.currentTime) ? audio.currentTime : 0);
        const total = Number.isFinite(audio.duration) ? audio.duration
            : audio.seekable?.length ? audio.seekable.end(audio.seekable.length - 1) : 0;
        setDuration(Math.max(0, total || 0));
    };

    const stop = () => {
        requested.current = false;
        playAttempt.current += 1;
        if (!alive.current) return;
        setPlaying(false);
        setLoading(false);
    };

    const fail = () => {
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

    const seek = (event) => {
        if (!duration) return;
        const next = Math.max(0, Math.min(duration, Number(event.target.value) || 0));
        audioRef.current.currentTime = next;
        setPosition(next);
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
            onPlay={() => { if (alive.current) setPlaying(true); }}
            onPlaying={() => { if (alive.current) { setPlaying(true); setLoading(false); } }}
            onWaiting={() => { if (alive.current && requested.current) setLoading(true); }}
            onPause={stop} onEnded={() => { syncTime(); stop(); }} onError={fail} />
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
                disabled={!duration || error} aria-label="Позиция воспроизведения аудио"
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
