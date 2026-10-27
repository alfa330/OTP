import React, { useCallback, useEffect, useRef, useState } from 'react';
import jsQR from 'jsqr';
import { Camera, Check, ImagePlus, KeyRound, Loader2, QrCode, Trash2 } from 'lucide-react';
import { IosHint, IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosInput } from '../ui/ios';
import { errorOf, manualErrors } from './twofaMeta';

/*
 * Окна аккаунтов «Кодов 2FA»: добавить (QR или ключ) и поправить существующий.
 *
 * QR читается здесь, в браузере (BarcodeDetector, а где его нет — jsQR, как у «QR
 * доступа»): на сервер уходит только строка из QR, фото остаётся на устройстве. Строка
 * живёт в памяти окна, пока его не закрыли, — и не дольше.
 *
 * Два вида QR и экран их не различает: QR сервиса (один аккаунт) и QR «Перенос
 * аккаунтов → Экспорт» из Google Authenticator (несколько аккаунтов, при многих —
 * несколько QR подряд). Разбирает сервер (twofa/otpauth.py), а окно показывает список с
 * галочками: снять лишнее — например, критичный аккаунт, который в бота не кладут (ТЗ п. 8).
 */

const FIELD_LABEL = 'mb-1.5 flex items-center gap-1.5 px-1 text-[12.5px] font-medium text-slate-600';
const FIELD_ERROR = 'mt-1.5 px-1 text-[12px] text-rose-600';

const detectorOrNull = () => {
    if (!window.BarcodeDetector) return null;
    try {
        return new window.BarcodeDetector({ formats: ['qr_code'] });
    } catch (error) {
        return null;
    }
};

/* Фото с телефона — 12 Мп: jsQR на таком кадре думает секунды. Пробуем в двух размерах:
   плотный QR экспорта читается крупным, размытый — мельче. Кадр камеры — один размер и без
   инверсии: jsQR синхронный, и четыре прохода на кадр раз в 250 мс на iPhone (где нет
   BarcodeDetector) подвешивали окно. */
const PHOTO_SIZES = [1600, 900];
const VIDEO_SIZES = [800];

