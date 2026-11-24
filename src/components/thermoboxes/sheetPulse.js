/*
 * «Термокороба»: открытый экран узнаёт о чтении Google-таблицы сразу (thermoboxes/live.py, владелец 24.11.2026:
 * «читать раз в минуту и обновлять»).
 *
 * Сервер читает лист раз в минуту и после каждого чтения шлёт по SSE пустое событие source; экран по нему спрашивает
 * /api/thermoboxes/source и перечитывает строки, только когда цифры правда сдвинулись. Пока тычки идут, опрос раз в
 * 30 с не нужен — его и выключает onLive(true); нет потока (мест нет — 503, сеть, нет прав) или он замолчал — экран
 * живёт на опросе.
 *
 * Без импортов: держатель соединения (runSseStream из common/sseClient.js), document и таймеры передаются снаружи —
 * так разбор проверяет node-тест (tests/thermobox_live.test.mjs).
 */

// Скрытая вкладка отпускает поток не сразу: вернулась в пределах минуты — тот же поток. Иначе каждое переключение
// вкладки — новое соединение, а старое сервер держит ещё около минуты (waitress замечает уход клиента только на
// третьей записи после него), и частые переключения заняли бы все места под потоки.
export const HIDDEN_GRACE_MS = 60000;
// Тычок приходит после каждого чтения листа, то есть раз в минуту. Тишина дольше 2,5 минут при «живом» потоке —
// канал не работает (слушатель на сервере лёг, лист не читается): экран возвращается на опрос и сверяется сразу.
export const SILENCE_MS = 150000;
// Вкладка вернулась, а поток за это время не ожил (мест нет, сеть) — сверить без него.
export const FALLBACK_CHECK_MS = 3000;

export const watchSheetSource = ({
    url, getHeaders, runStream, onPulse, onLive = () => {},
    doc = document, setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id),
}) => {
    let stream = null;
    let connected = false;   // поток подключён (sseClient сказал live)
    let fresh = false;       // подключён и тычки идут — опрос не нужен
    let state = 'closed';
    let stopped = false;
    let hideTimer = null;
    let silenceTimer = null;
    let fallbackTimer = null;

    const visible = () => doc.visibilityState !== 'hidden';
    const cancel = (timer) => {
        if (timer) clearTimer(timer);
        return null;
    };
    const setFresh = (value) => {
        if (fresh === value) return;
        fresh = value;
        onLive(value);
    };
    const armSilence = () => {
        silenceTimer = cancel(silenceTimer);
        silenceTimer = setTimer(() => {
            silenceTimer = null;
            setFresh(false);
            onPulse();
        }, SILENCE_MS);
    };
    // Тычок или подключение: канал работает — опрос не нужен, сверить сейчас.
    const signal = () => {
        setFresh(true);
        armSilence();
        onPulse();
    };
    const open = () => {
        if (stopped || stream) return;
        stream = runStream({
            url,
            getHeaders,
            onEvent: (event) => { if (event === 'source') signal(); },
            onState: (next) => {
                state = next;
                if (next === 'live') {
                    if (connected) return;
                    connected = true;
                    fallbackTimer = cancel(fallbackTimer);
                    // Подключился или переподключился — сверить всегда: тычок, ушедший до подписки, иначе ждал бы
                    // следующего чтения листа.
                    signal();
                    return;
                }
                connected = false;
                silenceTimer = cancel(silenceTimer);
                setFresh(false);
            },
        });
    };
    const close = () => {
        hideTimer = cancel(hideTimer);
        fallbackTimer = cancel(fallbackTimer);
        silenceTimer = cancel(silenceTimer);
        const current = stream;
        stream = null;
        connected = false;
        current?.close();
        setFresh(false);
    };
    const onVisibility = () => {
        if (stopped) return;
        if (!visible()) {
            if (stream && !hideTimer) {
                hideTimer = setTimer(() => {
                    hideTimer = null;
                    close();
                }, HIDDEN_GRACE_MS);
            }
            return;
        }
        hideTimer = cancel(hideTimer);
        // Поток пережил фон — тычки, пришедшие, пока вкладка была скрыта, экран пропускал: сверить сейчас.
        if (stream && connected) {
            onPulse();
            return;
        }
        // Поток сейчас и не оживёт (мест нет, нет прав) — сверить сразу; иначе — после подключения или, если оно
        // затянется, через FALLBACK_CHECK_MS.
        if (stream && (state === 'off' || state === 'denied')) {
            onPulse();
            return;
        }
        open();
        fallbackTimer = cancel(fallbackTimer);
        fallbackTimer = setTimer(() => {
            fallbackTimer = null;
            if (!connected) onPulse();
        }, FALLBACK_CHECK_MS);
    };
    doc.addEventListener('visibilitychange', onVisibility);
    if (visible()) open();
    return {
        stop() {
            stopped = true;
            doc.removeEventListener('visibilitychange', onVisibility);
            close();
        },
    };
};

/*
 * Сверка с сервером по одной: тычок, пришедший посреди сверки, не теряется — та могла уйти до записи на сервере,
 * поэтому сразу после неё сверка идёт ещё раз (сколько бы тычков ни пришло — один повтор). canRun — экран открыт и
 * видим. Ошибки run глотает сам: сверка — фон, экран остаётся как был.
 */
export const createSourceCheck = ({ run, canRun = () => true }) => {
    let running = false;
    let again = false;
    return async () => {
        if (!canRun()) return;
        if (running) {
            again = true;
            return;
        }
        running = true;
        try {
            do {
                again = false;
                try {
                    await run();
                } catch {
                    // Сеть или сервер: спросим в следующий раз.
                }
            } while (again && canRun());
        } finally {
            running = false;
            again = false;
        }
    };
};
