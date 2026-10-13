/* QR-подтверждение сессии — для встроенных страниц iCORE Phone.
 *
 * Механика та же, что у окна QR в портале (App.jsx: requestSensitiveQrAccess и
 * опрос статуса): POST /api/sensitive-access/qr/request выдаёт код на ТЕКУЩУЮ
 * сессию, супервайзер своего отдела подтверждает его у себя в разделе «QR доступ»
 * (components/qr/QrAccessView.jsx — камерой или «Ввести код вручную»), а страница
 * раз в 2,5 с спрашивает GET /api/sensitive-access/status. Своего ключа здесь нет:
 * сессия телефона — обычная сессия портала, и подтверждение на ней открывает и
 * справочник офисов, и запись попыток тренажёров, пока оператор не выйдет из
 * телефона (токен обновляется, а сессия остаётся та же).
 *
 * Чем отличается от окна портала — всё из-за того, где живёт страница:
 *   - код обновляется сам, до истечения: вкладка телефона висит открытой часами,
 *     и «Обновить QR» руками тут нажимать некому;
 *   - опрос и обновление спят, пока вкладка спрятана (оператор ушёл в обзвон), и
 *     просыпаются, как только её снова открыли;
 *   - резервный код крупно и с кнопкой «Скопировать»: удалённому оператору показать
 *     экран супервайзеру нельзя, зато код можно переслать в мессенджер.
 *
 * QR рисуется здесь же, библиотекой qrcode (как в портале): действующий код доступа
 * не должен уезжать постороннему сервису картинок.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Check, Copy, Loader2, QrCode, RefreshCw, WifiOff } from 'lucide-react';

import { iosCard, iosBtnSecondary, iosGroupLabel } from '../components/ui/ios';
import { recoverFromStaleBundle } from '../staleBundleRecovery';
import copyText from './copyText';
import {
    QR_POLL_MS, formatCountdown, qrSchedule, splitQrCode, statusPollDelayMs,
} from './embedAccess';

// Выдача кода не удалась — когда пробовать снова.
const QR_RETRY_MS = 10000;
// Сколько отказов опроса подряд считать «нет связи», а не случайной заминкой.
const OFFLINE_AFTER_FAILURES = 3;
// Модуль QR в пикселях исходной картинки. Целое число — чтобы при показе с
// image-rendering: pixelated края модулей оставались резкими, а не размытыми.
const QR_SCALE = 12;

const isHidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';

/* Текст для человека. Ответы ручек QR — английские и технические («Active session
   not found»), показывать их оператору незачем. */
const qrProblemText = (err) => {
    const status = err?.response?.status;
    if (!err?.response) return 'Нет связи с порталом — попробуем снова через несколько секунд.';
    if (status === 401) return 'Сессия закончилась — войдите заново.';
    return 'Портал не выдал код — попробуем снова через несколько секунд.';
};

