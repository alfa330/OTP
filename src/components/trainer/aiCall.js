/* Звонок ИИ-водителя в учебном рабочем месте (CRM + iCORE Phone).
 *
 * Владелец, 25.11.2026: «с айкорфона будет приходить звонок, и там ИИ-водитель
 * будет говорить свои данные — так обрабатывается звонок». Две половины уже
 * были: голосовой разговор раздела «Тренажёр» (сессия на сервере, VoiceLink:
 * микрофон → распознавание, ответ модели → озвучка) и рабочее место оператора
 * (TrainerPlayer в режиме звонка: машина звонка, CRM, место под голос —
 * проп voice и колбэк onCall). Этот модуль — переходник между ними.
 *
 * Жизненным циклом правят действия ЗВОНКА, а не voice.start/stop плеера:
 * плеер зовёт их на каждой смене «разговор ↔ удержание», и сессия ИИ
 * рвалась бы на каждом удержании. Поэтому:
 *   answer       → сессия на сервере, микрофон, приветствие водителя;
 *   hold/unhold  → на удержании сказанное в комнату водителю не уходит;
 *   end/transfer → микрофон и озвучка гаснут сразу (распознавание платное),
 *                  сессия закрывается и сервер собирает разбор;
 *   finish       → разбор отдаётся наружу (onReview), когда стажёр закончил
 *                  постобработку в CRM.
 *
 * Модуль без React: его гоняют тесты обычным node с поддельными api и
 * VoiceLink.
 */

/** Склейка замеров двух реплик, сказанных подряд, — как в разделе «Тренажёр». */
const mergeMetrics = (a = {}, b = {}) => {
    const out = { ...a };
    for (const [key, value] of Object.entries(b || {})) {
        out[key] = typeof value === 'number' && typeof a[key] === 'number' ? Math.max(a[key], value) : value;
    }
    return out;
};

/**
 * @param {object} cfg
 * @param {function} cfg.api         (path, init) => Promise<json> — /api/trainer…
 * @param {function} cfg.makeLink    (onEvent) => VoiceLink-подобный объект
 * @param {string}   cfg.scenarioKey ключ сценария водителя (payout_angry…)
 * @param {object}   [cfg.client]    что сказать серверу о клиенте (ua, экран)
 * @param {function} [cfg.onProblem] (text) — отказ звена: должен быть виден
 * @param {function} [cfg.onReview]  ({review, cost, review_error}) — разбор
 */
