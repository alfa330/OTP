import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Copy, Loader2, Lock, RefreshCw } from 'lucide-react';
import { APPLE_FONT, iosBtnGhost, iosBtnPrimary, iosCard } from '../ui/ios';
import { formatCountdown } from './workspaceStatus';
import { loadWorkspace, requestWorkspaceQr } from './workspaceStore';

/* Экран закрытого раздела у верификатора. Код здесь ОБЫЧНЫЙ QR портала — тот же,
 * что у «Вики» и «Обращений» (решение владельца 09.10.2026): супервайзер,
 * администратор или глава отдела подтверждает его в «QR доступ» одним сканом.
 * Строку кода собирает сервер (/api/wazzup/workspace/qr) — своей копии нет.
 *
 * Код показан СРАЗУ, без кнопки «Сгенерировать»: человек открыл раздел затем,
 * чтобы начать работать, и первое, что ему нужно, — показать экран старшему.
 * Раздел сам узнаёт о подтверждении: пока код действует, раз в несколько секунд
 * спрашивает сервер и открывается без перезагрузки. После истечения QR опрос
 * замедляется, но не прекращается: доступ могли подтвердить и с экрана «Вики».
 *
 * Замок — удобство, а не защита: доступом является ответ сервера, каждая ручка
 * чатов закрыта тем же правилом (wazzup/access.py). */

const POLL_MS = 2500;

export default function ChatAccessGate() {
    const [qr, setQr] = useState(null);            // { payload, expiresAt }
    const [image, setImage] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [left, setLeft] = useState(0);
    const [copied, setCopied] = useState(false);
    const alive = useRef(true);
    const requestId = useRef(0);
    const requesting = useRef(false);

    const showCode = useCallback(async () => {
        if (requesting.current) return;
        requesting.current = true;
        const id = ++requestId.current;
        setLoading(true);
        setError('');
        setQr(null);
        setImage('');
        setCopied(false);
        try {
            const data = await requestWorkspaceQr();
            if (!alive.current || id !== requestId.current) return;
            // Доступ уже открыт (подтвердили, пока экран грузился) — раздел откроется сам.
            if (data.granted) { loadWorkspace(); return; }
            const expiresAt = Date.parse(data.token_expires_at);
            setImage('');
            setQr({ payload: data.qr_payload, expiresAt: Number.isFinite(expiresAt) ? expiresAt : Date.now() + 300000 });
        } catch (requestError) {
            if (alive.current && id === requestId.current) setError(requestError?.response?.data?.error || 'Не удалось получить код');
        } finally {
            if (id === requestId.current) {
                requesting.current = false;
                if (alive.current) setLoading(false);
            }
        }
    }, []);

    useEffect(() => {
        alive.current = true;
        showCode();
        return () => { alive.current = false; requestId.current += 1; requesting.current = false; };
    }, [showCode]);

    /* Рисуем код у себя: строка с действующим кодом доступа наружу не уходит.
       Библиотека грузится по требованию — экран видят раз за сессию. */
    useEffect(() => {
        if (!qr?.payload) return undefined;
        let cancelled = false;
        import('qrcode')
            .then((mod) => (mod.default || mod).toDataURL(qr.payload, {
                errorCorrectionLevel: 'M', margin: 2, width: 1024,
            }))
            .then((url) => { if (!cancelled) setImage(url); })
            .catch(() => { if (!cancelled) setError('Не удалось нарисовать код — скопируйте его строкой'); });
        return () => { cancelled = true; };
    }, [qr?.payload]);

    // Истёкший QR не отменяет подтверждения: его могли дать и с другого экрана.
    useEffect(() => {
        if (!qr) return undefined;
        const tick = () => setLeft(Math.max(0, Math.ceil((qr.expiresAt - Date.now()) / 1000)));
        tick();
        const clock = setInterval(tick, 1000);
        let lastPoll = 0;
        const refresh = () => {
            if (document.visibilityState === 'hidden') return;
            lastPoll = Date.now();
            loadWorkspace();
        };
        const poll = setInterval(() => {
            if (Date.now() < qr.expiresAt || Date.now() - lastPoll >= 30000) refresh();
        }, POLL_MS);
        document.addEventListener('visibilitychange', refresh);
        return () => {
            clearInterval(clock);
            clearInterval(poll);
            document.removeEventListener('visibilitychange', refresh);
        };
    }, [qr]);

    const expired = Boolean(qr) && left <= 0;

    const copy = () => {
        if (!qr?.payload) return;
        const done = () => { if (!alive.current) return; setCopied(true); setTimeout(() => { if (alive.current) setCopied(false); }, 1600); };
        navigator.clipboard?.writeText(qr.payload).then(done).catch(() => {});
    };

    return (
        <div className="mx-auto w-full max-w-md px-4 py-8 sm:py-12" style={{ fontFamily: APPLE_FONT }}>
            <div className={`${iosCard} p-6 sm:p-7`}>
                <div className="flex flex-col items-center text-center">
                    <div className="grid h-14 w-14 place-items-center rounded-2xl bg-blue-50 text-blue-600 ring-1 ring-blue-100">
                        <Lock size={22} strokeWidth={1.9} />
                    </div>
                    <h2 className="mt-4 text-[19px] font-semibold text-slate-900">
                        Покажите код старшему
                    </h2>
                    <p className="mt-2 text-[13.5px] leading-relaxed text-slate-600">
                        Супервайзер, администратор или глава отдела отсканирует его в разделе
                        «QR доступ». Чаты откроются сами.
                    </p>
                </div>

                <div className="mt-5 flex justify-center">
                    <div className="relative grid aspect-square w-full max-w-[280px] place-items-center overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200">
                        {image && (
                            <img src={image} alt="Код доступа к чатам"
                                className={`h-full w-full transition duration-200 ${expired ? 'opacity-10 blur-[2px]' : ''}`} />
                        )}
                        {!image && !error && <Loader2 size={22} className="animate-spin text-slate-300" />}
                        {expired && (
                            <div className="absolute inset-0 grid place-items-center">
                                <button type="button" onClick={showCode} disabled={loading} className={iosBtnPrimary}>
                                    {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                                    Показать новый код
                                </button>
                            </div>
                        )}
                    </div>
                </div>

                {error && (
                    <div role="alert" className="mt-4 rounded-xl bg-rose-50 px-3.5 py-2.5 text-center text-[12.5px] text-rose-700">
                        {error}{' '}
                        <button type="button" onClick={showCode} disabled={loading} className="font-semibold underline">Повторить</button>
                    </div>
                )}

                {qr && !error && (
                    <div className="mt-3 flex items-center justify-between gap-2 text-[12.5px] text-slate-500">
                        <span className="tabular-nums" aria-live="off">
                            {expired ? 'Срок кода истёк' : `Код действует ещё ${formatCountdown(left)}`}
                        </span>
                        {/* Строка кода — запасной путь для сканера без камеры; прячем её
                            за кнопку: она нужна одному человеку из двадцати. */}
                        {!expired && (
                            <button type="button" onClick={copy} className={`${iosBtnGhost} !px-2 !py-1 text-[12.5px]`}
                                title="Скопировать код строкой — если у супервайзера нет камеры">
                                <Copy size={13} /> {copied ? 'Скопировано' : 'Код строкой'}
                            </button>
                        )}
                    </div>
                )}

                <p className="mt-4 border-t border-slate-100 pt-3 text-center text-[11.5px] leading-relaxed text-slate-500">
                    Доступ действует на этом устройстве, пока вы не выйдете из портала.
                </p>
            </div>
        </div>
    );
}