const decodeWithJsQr = (source, width, height, sizes = PHOTO_SIZES, inversion = 'attemptBoth') => {
    for (const limit of sizes) {
        const scale = Math.min(1, limit / Math.max(width, height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
        const frame = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const code = jsQR(frame.data, frame.width, frame.height, { inversionAttempts: inversion });
        if (code?.data) return String(code.data);
    }
    return null;
};

const decodeImageFile = async (file) => {
    const bitmap = await createImageBitmap(file);
    try {
        const detector = detectorOrNull();
        if (detector) {
            try {
                const found = await detector.detect(bitmap);
                if (found?.[0]?.rawValue) return String(found[0].rawValue);
            } catch (error) {
                /* у части браузеров детектор не берёт ImageBitmap — дальше jsQR */
            }
        }
        return decodeWithJsQr(bitmap, bitmap.width, bitmap.height);
    } finally {
        bitmap.close?.();
    }
};

/* Камера: задняя, если есть. Кадр раз в 250 мс; поймали QR — камера гаснет сразу. */
const useQrCamera = (onCode) => {
    const videoRef = useRef(null);
    const streamRef = useRef(null);
    const timerRef = useRef(null);
    const busyRef = useRef(false);
    // Номер запуска: окно закрыли или сменили режим, пока браузер спрашивал разрешение, —
    // пришедший потом поток надо сразу погасить, а не оставлять гореть индикатор камеры.
    const runRef = useRef(0);
    const onCodeRef = useRef(onCode);
    const [active, setActive] = useState(false);
    const [error, setError] = useState('');
    useEffect(() => { onCodeRef.current = onCode; }, [onCode]);

    const stop = useCallback(() => {
        runRef.current += 1;
        clearInterval(timerRef.current);
        timerRef.current = null;
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        if (videoRef.current) videoRef.current.srcObject = null;
        setActive(false);
    }, []);

    const start = useCallback(async () => {
        setError('');
        if (!navigator.mediaDevices?.getUserMedia) {
            setError('Камера в этом браузере недоступна — загрузите фото QR');
            return;
        }
        const run = runRef.current + 1;
        runRef.current = run;
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
            });
            const video = videoRef.current;
            if (run !== runRef.current || !video) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }
            streamRef.current = stream;
            setActive(true);
            video.srcObject = stream;
            await video.play();
            let detector = detectorOrNull();
            timerRef.current = setInterval(async () => {
                if (busyRef.current || !video.videoWidth) return;
                busyRef.current = true;
                try {
                    let text = null;
                    if (detector) {
                        try {
                            const found = await detector.detect(video);
                            text = found?.[0]?.rawValue ? String(found[0].rawValue) : null;
                        } catch (detectError) {
                            // Детектор есть, но не работает (Android без сервисов Google):
                            // дальше читаем кадры jsQR, а не смотрим впустую.
                            detector = null;
                        }
                    } else {
                        text = decodeWithJsQr(video, video.videoWidth, video.videoHeight, VIDEO_SIZES, 'dontInvert');
                    }
                    if (text) {
                        stop();
                        onCodeRef.current?.(text);
                    }
                } catch (scanError) {
                    /* битый кадр — ждём следующий */
                } finally {
                    busyRef.current = false;
                }
            }, 250);
        } catch (cameraError) {
            const denied = cameraError?.name === 'NotAllowedError' || cameraError?.name === 'SecurityError';
            setError(denied
                ? 'Доступ к камере запрещён — разрешите его в браузере или загрузите фото QR'
                : 'Не удалось включить камеру — загрузите фото QR');
            stop();
        }
    }, [stop]);

    useEffect(() => stop, [stop]);
    return { videoRef, active, error, start, stop };
};

const EntryRow = ({ entry, pick, onChange, error, inputRef }) => {
    const checked = Boolean(pick?.checked);
    const disabled = !entry.supported || Boolean(entry.duplicate_of);
    return (
        <div className={`rounded-xl px-3 py-2.5 ring-1 transition ${
            disabled ? 'bg-slate-50 ring-slate-200/70' : checked ? 'bg-blue-50/70 ring-blue-200' : 'bg-white ring-slate-200/70'
        }`}>
            <label className={`flex items-start gap-3 ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}>
                <span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md ring-1 transition ${
                    checked ? 'bg-blue-600 text-white ring-blue-600' : 'bg-white ring-slate-300'
                }`}>
                    {checked && <Check size={13} strokeWidth={3} />}
                </span>
                <input type="checkbox" className="sr-only" checked={checked} disabled={disabled}
                       onChange={(event) => onChange({ ...pick, checked: event.target.checked })} />
                <span className="min-w-0 grow basis-0">
                    <span className="block truncate text-[13.5px] font-semibold text-slate-800">
                        {entry.issuer || entry.name || 'Аккаунт без названия'}
                    </span>
                    {entry.issuer && entry.name && (
                        <span className="block truncate text-[12.5px] text-slate-500">{entry.name}</span>
                    )}
                    {!entry.supported && <span className="mt-0.5 block text-[12px] text-rose-600">{entry.reason}</span>}
                    {entry.supported && entry.duplicate_of && (
                        <span className="mt-0.5 block text-[12px] text-slate-500">Уже добавлен как «{entry.duplicate_of}»</span>
                    )}
                </span>
            </label>
            {checked && (
                <div className="mt-2.5 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,0.6fr)]">
                    <input ref={inputRef} className={`${iosInput} !py-2 ${error ? 'ring-2 ring-rose-300' : ''}`}
                           value={pick.title} maxLength={120} aria-label="Название"
                           placeholder="Название в разделе" onChange={(event) => onChange({ ...pick, title: event.target.value })} />
                    <input className={`${iosInput} !py-2`} value={pick.service} maxLength={80} aria-label="Сервис"
                           placeholder="Сервис" onChange={(event) => onChange({ ...pick, service: event.target.value })} />
                </div>
            )}
            {error && <p className="mt-1.5 px-1 text-[12px] text-rose-600">{error}</p>}
        </div>
    );
};