export const createAiCall = ({
    api, makeLink, scenarioKey, client = {}, onProblem = () => {}, onReview = () => {},
}) => {
    const listeners = new Set();
    const st = {
        session: null, link: null, busy: false, pending: null, held: false, micMuted: false,
        hungUp: false, review: null, reviewWaiters: [], finished: false,
    };

    const emit = (type, payload = {}) => {
        for (const fn of [...listeners]) {
            try { fn(type, payload); } catch { /* чужой подписчик разговор не роняет */ }
        }
    };

    const speak = async (text, turnId, since) => {
        if (!st.link || st.hungUp || !text) return;
        emit('speech', { text, turnId });
        try {
            const result = await st.link.speak(text, { turnId, since, sessionId: st.session });
            if (turnId && (result?.voice_to_voice_ms || result?.spoken)) {
                api(`/turns/${turnId}`, {
                    method: 'PATCH',
                    body: JSON.stringify({
                        voice_to_voice_ms: result.voice_to_voice_ms ?? undefined,
                        spoken_ms: result.spoken?.spoken_ms,
                        spoken_chars: result.spoken?.spoken_chars,
                        speech_cut: result.spoken?.cut,
                    }),
                }).catch(() => {});
            }
        } catch (error) {
            onProblem(`озвучка: ${error.message}`);
        } finally {
            emit('speech_end', { turnId });
        }
    };

    /* Реплика оператора → ответ водителя. Пока модель думает, сказанное
       дальше копится и уходит следующей репликой, а не теряется. */
    const reply = async ({ text, metrics, at }) => {
        if (!st.session || st.hungUp || !text) return;
        // На удержании водитель ждёт молча: сказанное в комнату — не ему.
        // С выключенным микрофоном в iCORE Phone — тем более.
        if (st.held || st.micMuted) return;
        if (st.busy) {
            st.pending = st.pending
                ? { text: `${st.pending.text} ${text}`.trim(), at, metrics: mergeMetrics(st.pending.metrics, metrics) }
                : { text, at, metrics };
            return;
        }
        st.busy = true;
        let answer = null;
        try {
            answer = await api(`/sessions/${st.session}/turn`, {
                method: 'POST', body: JSON.stringify({ text, ...(metrics || {}) }),
            });
        } catch (error) {
            onProblem(`водитель не ответил: ${error.message}`);
        } finally {
            st.busy = false;
        }
        if (answer && !st.hungUp) await speak(answer.text, answer.turn_id, at);
        const waiting = st.pending;
        st.pending = null;
        if (waiting && !st.hungUp) await reply(waiting);
    };

    const onLinkEvent = (type, payload = {}) => {
        if (type === 'utterance') {
            emit('level', { level: 0 });
            reply(payload);
        } else if (type === 'live') {
            // Человек говорит — индикатор микрофона в iCORE Phone оживает.
            emit('level', { level: payload.text ? 0.7 : 0 });
        } else if (type === 'error') {
            onProblem(`${payload.where || 'голос'}: ${payload.message || 'ошибка'}`);
        }
    };

    const answer = async () => {
        if (st.session || st.hungUp) return;
        try {
            const created = await api('/sessions', {
                method: 'POST',
                body: JSON.stringify({ mode: 'driver', scenario: scenarioKey, client }),
            });
            if (st.hungUp) {
                // Трубку положили, пока сервер заводил сессию: закрываем её сразу.
                api(`/sessions/${created.session_id}/finish`, { method: 'POST' }).catch(() => {});
                return;
            }
            st.session = created.session_id;
            st.link = makeLink(onLinkEvent);
            await st.link.start();
            if (created.opening && !st.hungUp) {
                await speak(created.opening, created.opening_turn_id ?? null, 0);
            }
        } catch (error) {
            try { st.link?.stop(); } catch { /* уже остановлен */ }
            st.link = null;
            onProblem(`не удалось начать разговор: ${error.message}`);
        }
    };

    const deliver = (result) => {
        st.review = result;
        st.reviewWaiters.splice(0).forEach((fn) => fn(result));
    };

    /* Трубку положили: голос гаснет сразу, разбор собирается в фоне — пока
       стажёр оформляет обращение, он уже будет готов. */
    const hangup = () => {
        if (st.hungUp) return;
        st.hungUp = true;
        st.pending = null;
        try { st.link?.stop(); } catch { /* уже остановлен */ }
        st.link = null;
        emit('speech_end', {});
        emit('level', { level: 0 });
        const id = st.session;
        if (!id) { deliver({ review: null, cost: null, review_error: 'разговора не было' }); return; }
        api(`/sessions/${id}/finish`, { method: 'POST' })
            .then((result) => deliver({
                review: result?.review || null,
                cost: result?.cost || null,
                review_error: result?.review_error || null,
            }))
            .catch((error) => deliver({ review: null, cost: null, review_error: error.message }));
    };

    /** Разбор — когда стажёр закончил постобработку. */
    const finish = () => new Promise((resolve) => {
        if (!st.hungUp) hangup();
        const give = (result) => {
            if (!st.finished) { st.finished = true; onReview(result); }
            resolve(result);
        };
        if (st.review) give(st.review);
        else st.reviewWaiters.push(give);
    });

    return {
        /* Интерфейс голоса для TrainerPlayer. */
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        start() {},
        stop() {},

        /* Действия звонка (TrainerPlayer.onCall). */
        onCall(type, payload = {}) {
            if (type === 'answer') answer();
            else if (type === 'mic') st.micMuted = Boolean(payload.muted);
            else if (type === 'hold') st.held = true;
            else if (type === 'unhold') st.held = false;
            else if (type === 'end' || type === 'transfer') hangup();
            else if (type === 'finish') finish();
        },

        finish,

        /** Закрыли тренажёр посреди разговора: микрофон и сессия — закрыть. */
        dispose() {
            if (!st.hungUp) hangup();
            listeners.clear();
        },

        /* Для тестов и отладки. */
        get state() {
            return { session: st.session, held: st.held, hungUp: st.hungUp, busy: st.busy };
        },
    };
};