export default function QrUnlockPanel({
    apiBase, headers = null, onGranted, title, inPhone = false, pollMs = QR_POLL_MS,
}) {
    /* Заголовки — в ref: токен обновляется раз в полчаса, и новый объект заголовков
       не должен перезапускать ни опрос, ни выдачу кода. В офисах заголовков нет
       вовсе: токен подставляет перехватчик axios страницы. */
    const headersRef = useRef(headers);
    headersRef.current = headers;
    const grantedRef = useRef(onGranted);
    grantedRef.current = onGranted;
    const aliveRef = useRef(true);
    const doneRef = useRef(false);
    const inflightRef = useRef(false);
    const copyTimerRef = useRef(null);

    const [qr, setQr] = useState(null);          // { payload, validUntilMs, refreshAtMs }
    const [nextQrAt, setNextQrAt] = useState(0); // когда просить код; 0 — сразу
    const [image, setImage] = useState('');
    const [drawFailed, setDrawFailed] = useState(false);
    const [problem, setProblem] = useState('');
    const [offline, setOffline] = useState(false);
    const [copied, setCopied] = useState('');    // '' | 'ok' | 'fail'
    const [now, setNow] = useState(() => Date.now());

    useEffect(() => {
        aliveRef.current = true;
        return () => {
            aliveRef.current = false;
            clearTimeout(copyTimerRef.current);
        };
    }, []);

    const requestConfig = () => (headersRef.current ? { headers: headersRef.current } : undefined);

    /* Подтвердили — один раз. Дальше решает страница: офисы перепроверяют доступ,
       тренажёры — запись. */
    const finish = useCallback(() => {
        if (doneRef.current) return;
        doneRef.current = true;
        grantedRef.current?.();
    }, []);

    const requestQr = useCallback(async () => {
        if (inflightRef.current || doneRef.current) return;
        inflightRef.current = true;
        try {
            const { data } = await axios.post(`${apiBase}/api/sensitive-access/qr/request`, {}, requestConfig());
            if (!aliveRef.current || doneRef.current) return;
            if (data?.granted) { finish(); return; }
            // Приставку ставит сервер (SENSITIVE_QR_PREFIX); своей копии здесь нет,
            // как и в портале. Без qr_payload — голый токен: разбор понимает и его.
            const payload = String(data?.qr_payload || data?.token || '');
            if (data?.status !== 'success' || !payload) throw new Error('QR не выдан');
            const schedule = qrSchedule(data.token_expires_at, Date.now());
            setQr({ payload, ...schedule });
            setNextQrAt(schedule.refreshAtMs);
            setNow(Date.now());
            setProblem('');
        } catch (err) {
            if (!aliveRef.current || doneRef.current) return;
            // 403 у этой ручки один: «этой роли подтверждение не нужно». Значит, и
            // замка быть не должно — пусть страница проверит доступ заново.
            if (err?.response?.status === 403) { finish(); return; }
            setProblem(qrProblemText(err));
            setNextQrAt(Date.now() + QR_RETRY_MS);
        } finally {
            inflightRef.current = false;
        }
        // requestConfig читает ref — в зависимостях ему делать нечего.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBase, finish]);

    /* Выдача кода: сразу при открытии, затем — до истечения прежнего. Спрятанной
       вкладке код не нужен: просим, когда её снова откроют. */
    useEffect(() => {
        let timer = null;
        const fire = () => {
            timer = null;
            if (!isHidden()) requestQr();
        };
        timer = setTimeout(fire, Math.max(0, nextQrAt - Date.now()));
        const onVisible = () => {
            if (!isHidden() && !timer && Date.now() >= nextQrAt) requestQr();
        };
        document.addEventListener('visibilitychange', onVisible);
        return () => {
            if (timer) clearTimeout(timer);
            document.removeEventListener('visibilitychange', onVisible);
        };
    }, [nextQrAt, requestQr]);

    /* «Подтвердили?» — раз в 2,5 с, пока вкладка на экране. Следующий вопрос — после
       ответа на предыдущий, а не по часам: медленный ответ не наслаивает запросы. */
    useEffect(() => {
        let timer = null;
        let busy = false;
        let failures = 0;
        let stopped = false;
        const tick = async () => {
            timer = null;
            if (stopped || doneRef.current || isHidden()) return;
            busy = true;
            try {
                const { data } = await axios.get(`${apiBase}/api/sensitive-access/status`, requestConfig());
                if (stopped) return;
                failures = 0;
                setOffline(false);
                // required:false — роли подтверждение не нужно вовсе; это тоже «можно».
                if (data?.granted || data?.required === false) { finish(); return; }
            } catch {
                if (stopped) return;
                failures += 1;
                if (failures >= OFFLINE_AFTER_FAILURES) setOffline(true);
            } finally {
                busy = false;
            }
            if (!stopped && !doneRef.current) timer = setTimeout(tick, statusPollDelayMs(failures, pollMs));
        };
        const onVisible = () => {
            if (!isHidden() && !timer && !busy && !stopped) tick();
        };
        timer = setTimeout(tick, pollMs);
        document.addEventListener('visibilitychange', onVisible);
        return () => {
            stopped = true;
            if (timer) clearTimeout(timer);
            document.removeEventListener('visibilitychange', onVisible);
        };
        // requestConfig читает ref — в зависимостях ему делать нечего.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBase, pollMs, finish]);

    /* Картинка кода. Прежняя остаётся на экране, пока рисуется новая: мигать пустым
       квадратом каждые пять минут незачем. */
    useEffect(() => {
        if (!qr?.payload) return undefined;
        let cancelled = false;
        import('qrcode')
            .then((mod) => (mod.default || mod).toDataURL(qr.payload, {
                errorCorrectionLevel: 'M',
                margin: 2,
                scale: QR_SCALE,
            }))
            .then((url) => {
                if (cancelled) return;
                setImage(url);
                setDrawFailed(false);
            })
            .catch((err) => {
                // Устаревшая сборка (кусок библиотеки удалён выкладкой) лечится
                // перезагрузкой; остальное — показать код строкой.
                if (cancelled || recoverFromStaleBundle(err)) return;
                setImage('');
                setDrawFailed(true);
            });
        return () => { cancelled = true; };
    }, [qr?.payload]);

    // Отсчёт «действует ещё» — раз в секунду и только пока вкладку видно.
    useEffect(() => {
        if (!qr) return undefined;
        const timer = setInterval(() => { if (!isHidden()) setNow(Date.now()); }, 1000);
        return () => clearInterval(timer);
    }, [qr]);

    const copyCode = async () => {
        if (!qr?.payload) return;
        const ok = await copyText(qr.payload);
        if (!aliveRef.current) return;
        setCopied(ok ? 'ok' : 'fail');
        clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => { if (aliveRef.current) setCopied(''); }, 2500);
    };

    const retryNow = () => {
        setProblem('');
        requestQr();
    };

    const code = splitQrCode(qr?.payload);
    const left = qr ? qr.validUntilMs - now : 0;

    return (
        <section className={`${iosCard} p-4`}>
            <header className="flex items-start gap-3">
                {/* Значок — только в широкой раскладке (от 460 px). В узкой он отнимает
                    у заголовка строку: «Офисы откроются после подтверждения» шло в
                    две строки при 393 px и в три при 243, а каждая строка шапки
                    здесь сдвигает кнопку «Скопировать код» к нижнему краю вкладки. */}
                <div className="hidden h-10 w-10 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600 ring-1 ring-blue-100 min-[460px]:grid">
                    <QrCode size={20} strokeWidth={1.9} aria-hidden="true" />
                </div>
                <div className="min-w-0">
                    <h2 className="text-[15px] font-semibold leading-snug text-slate-900">{title}</h2>
                    <p className="mt-0.5 text-[12.5px] leading-relaxed text-slate-500">
                        Покажите QR супервайзеру своего отдела или отправьте ему код — он подтвердит
                        в разделе «QR доступ».
                    </p>
                </div>
            </header>

            {/* Раскладка под вкладку телефона — около 393 × 285 px (у режима обзвона
                окно не уже).

                Уже 460 px — столбиком, и КОД С КНОПКОЙ СТОЯТ ПЕРЕД QR, а кнопка —
                раньше самого кода. Удалённому оператору QR бесполезен: показать
                экран супервайзеру некому, его путь — скопировать код и отправить.
                Пока первым шёл QR в 198 px, а ряд включался только с 400 px, код и
                кнопка лежали под нижним краем вкладки (кнопка — на 445–485 px при
                высоте 285): человек видел картинку, которую некому сканировать, и
                ждал. Теперь кнопка на первом экране при любой ширине вплоть до
                243 px; код под ней, QR — ниже, он нужен тому, кто сидит рядом.

                От 460 px QR слева, код справа — оба на первом экране. QR 150 px, а
                не 198: колонке кода при 460 px нужна ширина, иначе код вставал в
                пять строк и выталкивал кнопку вниз; код версии 3 (33 модуля с
                полями) читается камерой и с такой картинки. */}
            <div className="mt-3 flex flex-col items-center gap-3 min-[460px]:flex-row min-[460px]:items-start">
                <div className="w-full max-w-[150px] shrink-0">
                    {image ? (
                        <img
                            src={image}
                            alt="QR-код подтверждения"
                            width={150}
                            height={150}
                            className="block aspect-square h-auto w-full rounded-xl bg-white ring-1 ring-slate-200"
                            style={{ imageRendering: 'pixelated' }}
                        />
                    ) : (
                        <div className="grid aspect-square w-full place-items-center rounded-xl bg-slate-50 px-4 text-center text-[12.5px] leading-relaxed text-slate-400 ring-1 ring-slate-200">
                            {drawFailed
                                ? 'QR не нарисовался — отправьте код'
                                : <Loader2 size={20} className="animate-spin" aria-label="Готовим код" />}
                        </div>
                    )}
                </div>

                {/* Порядок внутри колонки кода — тоже от ширины, классами order: в
                    разметке код идёт перед кнопкой (так его читает и экранный
                    диктор), а столбиком кнопка поднимается над ним. При 243 px код
                    занимает пять строк (130 px), и кнопка под ним не попадала бы
                    на первый экран; над ним она стоит на 186–226 px из 285. */}
                {qr && (
                    <div className="order-first flex w-full min-w-0 flex-1 flex-col min-[460px]:order-none">
                        <div className="flex items-baseline justify-between gap-2">
                            <span className={iosGroupLabel}>Код</span>
                            <span className="text-[11.5px] tabular-nums text-slate-400">
                                {left > 0 ? `ещё ${formatCountdown(left)}` : 'обновляем…'}
                            </span>
                        </div>
                        {/* Группы — отдельные строчные элементы без пробелов между
                            ними: выделенный и скопированный руками код остаётся
                            одной строкой, а <wbr> разрешает перенос только между
                            группами (break-all рвал бы и саму четвёрку). */}
                        <div className="order-2 mt-2 select-all rounded-xl bg-slate-100 px-3 py-2 font-mono text-[14px] font-semibold leading-relaxed tracking-wide text-slate-800 min-[460px]:order-none min-[460px]:mt-1">
                            {code.prefix && <span className="text-slate-400">{code.prefix}</span>}
                            {code.groups.map((group, index) => (
                                // eslint-disable-next-line react/no-array-index-key
                                <React.Fragment key={index}>
                                    <wbr />
                                    <span className={index || code.prefix ? 'ml-1.5' : ''}>{group}</span>
                                </React.Fragment>
                            ))}
                        </div>
                        <button
                            type="button"
                            onClick={copyCode}
                            className={`${iosBtnSecondary} order-1 mt-1 w-full min-[460px]:order-none min-[460px]:mt-2`}
                        >
                            {/* «код» — только столбиком: рядом с QR колонка узкая, и
                                подпись переносилась на вторую строку. Подпись — одним
                                элементом: у кнопки зазор между детьми (gap), и слово
                                «код» отдельным ребёнком отъезжало от «Скопировать». */}
                            {copied === 'ok'
                                ? <><Check size={15} aria-hidden="true" /> Скопировано</>
                                : <><Copy size={15} aria-hidden="true" /> <span>Скопировать<span className="min-[460px]:hidden"> код</span></span></>}
                        </button>
                        {copied === 'fail' && (
                            <p className="order-3 mt-1.5 px-1 text-[11.5px] text-rose-600">
                                Не скопировалось — выделите код и нажмите Ctrl+C.
                            </p>
                        )}
                        <p className="order-3 mt-1.5 px-1 text-[11.5px] leading-relaxed text-slate-400">
                            Супервайзер вводит его в «QR доступ» → «Ввести код вручную».
                        </p>
                    </div>
                )}
            </div>

            {problem ? (
                <div className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2 text-[12.5px] leading-relaxed text-amber-800">
                    <span className="min-w-0 flex-1">{problem}</span>
                    <button
                        type="button"
                        onClick={retryNow}
                        className="inline-flex shrink-0 items-center gap-1 font-semibold text-amber-900 hover:underline"
                    >
                        <RefreshCw size={13} aria-hidden="true" /> Сейчас
                    </button>
                </div>
            ) : (
                <div
                    className={`mt-3 flex items-center gap-2 rounded-xl px-3 py-2 text-[12.5px] ${
                        offline ? 'bg-amber-50 text-amber-800' : 'bg-slate-50 text-slate-500'
                    }`}
                    aria-live="polite"
                >
                    {offline
                        ? <><WifiOff size={13} aria-hidden="true" /> Нет связи с порталом — проверяем снова…</>
                        : <><Loader2 size={13} className="animate-spin" aria-hidden="true" /> Ждём подтверждения…</>}
                </div>
            )}

            <p className="mt-3 text-center text-[11.5px] leading-relaxed text-slate-400">
                {inPhone
                    ? 'Подтверждение действует, пока вы не выйдете из iCORE Phone.'
                    : 'Подтверждение действует до выхода из портала в этом браузере.'}
            </p>
        </section>
    );
}