export const AddAccountSheet = ({ open, onClose, api, onSaved }) => {
    const [mode, setMode] = useState('qr');
    const [qrText, setQrText] = useState('');
    const [preview, setPreview] = useState(null);
    const [picks, setPicks] = useState({});
    const [manual, setManual] = useState({ title: '', service: '', secret: '' });
    const [errors, setErrors] = useState({});
    const [busy, setBusy] = useState('');
    const [problem, setProblem] = useState('');
    // Ошибки строк экспорта по индексу: «чего не хватает — красным у поля», а не текстом внизу.
    const [rowErrors, setRowErrors] = useState({});
    const rowRefs = useRef({});
    const fileRef = useRef(null);
    const titleRef = useRef(null);
    const secretRef = useRef(null);

    const reset = useCallback(() => {
        setQrText('');
        setPreview(null);
        setPicks({});
        setManual({ title: '', service: '', secret: '' });
        setErrors({});
        setRowErrors({});
        setProblem('');
        setBusy('');
    }, []);

    const readText = useCallback(async (text) => {
        setProblem('');
        setBusy('preview');
        try {
            const { data } = await api.post('/accounts/preview', { text });
            setQrText(text);
            setPreview(data);
            const next = {};
            (data.entries || []).forEach((entry) => {
                next[entry.index] = { checked: entry.supported && !entry.duplicate_of,
                                      title: entry.title || '', service: entry.issuer || '' };
            });
            setPicks(next);
        } catch (requestError) {
            setProblem(errorOf(requestError, 'Не удалось разобрать QR'));
        } finally {
            setBusy('');
        }
    }, [api]);

    const camera = useQrCamera(readText);

    useEffect(() => {
        if (!open) {
            camera.stop();
            reset();
        }
    }, [open, camera.stop, reset]);

    const pickFile = async (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file) return;
        camera.stop();
        setProblem('');
        setBusy('decode');
        try {
            const text = await decodeImageFile(file);
            if (!text) {
                setProblem('QR на фото не найден. Снимите ближе и ровнее, чтобы QR занял почти весь кадр');
                setBusy('');
                return;
            }
            await readText(text);
        } catch (decodeError) {
            setProblem('Фото не открылось — попробуйте другое');
            setBusy('');
        }
    };

    const chosen = Object.entries(picks).filter(([, pick]) => pick.checked);

    const focusRow = (index) => {
        const input = rowRefs.current[index];
        input?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
        input?.focus?.();
    };

    const saveQr = async () => {
        const missing = {};
        chosen.forEach(([index, pick]) => { if (!String(pick.title || '').trim()) missing[index] = 'Укажите название'; });
        setRowErrors(missing);
        const first = Object.keys(missing)[0];
        if (first !== undefined) {
            focusRow(first);
            return;
        }
        setBusy('save');
        try {
            await api.post('/accounts', {
                text: qrText,
                items: chosen.map(([index, pick]) => ({ index: Number(index), title: pick.title, service: pick.service })),
            });
            onSaved?.({ count: chosen.length, batch: preview?.batch || null });
            reset();
            onClose();
        } catch (requestError) {
            // Ответ сервера про конкретную строку (дубль, неподходящий код, название) — к ней.
            const index = requestError?.response?.data?.index;
            const message = errorOf(requestError, 'Аккаунты не сохранились');
            if (index !== undefined && index !== null && picks[index]) {
                setRowErrors({ [index]: message });
                focusRow(index);
            } else {
                setProblem(message);
            }
            setBusy('');
        }
    };

    const saveManual = async () => {
        const found = manualErrors(manual);
        setErrors(found);
        if (found.title) { titleRef.current?.focus(); return; }
        if (found.secret) { secretRef.current?.focus(); return; }
        setBusy('save');
        try {
            await api.post('/accounts', {
                text: manual.secret,
                items: [{ index: 0, title: manual.title, service: manual.service }],
            });
            onSaved?.({ count: 1, batch: null });
            reset();
            onClose();
        } catch (requestError) {
            const message = errorOf(requestError, 'Аккаунт не сохранился');
            if (['TWOFA_PARSE', 'TWOFA_UNSUPPORTED', 'TWOFA_DUPLICATE'].includes(requestError?.response?.data?.code)) {
                setErrors({ secret: message });
                secretRef.current?.focus();
            } else {
                setProblem(message);
            }
            setBusy('');
        }
    };

    const footer = mode === 'qr' ? (
        <div className="flex w-full items-center justify-end gap-2">
            <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
            <button type="button" className={iosBtnPrimary} disabled={!preview || !chosen.length || busy === 'save'} onClick={saveQr}>
                {busy === 'save' && <Loader2 size={15} className="animate-spin" />}
                {chosen.length > 1 ? `Добавить ${chosen.length}` : 'Добавить'}
            </button>
        </div>
    ) : (
        <div className="flex w-full items-center justify-end gap-2">
            <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
            <button type="button" className={iosBtnPrimary} disabled={busy === 'save'} onClick={saveManual}>
                {busy === 'save' && <Loader2 size={15} className="animate-spin" />}
                Добавить
            </button>
        </div>
    );

    return (
        <IosModal open={open} onClose={onClose} title="Новый аккаунт" footer={footer} maxWidth="max-w-xl">
            <div className="space-y-4">
                <IosSegmented
                    value={mode}
                    onChange={(value) => { camera.stop(); setProblem(''); setMode(value); }}
                    options={[{ value: 'qr', label: 'QR-код' }, { value: 'key', label: 'Ключ вручную' }]}
                    stretch
                    ariaLabel="Как добавить"
                />

                {mode === 'qr' && !preview && (
                    <div className="space-y-3">
                        <p className="flex items-center gap-1.5 px-1 text-[13px] text-slate-600">
                            QR из настроек 2FA сервиса или экспорт из Google Authenticator
                            <IosHint
                                label="Где взять QR"
                                text="Уже настроенные аккаунты: в Google Authenticator на телефоне владельца — меню «Перенос аккаунтов» → «Экспорт», отметьте нужные аккаунты. Экран экспорта не даёт сделать скриншот — снимите его другим телефоном или камерой здесь. Новый аккаунт: QR, который сервис показывает при подключении приложения-аутентификатора."
                            />
                        </p>
                        <div className="relative overflow-hidden rounded-2xl bg-slate-900" style={{ aspectRatio: camera.active ? '4 / 3' : undefined }}>
                            <video ref={camera.videoRef} className={camera.active ? 'h-full w-full object-cover' : 'hidden'} playsInline muted />
                            {camera.active && (
                                <div className="pointer-events-none absolute inset-[12%] rounded-2xl ring-2 ring-white/80" />
                            )}
                            {!camera.active && (
                                <div className="grid gap-2 p-4 sm:grid-cols-2">
                                    <button type="button" onClick={camera.start} disabled={Boolean(busy)}
                                            className="flex h-24 flex-col items-center justify-center gap-1.5 rounded-xl bg-white/10 text-[13px] font-semibold text-white transition hover:bg-white/15 disabled:opacity-50">
                                        <Camera size={22} /> Снять камерой
                                    </button>
                                    <button type="button" onClick={() => fileRef.current?.click()} disabled={Boolean(busy)}
                                            className="flex h-24 flex-col items-center justify-center gap-1.5 rounded-xl bg-white/10 text-[13px] font-semibold text-white transition hover:bg-white/15 disabled:opacity-50">
                                        {busy ? <Loader2 size={22} className="animate-spin" /> : <ImagePlus size={22} />}
                                        {busy ? 'Читаем QR…' : 'Фото или скриншот'}
                                    </button>
                                </div>
                            )}
                        </div>
                        {camera.active && (
                            <button type="button" className={`${iosBtnSecondary} w-full`} onClick={camera.stop}>Выключить камеру</button>
                        )}
                        <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={pickFile} />
                        {(camera.error || problem) && <p className={FIELD_ERROR}>{camera.error || problem}</p>}
                    </div>
                )}

                {mode === 'qr' && preview && (
                    <div className="space-y-2.5">
                        <div className="flex items-center justify-between gap-3 px-1">
                            <span className="flex items-center gap-1.5 text-[13px] font-semibold text-slate-700">
                                <QrCode size={15} className="text-slate-400" />
                                {preview.kind === 'migration' ? 'Экспорт Google Authenticator' : 'QR аккаунта'}
                                {preview.batch && preview.batch.size > 1 && (
                                    <span className="font-normal text-slate-500">· QR {preview.batch.index + 1} из {preview.batch.size}</span>
                                )}
                            </span>
                            <button type="button" className="text-[12.5px] font-medium text-blue-600 hover:text-blue-700"
                                    onClick={() => { setPreview(null); setQrText(''); setPicks({}); setProblem(''); }}>
                                Другой QR
                            </button>
                        </div>
                        {(preview.entries || []).map((entry) => (
                            <EntryRow key={entry.index} entry={entry} pick={picks[entry.index]}
                                      error={rowErrors[entry.index]}
                                      inputRef={(node) => { rowRefs.current[entry.index] = node; }}
                                      onChange={(pick) => {
                                          setPicks((prev) => ({ ...prev, [entry.index]: pick }));
                                          setRowErrors((prev) => ({ ...prev, [entry.index]: undefined }));
                                      }} />
                        ))}
                        {problem && <p className={FIELD_ERROR}>{problem}</p>}
                    </div>
                )}

                {mode === 'key' && (
                    <div className="space-y-3">
                        <div>
                            <label className={FIELD_LABEL} htmlFor="twofa-title">Название</label>
                            <input id="twofa-title" ref={titleRef} className={`${iosInput} ${errors.title ? 'ring-2 ring-rose-300' : ''}`}
                                   value={manual.title} maxLength={120} placeholder="Google Ads — основной кабинет"
                                   onChange={(event) => { setManual((prev) => ({ ...prev, title: event.target.value })); setErrors((prev) => ({ ...prev, title: undefined })); }} />
                            {errors.title && <p className={FIELD_ERROR}>{errors.title}</p>}
                        </div>
                        <div>
                            <label className={FIELD_LABEL} htmlFor="twofa-service">Сервис</label>
                            <input id="twofa-service" className={iosInput} value={manual.service} maxLength={80}
                                   placeholder="Google, Meta, TikTok…"
                                   onChange={(event) => setManual((prev) => ({ ...prev, service: event.target.value }))} />
                        </div>
                        <div>
                            <label className={FIELD_LABEL} htmlFor="twofa-secret">
                                Ключ
                                <IosHint
                                    label="Где взять ключ"
                                    text="Сервис показывает его при подключении приложения-аутентификатора — ссылкой «Не можете отсканировать?» под QR. Строка из латинских букв и цифр 2–7; пробелы можно оставить. После сохранения ключ не показывается никому."
                                />
                            </label>
                            <input id="twofa-secret" ref={secretRef} autoComplete="off" spellCheck={false}
                                   className={`${iosInput} font-mono ${errors.secret ? 'ring-2 ring-rose-300' : ''}`}
                                   value={manual.secret} placeholder="JBSW Y3DP EHPK 3PXP"
                                   onChange={(event) => { setManual((prev) => ({ ...prev, secret: event.target.value })); setErrors((prev) => ({ ...prev, secret: undefined })); }} />
                            {errors.secret && <p className={FIELD_ERROR}>{errors.secret}</p>}
                        </div>
                        {problem && <p className={FIELD_ERROR}>{problem}</p>}
                    </div>
                )}
            </div>
        </IosModal>
    );
};

