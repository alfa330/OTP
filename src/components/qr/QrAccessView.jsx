import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import jsQR from 'jsqr';
import {
    AlertCircle, Camera, CameraOff, CheckCircle2, Flashlight, FlashlightOff,
    Keyboard, Loader2, QrCode, ShieldCheck, UserRound,
} from 'lucide-react';
import { APPLE_FONT, IosModal, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import './qr-access.css';

/**
 * Раздел «QR доступ» — сканер, открывающий сотруднику закрытые разделы.
 *
 * КАК ЭТО РАБОТАЕТ ТЕПЕРЬ (постановка владельца 10.09.2026: «как в
 * телеграмме»). Зашёл в раздел — камера уже ловит код; поймала — во весь
 * экран выезжает карточка с ИМЕНЕМ и фотографией того, кому открывается
 * доступ, и двумя кнопками: открыть или отмена. Всё.
 *
 * ЧТО БЫЛО ДО. Первым на экране стояло поле «Токен / строка из QR» на три
 * строки, сканер — вторым, и запускался он кнопкой. Подтверждали при этом не
 * человека, а строку: в окне подтверждения печаталась сама подпись токена, и
 * узнать по ней, кого пускаешь, было нельзя — имя приходило с сервера уже
 * ПОСЛЕ выдачи. Отсюда ручка предпросмотра (`/api/sensitive-access/qr/preview`)
 * и этот компонент.
 *
 * ПОЧЕМУ ОДИН КОМПОНЕНТ. Разметка раздела лежала в App.jsx ДВАЖДЫ — своя
 * копия у администраторов и своя у глав отделов с тренерами, — и отличались
 * копии одной фразой в подписи. Любая правка в одной из них молча оставляла
 * половину людей на старом экране.
 *
 * Ручной ввод кода остался, но ушёл вниз под кнопку: он нужен, когда камеры
 * нет вовсе (настольный браузер без веб-камеры) или в неё не дали доступ.
 */

/* Как часто дёргаем кадр. 320 мс — компромисс из прежней реализации: чаще
   греет телефон на разборе кадров, реже — код «не ловится» на глаз. */
const SCAN_INTERVAL_MS = 320;

/* Сколько держится экран «Доступ открыт» перед возвратом к сканеру. Хватает,
   чтобы прочитать имя, и не заставляет жать кнопку ради следующего человека. */
const SUCCESS_HOLD_MS = 2400;

const initialsOf = (name) => String(name || '')
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join('');

/* Разрешение на камеру УЖЕ дано? Permissions API есть не везде (в Safari его
   для камеры нет), поэтому «не знаем» = «нет»: тогда на компьютере камера не
   включится сама, а на телефоне включится по правилу ниже. */
const cameraAlreadyAllowed = async () => {
    try {
        const status = await navigator.permissions?.query?.({ name: 'camera' });
        return status?.state === 'granted';
    } catch (e) {
        return false;
    }
};

const QrAccessView = ({ user, apiBaseUrl, withAccessTokenHeader, scopeHint = '' }) => {
    const isNarrow = useIsMobileShell();

    const videoRef = useRef(null);
    const streamRef = useRef(null);
    const timerRef = useRef(null);
    /* Кадр ещё разбирается. BarcodeDetector.detect асинхронный, и без этого
       флага следующий тик интервала входил бы в разбор поверх незакончившегося. */
    const frameBusyRef = useRef(false);
    /* Код уже пойман — дальше молчим. Иначе одна и та же бумажка в кадре
       успевала бы отправить три запроса предпросмотра подряд. */
    const caughtRef = useRef(false);
    const autoStartedRef = useRef(false);
    /* Камеру ХОТЯТ держать включённой. Ловля кода тоже гасит камеру, поэтому
       по одному лишь scanning отличить «выключил сам» от «выключилось на время
       вопроса» нельзя, и возврат к сканеру включал бы камеру человеку, который
       минуту назад её выключил и набирает код руками. */
    const cameraWantedRef = useRef(false);
    const mountedRef = useRef(true);
    /* Строка, снятая с кода. Держим её здесь, а не в ответе предпросмотра:
       сервер токен обратно не возвращает — эхо секрета в лишнем ответе ничего
       не даёт, а поводов утечь добавляет. */
    const pendingTokenRef = useRef('');
    /* Запуск камеры уже идёт. Ref, а не состояние: состояние в зависимостях
       startScanner меняло бы его личность на каждом включении, а от неё
       зависит таймер экрана «Доступ открыт». */
    const startingRef = useRef(false);

    const [scanning, setScanning] = useState(false);
    const [starting, setStarting] = useState(false);
    const [cameraError, setCameraError] = useState('');
    /* null — фонарика у камеры нет (так на всех iPhone), иначе включён/выключен. */
    const [torchOn, setTorchOn] = useState(null);

    const [checking, setChecking] = useState(false);
    const [candidate, setCandidate] = useState(null);
    const [failure, setFailure] = useState('');
    const [granting, setGranting] = useState(false);
    const [granted, setGranted] = useState(null);

    const [manualOpen, setManualOpen] = useState(false);
    const [manualValue, setManualValue] = useState('');

    const authHeaders = useCallback(
        () => ({ withCredentials: true, headers: withAccessTokenHeader({ 'X-User-Id': user?.id }) }),
        [withAccessTokenHeader, user?.id],
    );

    const stopScanner = useCallback(() => {
        if (timerRef.current) {
            clearInterval(timerRef.current);
            timerRef.current = null;
        }
        if (streamRef.current) {
            streamRef.current.getTracks().forEach((track) => track.stop());
            streamRef.current = null;
        }
        if (videoRef.current) videoRef.current.srcObject = null;
        frameBusyRef.current = false;
        setScanning(false);
        setTorchOn(null);
    }, []);

    /* Поймали код. Дальше сканер выключен до решения человека: держать камеру
       включённой под карточкой подтверждения незачем — она греет телефон и
       ловит следующий код в спину уже принятому решению. */
    const catchCode = useCallback(async (raw) => {
        if (caughtRef.current) return;
        caughtRef.current = true;
        pendingTokenRef.current = String(raw || '');
        try { navigator.vibrate?.(18); } catch (e) { /* вибрации может не быть */ }
        stopScanner();
        setFailure('');
        setCandidate(null);
        setChecking(true);
        try {
            const { data } = await axios.post(
                `${apiBaseUrl}/api/sensitive-access/qr/preview`,
                { token: pendingTokenRef.current },
                authHeaders(),
            );
            if (!mountedRef.current) return;
            if (data?.status === 'success') setCandidate(data);
            else setFailure(data?.error || 'Не удалось прочитать код');
        } catch (err) {
            if (!mountedRef.current) return;
            setFailure(err.response?.data?.error || 'Не удалось прочитать код');
        } finally {
            if (mountedRef.current) setChecking(false);
        }
    }, [apiBaseUrl, authHeaders, stopScanner]);

    const startScanner = useCallback(async () => {
        if (streamRef.current || startingRef.current) return;
        setCameraError('');
        if (!navigator.mediaDevices?.getUserMedia) {
            setCameraError('Камера в этом браузере недоступна — введите код вручную');
            return;
        }

        startingRef.current = true;
        setStarting(true);
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: 'environment' } },
            });
            if (!mountedRef.current || !videoRef.current) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }
            streamRef.current = stream;
            videoRef.current.srcObject = stream;
            await videoRef.current.play();
            caughtRef.current = false;
            cameraWantedRef.current = true;
            setScanning(true);

            /* Фонарик есть у задней камеры Android; на iPhone возможности
                пустые, и кнопку тогда не показываем вовсе. */
            const track = stream.getVideoTracks()[0];
            const torchCapable = Boolean(track?.getCapabilities?.().torch);
            setTorchOn(torchCapable ? false : null);

            let detector = null;
            if (window.BarcodeDetector) {
                try {
                    detector = new window.BarcodeDetector({ formats: ['qr_code'] });
                } catch (e) {
                    detector = null;
                }
            }

            const canvas = detector ? null : document.createElement('canvas');
            const ctx = canvas ? canvas.getContext('2d', { willReadFrequently: true }) : null;
            if (!detector && !ctx) {
                setCameraError('Сканер в этом браузере недоступен — введите код вручную');
                stopScanner();
                return;
            }

            timerRef.current = setInterval(async () => {
                const video = videoRef.current;
                if (!video || caughtRef.current || frameBusyRef.current) return;
                if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;
                frameBusyRef.current = true;
                try {
                    if (detector) {
                        const codes = await detector.detect(video);
                        const value = codes?.[0]?.rawValue;
                        if (value) catchCode(String(value));
                        return;
                    }
                    canvas.width = video.videoWidth;
                    canvas.height = video.videoHeight;
                    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                    const frame = ctx.getImageData(0, 0, canvas.width, canvas.height);
                    const code = jsQR(frame.data, frame.width, frame.height, { inversionAttempts: 'dontInvert' });
                    if (code?.data) catchCode(String(code.data));
                } catch (e) {
                    /* Битый кадр — просто ждём следующий. */
                } finally {
                    frameBusyRef.current = false;
                }
            }, SCAN_INTERVAL_MS);
        } catch (err) {
            cameraWantedRef.current = false;
            const denied = err?.name === 'NotAllowedError' || err?.name === 'SecurityError';
            setCameraError(denied
                ? 'Доступ к камере запрещён. Разрешите его в настройках браузера или введите код вручную'
                : (err?.message || 'Не удалось включить камеру'));
            stopScanner();
        } finally {
            startingRef.current = false;
            if (mountedRef.current) setStarting(false);
        }
    }, [stopScanner, catchCode]);

    const toggleTorch = useCallback(async () => {
        const track = streamRef.current?.getVideoTracks?.()[0];
        if (!track) return;
        const next = !torchOn;
        try {
            await track.applyConstraints({ advanced: [{ torch: next }] });
            setTorchOn(next);
        } catch (e) {
            setTorchOn(null);
        }
    }, [torchOn]);

    const turnCameraOff = useCallback(() => {
        cameraWantedRef.current = false;
        stopScanner();
    }, [stopScanner]);

    /* Уйти с экрана вопроса. Камеру возвращаем в то состояние, в котором её
       застали: после «Отмены» и после выданного доступа человек ждёт того же
       экрана, с которого пришёл. */
    const resumeScanning = useCallback(() => {
        setCandidate(null);
        setFailure('');
        setGranted(null);
        setChecking(false);
        caughtRef.current = false;
        if (cameraWantedRef.current) startScanner();
    }, [startScanner]);

    /* А это — нажатие на кнопку со словом «сканировать»: её просят именно
       затем, чтобы камера включилась, даже если до этого код вводили руками. */
    const scanAgain = useCallback(() => {
        setCandidate(null);
        setFailure('');
        setGranted(null);
        setChecking(false);
        caughtRef.current = false;
        startScanner();
    }, [startScanner]);

    const approve = useCallback(async () => {
        if (!candidate || granting) return;
        setGranting(true);
        try {
            const { data } = await axios.post(
                `${apiBaseUrl}/api/sensitive-access/approve`,
                { token: pendingTokenRef.current },
                authHeaders(),
            );
            if (!mountedRef.current) return;
            if (data?.status === 'success') {
                setCandidate(null);
                /* Всплывающего уведомления здесь НЕТ намеренно: экран и так
                   говорит «Доступ открыт» с именем, а всплывашка садится ровно
                   на кнопку «Сканировать дальше» в подвале экрана. */
                setGranted({ name: data.operator_name || candidate.operator_name });
            } else {
                setCandidate(null);
                setFailure(data?.error || 'Не удалось открыть доступ');
            }
        } catch (err) {
            if (!mountedRef.current) return;
            setCandidate(null);
            setFailure(err.response?.data?.error || 'Не удалось открыть доступ');
        } finally {
            if (mountedRef.current) setGranting(false);
        }
    }, [apiBaseUrl, authHeaders, candidate, granting]);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            stopScanner();
        };
    }, [stopScanner]);

    /* Камера включается сама — ради этого раздел и переделан. На компьютере
       только если разрешение уже дано: там раздел открывают и просто чтобы
       посмотреть, а зажёгшийся индикатор веб-камеры без спроса — плохая
       неожиданность. На телефоне сканер и есть весь раздел, спрашивает сам
       браузер, и лишнее нажатие «Запустить» здесь ничего не охраняет. */
    useEffect(() => {
        if (autoStartedRef.current) return undefined;
        autoStartedRef.current = true;
        let cancelled = false;
        (async () => {
            const allowed = isNarrow || await cameraAlreadyAllowed();
            if (!cancelled && allowed) startScanner();
        })();
        return () => { cancelled = true; };
    }, [isNarrow, startScanner]);

    /* Экран «Доступ открыт» гаснет сам: следующего сотрудника подтверждают
       сразу за первым, и лишнее нажатие в очереди из десяти человек — десять
       нажатий. */
    useEffect(() => {
        if (!granted) return undefined;
        const timer = setTimeout(() => { if (mountedRef.current) resumeScanning(); }, SUCCESS_HOLD_MS);
        return () => clearTimeout(timer);
    }, [granted, resumeScanning]);

    const submitManual = useCallback((event) => {
        event?.preventDefault?.();
        const value = manualValue.trim();
        if (!value) return;
        caughtRef.current = false;
        catchCode(value);
        setManualValue('');
    }, [manualValue, catchCode]);

    const sheetOpen = Boolean(checking || candidate || failure || granted);
    const closeSheet = granting ? () => {} : resumeScanning;

    return (
        <div
            className={`mx-auto w-full max-w-2xl ${isNarrow ? 'px-4 pb-4' : ''}`}
            style={{ fontFamily: APPLE_FONT }}
        >
            <div className="mb-4 flex items-start gap-3">
                <div className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-blue-600/10 text-blue-600">
                    <QrCode size={20} strokeWidth={1.9} />
                </div>
                <div className="min-w-0">
                    <h2 className="text-[19px] font-semibold leading-tight text-slate-900">QR доступ</h2>
                    {/* На телефоне подпись — одна строка. Полный список того,
                        что откроется, стоит на экране подтверждения, прямо над
                        кнопкой: там он и нужен, а здесь три абзаца отодвигали
                        видоискатель на треть экрана вниз. */}
                    <p className="mt-1 text-[13px] leading-relaxed text-slate-500">
                        Наведите камеру на код сотрудника.
                        {!isNarrow && ' Подтверждение открывает ему «Обращения», «Вики» и «Посылки»,'
                            + ' а в оценках — полный номер и записи разговоров. Только в текущей'
                            + ' сессии этого человека.'}
                        {scopeHint ? ` ${scopeHint}` : ''}
                    </p>
                </div>
            </div>

            {/* Видоискатель */}
            <div
                className="relative overflow-hidden rounded-[28px] bg-slate-900 ring-1 ring-slate-900/10"
                /* Пока набирают код руками, видоискатель уступает место: на
                   телефоне полноразмерный кадр уводил подвал формы под бар
                   разделов, и «Проверить код» приходилось выискивать
                   прокруткой — а нажатие по нему попадало в чужой пункт меню. */
                style={{ height: isNarrow ? (manualOpen ? 'min(32vh, 280px)' : 'min(62vh, 560px)') : '380px' }}
            >
                <video
                    ref={videoRef}
                    className="absolute inset-0 h-full w-full object-cover"
                    autoPlay
                    muted
                    playsInline
                />

                {scanning && (
                    <div className="pointer-events-none absolute inset-0 grid place-items-center">
                        <div
                            /* Размер считается от ВЫСОТЫ кадра, а не от ширины:
                               кадр бывает и низким (пока набирают код руками), и
                               квадрат в 240 px тогда вылезал за него, накрывая
                               подпись. По высоте окно вписывается в любой. */
                            className="qr-window relative rounded-[26px]"
                            style={{ height: 'min(64%, 240px)', aspectRatio: '1 / 1', '--qr-sweep-distance': '100%' }}
                        >
                            <span className="absolute -left-px -top-px h-8 w-8 rounded-tl-[26px] border-l-[3px] border-t-[3px] border-white/90" />
                            <span className="absolute -right-px -top-px h-8 w-8 rounded-tr-[26px] border-r-[3px] border-t-[3px] border-white/90" />
                            <span className="absolute -bottom-px -left-px h-8 w-8 rounded-bl-[26px] border-b-[3px] border-l-[3px] border-white/90" />
                            <span className="absolute -bottom-px -right-px h-8 w-8 rounded-br-[26px] border-b-[3px] border-r-[3px] border-white/90" />
                            <span className="qr-sweep absolute inset-x-3 top-0 h-0.5 rounded-full bg-blue-400/90 shadow-[0_0_12px_rgba(96,165,250,0.9)]" />
                        </div>
                    </div>
                )}

                {scanning && !manualOpen && (
                    <p className="pointer-events-none absolute inset-x-0 bottom-6 text-center text-[13px] font-medium text-white/80 drop-shadow">
                        Наведите на QR-код сотрудника
                    </p>
                )}

                {/* Управление живёт В КАДРЕ, а не строкой под ним: на 390 px
                    подписанные кнопки «Выключить камеру» и «Ввести код вручную»
                    в одну строку не влезали и разъезжались на две, отнимая у
                    видоискателя ещё полсотни пикселей. */}
                {scanning && (
                    <div className="absolute inset-x-4 top-4 flex items-start justify-between">
                        <button
                            type="button"
                            onClick={turnCameraOff}
                            title="Выключить камеру"
                            aria-label="Выключить камеру"
                            className="grid h-11 w-11 place-items-center rounded-full bg-slate-900/50 text-white backdrop-blur transition active:scale-95"
                        >
                            <CameraOff size={19} />
                        </button>
                        {torchOn !== null && (
                            <button
                                type="button"
                                onClick={toggleTorch}
                                title={torchOn ? 'Выключить фонарик' : 'Включить фонарик'}
                                aria-label={torchOn ? 'Выключить фонарик' : 'Включить фонарик'}
                                className={`grid h-11 w-11 place-items-center rounded-full backdrop-blur transition active:scale-95 ${
                                    torchOn ? 'bg-white text-slate-900' : 'bg-slate-900/50 text-white'
                                }`}
                            >
                                {torchOn ? <Flashlight size={19} /> : <FlashlightOff size={19} />}
                            </button>
                        )}
                    </div>
                )}

                {!scanning && (
                    <div className="absolute inset-0 grid place-items-center bg-slate-900/70 px-6 text-center backdrop-blur-sm">
                        {starting ? (
                            <div className="flex flex-col items-center gap-3 text-white/80">
                                <Loader2 size={26} className="animate-spin" />
                                <span className="text-[13px]">Включаю камеру…</span>
                            </div>
                        ) : (
                            <div className="flex flex-col items-center gap-4">
                                <CameraOff size={28} className="text-white/50" strokeWidth={1.6} />
                                {cameraError ? (
                                    <p className="max-w-xs text-[13px] leading-relaxed text-white/75">{cameraError}</p>
                                ) : (
                                    <p className="text-[13px] text-white/70">Камера выключена</p>
                                )}
                                <button type="button" onClick={startScanner} className={iosBtnPrimary}>
                                    <Camera size={16} />
                                    Включить камеру
                                </button>
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Ручной ввод — запасной путь: он нужен там, где камеры нет вовсе. */}
            <div className="mt-4">
                {!manualOpen ? (
                    <div className="flex justify-center">
                        <button
                            type="button"
                            onClick={() => setManualOpen(true)}
                            className="inline-flex items-center gap-2 rounded-xl px-3 py-2 text-[13px] font-medium text-blue-600 transition hover:bg-blue-50 active:scale-[0.98]"
                        >
                            <Keyboard size={16} />
                            Ввести код вручную
                        </button>
                    </div>
                ) : (
                    <form onSubmit={submitManual} className={`${iosCard} p-3.5`}>
                        <label className="mb-2 block px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                            Строка из QR
                        </label>
                        <textarea
                            rows={3}
                            value={manualValue}
                            onChange={(e) => setManualValue(e.target.value)}
                            placeholder="OTP-SENSITIVE:… или ссылка с кодом"
                            className={`${iosInput} resize-none font-mono`}
                        />
                        <div className="mt-3 flex flex-wrap gap-2">
                            <button type="submit" disabled={!manualValue.trim()} className={`${iosBtnPrimary} flex-1`}>
                                <ShieldCheck size={16} />
                                Проверить код
                            </button>
                            <button
                                type="button"
                                onClick={() => { setManualOpen(false); setManualValue(''); }}
                                className={iosBtnSecondary}
                            >
                                Скрыть
                            </button>
                        </div>
                    </form>
                )}
            </div>

            {/* Подтверждение. На телефоне IosModal — это экран, въезжающий
                справа, поэтому отдельной мобильной разметки здесь нет. */}
            <IosModal
                open={sheetOpen}
                onClose={closeSheet}
                title={granted ? 'Доступ открыт' : (failure ? 'Код не принят' : 'Открыть доступ?')}
                subtitle={granted || failure ? '' : 'Подтверждение по QR'}
                maxWidth="max-w-sm"
                footer={granted ? (
                    <button type="button" onClick={scanAgain} className={`${iosBtnPrimary} w-full`}>
                        Сканировать дальше
                    </button>
                ) : failure ? (
                    <button type="button" onClick={scanAgain} className={`${iosBtnPrimary} w-full`}>
                        <Camera size={16} />
                        Сканировать снова
                    </button>
                ) : candidate ? (
                    <div className="flex w-full gap-2">
                        <button
                            type="button"
                            onClick={resumeScanning}
                            disabled={granting}
                            className={`${iosBtnSecondary} flex-1`}
                        >
                            Отмена
                        </button>
                        <button
                            type="button"
                            onClick={approve}
                            disabled={granting}
                            className={`${iosBtnPrimary} flex-1`}
                        >
                            {granting ? <Loader2 size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
                            {granting ? 'Открываю…' : 'Открыть доступ'}
                        </button>
                    </div>
                ) : null}
            >
                <div className="flex min-h-full flex-col">
                {checking && (
                    <div className="my-auto flex flex-col items-center gap-3 py-10 text-slate-500">
                        <Loader2 size={26} className="animate-spin text-blue-600" />
                        <span className="text-[13px]">Проверяю код…</span>
                    </div>
                )}

                {failure && !checking && (
                    <div className="my-auto flex flex-col items-center gap-3 py-8 text-center">
                        <div className="grid h-14 w-14 place-items-center rounded-full bg-amber-100 text-amber-600">
                            <AlertCircle size={26} />
                        </div>
                        <p className="max-w-xs text-[14px] leading-relaxed text-slate-700">{failure}</p>
                    </div>
                )}

                {granted && !checking && (
                    <div className="my-auto flex flex-col items-center gap-3 py-8 text-center">
                        <div className="grid h-16 w-16 place-items-center rounded-full bg-green-100 text-green-600">
                            <CheckCircle2 size={30} />
                        </div>
                        <p className="text-[16px] font-semibold text-slate-900">{granted.name}</p>
                        <p className="max-w-xs text-[13px] leading-relaxed text-slate-500">
                            Закрытые разделы у него откроются сами — обновлять страницу не нужно.
                        </p>
                    </div>
                )}

                {candidate && !checking && (
                    <div className="my-auto space-y-4">
                        <div className="flex flex-col items-center gap-3 pt-2 text-center">
                            <div className="relative">
                                <div className="grid h-20 w-20 place-items-center overflow-hidden rounded-full bg-slate-200 text-[22px] font-semibold text-slate-500 ring-4 ring-white">
                                    {candidate.avatar_url ? (
                                        <img
                                            src={candidate.avatar_url}
                                            alt=""
                                            className="h-full w-full object-cover"
                                            referrerPolicy="no-referrer"
                                        />
                                    ) : (initialsOf(candidate.operator_name) || <UserRound size={30} />)}
                                </div>
                            </div>
                            <div>
                                <p className="text-[18px] font-semibold leading-tight text-slate-900">
                                    {candidate.operator_name}
                                </p>
                                <p className="mt-1 text-[12.5px] text-slate-500">
                                    {[candidate.operator_department, candidate.operator_direction]
                                        .filter(Boolean).join(' · ') || candidate.operator_login}
                                </p>
                            </div>
                        </div>

                        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                            <div className="flex items-start gap-3 px-3.5 py-3">
                                <ShieldCheck size={17} className="mt-0.5 shrink-0 text-blue-600" />
                                <p className="text-[13px] leading-relaxed text-slate-600">
                                    Откроются «Обращения», «Вики» и «Посылки», а в оценках — полный номер
                                    и записи разговоров.
                                </p>
                            </div>
                            <div className="flex items-start gap-3 px-3.5 py-3">
                                <QrCode size={17} className="mt-0.5 shrink-0 text-slate-400" />
                                <p className="text-[13px] leading-relaxed text-slate-600">
                                    Доступ живёт до конца текущей сессии сотрудника: на другом устройстве
                                    и после повторного входа его снова придётся подтвердить.
                                </p>
                            </div>
                        </div>

                        {candidate.already_granted && (
                            <p className="rounded-xl bg-amber-50 px-3.5 py-2.5 text-[12.5px] leading-relaxed text-amber-700">
                                Этой сессии доступ уже открыт — подтверждение ничего не изменит.
                            </p>
                        )}
                    </div>
                )}
                </div>
            </IosModal>
        </div>
    );
};

export default QrAccessView;