export const AccountSheet = ({ account, onClose, api, onSaved, onRemoved, peopleNames }) => {
    const [title, setTitle] = useState('');
    const [service, setService] = useState('');
    const [busy, setBusy] = useState('');
    const [problem, setProblem] = useState('');
    const [confirmRemove, setConfirmRemove] = useState(false);

    useEffect(() => {
        setTitle(account?.title || '');
        setService(account?.service || '');
        setProblem('');
        setBusy('');
        setConfirmRemove(false);
    }, [account]);

    if (!account) return <IosModal open={false} onClose={onClose} title="" />;

    const save = async () => {
        if (!title.trim()) { setProblem('Укажите название'); return; }
        setBusy('save');
        try {
            await api.patch(`/accounts/${account.id}`, { title, service });
            onSaved?.();
            onClose();
        } catch (requestError) {
            setProblem(errorOf(requestError, 'Не сохранилось'));
            setBusy('');
        }
    };

    const remove = async () => {
        if (!confirmRemove) { setConfirmRemove(true); return; }
        setBusy('remove');
        try {
            await api.delete(`/accounts/${account.id}`);
            onRemoved?.(account);
            onClose();
        } catch (requestError) {
            setProblem(errorOf(requestError, 'Не удалилось'));
            setBusy('');
        }
    };

    const names = (account.user_ids || []).map((id) => peopleNames?.[id]).filter(Boolean);

    return (
        <IosModal
            open={Boolean(account)}
            onClose={onClose}
            title={account.title}
            subtitle={account.service || null}
            maxWidth="max-w-lg"
            footer={(
                <div className="flex w-full items-center justify-between gap-2">
                    <button type="button" onClick={remove} disabled={Boolean(busy)}
                            className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-semibold transition ${
                                confirmRemove ? 'bg-rose-600 text-white hover:bg-rose-700' : 'text-rose-600 hover:bg-rose-50'
                            }`}>
                        {busy === 'remove' ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        {confirmRemove ? 'Удалить вместе с ключом' : 'Удалить'}
                    </button>
                    <div className="flex items-center gap-2">
                        <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={save} disabled={Boolean(busy)}>
                            {busy === 'save' && <Loader2 size={15} className="animate-spin" />}
                            Сохранить
                        </button>
                    </div>
                </div>
            )}
        >
            <div className="space-y-3">
                <div>
                    <label className={FIELD_LABEL} htmlFor="twofa-edit-title">Название</label>
                    <input id="twofa-edit-title" className={iosInput} value={title} maxLength={120}
                           onChange={(event) => { setTitle(event.target.value); setProblem(''); }} />
                </div>
                <div>
                    <label className={FIELD_LABEL} htmlFor="twofa-edit-service">Сервис</label>
                    <input id="twofa-edit-service" className={iosInput} value={service} maxLength={80}
                           placeholder="Google, Meta, TikTok…" onChange={(event) => setService(event.target.value)} />
                </div>
                <div className="flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-[12.5px] text-slate-600">
                    <KeyRound size={14} className="mt-0.5 shrink-0 text-slate-400" />
                    <span>
                        Ключ хранится зашифрованным и не показывается. Чтобы сменить ключ, удалите аккаунт и добавьте заново.
                        {names.length > 0 && <> Доступ: {names.join(', ')}.</>}
                    </span>
                </div>
                {confirmRemove && (
                    <p className="px-1 text-[12.5px] text-rose-600">
                        Ключ сотрётся без возврата, доступы снимутся. Журнал выдач останется.
                    </p>
                )}
                {problem && <p className={FIELD_ERROR}>{problem}</p>}
            </div>
        </IosModal>
    );
};
