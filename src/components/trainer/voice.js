/* Раздел «Тренажёр»: голосовой канал браузера.
 *
 * Держится отдельно от разметки сознательно: здесь живут микрофон, сокет
 * распознавания, очередь воспроизведения и все замеры, а в JSX остаётся только
 * показ. Поведение, решённое внутри разметки, в этом проекте уже стоило
 * четырёх тематик — правило «логика в .js, а не в JSX» здесь соблюдается.
 *
 * Куда что идёт:
 *   микрофон → ПРЯМО в Soniox по временному ключу (звук мимо нашего сервера);
 *   ответ    → наш /api/trainer/speak, оттуда SSE с кусками звука.
 *
 * Поток озвучки: 'start' (провайдер, модель, частота) → 'audio'… → 'done'.
 * Событие 'error' означает, что звука не будет вовсе, и его обязан увидеть
 * человек: ровно этого не хватало 22.08.2026, когда у провайдера кончились
 * кредиты и раздел сутки молчал, не сказав ни слова о причине.
 *
 * Почему по-разному. Микрофон стримит непрерывно, и гонять этот поток через
 * Render значило бы платить лишним кругом на каждом кадре. Ответ звучит раз в
 * реплику, а Live API эфемерные токены в браузере не принимает — проверено
 * 22.08.2026, сокет закрывается с 1008. Постоянному ключу в браузере не место,
 * поэтому озвучку отдаёт сервер.
 *
 * ДВА ЧАСА, КОТОРЫЕ ЗДЕСЬ СВЕРЯЮТСЯ. Речь человека размечена по шкале
 * микрофона (Soniox отдаёт start_ms/end_ms от начала потока), а речь
 * собеседника — по шкале звуковой подсистемы (ctx.currentTime). Пока эти шкалы
 * не были сведены, «человек заговорил» и «собеседник заговорил» нельзя было
 * упорядочить, и хвост фразы самого человека считался перебиванием. Место
 * сведения одно: playChunk, где первый кусок реплики ставится в расписание.
 */

/* Расширение '.js' в импортах обязательно: эти модули проверяются через
 * `node --test` без сборщика, а Node не достраивает расширение сам. Vite
 * принимает оба вида, поэтому ошибка вылезает только в тестах. */
import { bargeVerdict, spokenChars, weighText } from './speechClock.js';
import { connectTelephone } from './telephone.js';

const SONIOX_WS = 'wss://stt-rt.soniox.com/transcribe-websocket';

/**
 * Адрес модуля захвата микрофона с учётом базы сборки.
 *
 * BASE_URL у Vite всегда заканчивается слэшем ('/' локально, '/OTP/' на Pages),
 * но подстраховываемся: одна пропущенная косая превращает путь в
 * '/OTPtrainer-worklet.js' и ошибка выглядит точно так же, как отсутствие файла.
 */
export const workletUrl = (base = import.meta.env?.BASE_URL || '/') => {
    const root = base || '/';
    return `${root.endsWith('/') ? root : `${root}/`}trainer-worklet.js`;
};

/* Слова, после которых фраза почти наверняка продолжится: союзы, предлоги и
 * казахские послелоги. Список не теоретический — здесь ровно те хвосты, на
 * которых прод обрывал живых людей 22.08.2026: «Ты уверен, что.», «Нужно делать
 * всего лишь.», «Вижу то, что заказ был полностью на.», «Акциясы бойынша.». */
const DANGLING = new Set([
    'и', 'а', 'но', 'или', 'что', 'чтобы', 'если', 'когда', 'как', 'где', 'куда',
    'кто', 'потому', 'поэтому', 'значит', 'который', 'которая', 'которое',
    'для', 'про', 'с', 'со', 'к', 'ко', 'на', 'в', 'во', 'о', 'об', 'от', 'до',
    'по', 'за', 'из', 'у', 'при', 'без', 'через', 'между', 'над', 'под', 'перед',
    'только', 'всего', 'лишь', 'ещё', 'еще', 'уже', 'вот', 'это', 'этот', 'эта',
    'мой', 'моя', 'наш', 'ваш', 'его', 'её', 'их', 'то', 'та', 'тот', 'же',
    // казахский
    'бойынша', 'туралы', 'үшін', 'және', 'немесе', 'мен', 'бен', 'пен',
    'кейін', 'дейін', 'қандай', 'қалай', 'сол', 'бұл', 'осы', 'мына',
]);

const WORDS = /[\p{L}\d]+/gu;

/**
 * Сколько ждать продолжения фразы после того, как распознавание поставило точку.
 *
 * Soniox ставит её по паузе в 600 мс, и этого мало: человек думает посреди
 * предложения дольше. На проде из-за этого обрывали на полуслове — реплики
 * уходили в модель кусками, а собеседник отвечал на обрывок.
 *
 * Выдержка адаптивная, потому что платить ею за КАЖДУЮ реплику незачем:
 * законченная фраза ждёт чуть-чуть, оборванная — заметно дольше. Признак
 * незаконченности простой и проверяемый: мало слов или хвост-связка.
 */
export const utteranceHold = (text, { short = 300, long = 1200 } = {}) => {
    const trimmed = String(text || '').trim();
    if (!trimmed) return short;
    // Тире или запятая в конце — фразу точно не закончили.
    if (/[,:;–—-]$/.test(trimmed)) return long;
    const words = trimmed.toLowerCase().match(WORDS) || [];
    if (words.length < 3) return long;
    if (DANGLING.has(words[words.length - 1])) return long;
    return short;
};

/** Среднее по массиву или null — чтобы в метриках не появлялся NaN. */
const mean = (values) => (values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : null);

/* Как часто перерисовывать текст под речь. 80 мс — заметно чаще, чем человек
 * различает рывки, и заметно реже, чем приходит кусок звука. */
const PAINT_MS = 80;

export class VoiceLink {
    /**
     * @param {object} config
     * @param {string} config.apiBaseUrl
     * @param {function} config.headers  () => объект заголовков авторизации
     * @param {function} config.onEvent  (type, payload) — единственный выход наружу
     */
    constructor({ apiBaseUrl, headers, onEvent, hold, barge, telephone = true }) {
        this.apiBaseUrl = apiBaseUrl;
        this.headers = headers;
        this.emit = onEvent || (() => {});
        // Выдержка против перебивания. Значения приходят с сервера в /tokens,
        // чтобы подбирать их переменной окружения, а не пересборкой фронта.
        this.hold = { short: 300, long: 1200, ...(hold || {}) };
        // Пороги «что считать перебиванием» — оттуда же и по той же причине.
        this.barge = {
            enabled: true, grace_ms: 250, min_words: 2, min_ms: 350,
            min_confidence: 0.7, echo_ratio: 0.6, backoff_ms: 800,
            ...(barge || {}),
        };
        this.telephone = telephone;

        this.ctx = null;
        this.stream = null;
        this.node = null;
        this.stt = null;
        this.keepalive = null;
        this.phone = null;        // телефонный тракт, собирается один раз

        this.sources = [];
        this.playAt = 0;
        this.streaming = false;   // жив поток SSE (это НЕ «звучит речь»)
        // Частота приходит от сервера событием 'start': у провайдеров озвучки
        // она своя, и угадывать её на клиенте значит играть речь не с той
        // скоростью. 24 000 — только значение до первого события.
        this.rate = 24000;
        this.charsPerSec = 13.3;  // замер по проду; приходит с сервера

        this.pending = '';        // подтверждённый текст текущей реплики
        this.partial = '';        // неподтверждённый — ПЕРЕПИСЫВАЕТСЯ целиком
        this.lastVoiceAt = 0;     // когда человек в последний раз звучал
        this.holdTimer = null;    // выдержка: ждём, не продолжит ли человек
        this.heldText = '';       // текст, на котором выдержка уже заведена
        this.holdMs = 0;          // сколько прождали — уходит в замеры реплики
        this.endedAt = 0;         // когда распознавание поставило точку
        this.confidences = [];
        this.langs = {};
        this.tokens = 0;
        this.startedAt = 0;

        // ── часы микрофона: та же шкала, в которой Soniox размечает токены ──
        this.micMs = 0;           // сколько звука реально ушло в сокет
        this.uttMs = 0;           // сколько его пришлось на текущую реплику

        // ── речь собеседника ──
        this.speechText = '';     // что произносится прямо сейчас
        this.speechTurnId = null;
        this.speechWeight = 0;    // вес текста в «буквенном эквиваленте»
        this.expectedMs = 0;      // ожидаемая длительность (до конца потока)
        this.totalMs = 0;         // фактическая, когда поток закончился
        this.startAt = null;      // ctx.currentTime первого запланированного сэмпла
        this.audibleFromMicMs = null;  // тот же момент по часам микрофона
        this.scheduledMs = 0;     // сколько звука поставлено в расписание
        this.gapMs = 0;           // дыры в расписании: это тишина, не речь
        this.paintedChars = -1;
        this.painter = null;
        this.backoffUntil = 0;

        this.quietTimer = null;
        this.quietDone = null;       // чем разбудить ожидание конца речи
        // Номер реплики. Асинхронный хвост завершающегося speak() не должен
        // дописывать свой итог в состояние, которое уже принадлежит следующей
        // реплике: поля здесь общие на объект, и без этого счётчика второй
        // вызов молча затирал замеры первого.
        this.speakId = 0;
        this.voiceGain = null;       // общий кран: им гасим звук без щелчка
        this.closingOnPurpose = false;   // мы сами закрываем сокет, это не отказ
        this.pendingBarge = false;   // перебил ли человек текущую реплику
        this.lastSpoken = null;      // что услышано из прошлой реплики
    }

    // ── запуск ───────────────────────────────────────────────────────────────

    async start() {
        const response = await fetch(`${this.apiBaseUrl}/api/trainer/tokens`, {
            method: 'POST', headers: { ...this.headers(), 'Content-Type': 'application/json' },
            body: '{}',
        });
        if (!response.ok) throw new Error(`ключи не выданы: HTTP ${response.status}`);
        const tokens = await response.json();
        if (!tokens?.soniox?.api_key) {
            throw new Error((tokens?.problems || ['распознавание недоступно']).join('; '));
        }

        // echoCancellation обязателен: колонки и микрофон в одной комнате, без
        // него агент слышит сам себя и перебивает себя же.
        this.stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true, noiseSuppression: true,
                autoGainControl: true, channelCount: 1,
            },
        });
        this.ctx = new AudioContext();
        await this.ctx.resume();
        // Путь к воркеру — ОТ БАЗЫ СБОРКИ, а не от корня домена. Фронт едет на
        // GitHub Pages в подпапку (/OTP/), и абсолютный '/trainer-worklet.js'
        // уходил в корень домена: браузер отвечал «Unable to load a worklet's
        // module», а локально, где база '/', всё работало.
        await this.ctx.audioWorklet.addModule(workletUrl());
        // Тракт собирается ОДИН раз на контекст: у биквадов есть состояние, и
        // новая цепочка на каждый кусок рвала бы речь щелчками на стыках.
        if (this.telephone) {
            try { this.phone = connectTelephone(this.ctx); } catch { this.phone = null; }
        }

        await this.openStt(tokens);
        this.startedAt = performance.now();

        const source = this.ctx.createMediaStreamSource(this.stream);
        this.node = new AudioWorkletNode(this.ctx, 'trainer-capture');
        this.node.port.onmessage = (event) => {
            if (this.stt && this.stt.readyState === WebSocket.OPEN) {
                // Часы двигаем ТОЛЬКО на реально отправленный звук: иначе они
                // разойдутся с разметкой Soniox, и сверка «кто заговорил
                // раньше» начнёт врать в обе стороны.
                const ms = (event.data.byteLength / 2) / this.ctx.sampleRate * 1000;
                this.micMs += ms;
                this.uttMs += ms;
                this.stt.send(event.data);
            }
        };
        source.connect(this.node);
        // Узел обязан быть в графе, иначе часть браузеров его не запускает.
        const mute = this.ctx.createGain();
        mute.gain.value = 0;
        this.node.connect(mute);
        mute.connect(this.ctx.destination);

        if (tokens.stt?.hold) this.hold = { ...this.hold, ...tokens.stt.hold };
        if (tokens.stt?.barge) this.barge = { ...this.barge, ...tokens.stt.barge };
        if (tokens.tts?.chars_per_sec) this.charsPerSec = tokens.tts.chars_per_sec;
        this.emit('ready', { sampleRate: this.ctx.sampleRate, tts: tokens.tts });
        return { sampleRate: this.ctx.sampleRate };
    }

    openStt(tokens) {
        return new Promise((resolve, reject) => {
            const socket = new WebSocket(SONIOX_WS);
            socket.binaryType = 'arraybuffer';
            this.stt = socket;

            socket.onopen = () => {
                socket.send(JSON.stringify({
                    api_key: tokens.soniox.api_key,
                    model: tokens.stt?.model || 'stt-rt-v5',
                    audio_format: 'pcm_s16le',
                    sample_rate: this.ctx.sampleRate,
                    num_channels: 1,
                    language_hints: ['ru', 'kk'],
                    enable_language_identification: true,
                    enable_endpoint_detection: true,
                    // По умолчанию Soniox ждёт 2000 мс тишины, и человек слышит
                    // это как «оно висит». 600 мс заметно живее.
                    max_endpoint_delay_ms: tokens.stt?.endpoint_ms || 600,
                }));
                // Пока говорит собеседник, а человек молчит, поток пустой, и без
                // служебного кадра Soniox рвёт сессию по таймауту в 20 секунд.
                this.keepalive = setInterval(() => {
                    if (socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({ type: 'keepalive' }));
                    }
                }, 10000);
                resolve();
            };
            socket.onerror = () => reject(new Error('распознавание не подключилось'));
            socket.onclose = (event) => {
                clearInterval(this.keepalive);
                // Молчащий обрыв выглядит как «бот перестал меня слышать»:
                // фаза остаётся «слушаю вас», кадры микрофона тихо уходят в
                // никуда, а часы микрофона замирают — вместе с ними врёт и
                // сверка «кто заговорил раньше». Отказ обязан быть виден.
                if (this.stt === socket && !this.closingOnPurpose) {
                    this.emit('error', {
                        where: 'stt',
                        message: `распознавание отключилось (${event?.code ?? '—'})`
                                 + ' — начните разговор заново',
                    });
                }
            };
            socket.onmessage = (event) => this.onSttMessage(event);
        });
    }

    onSttMessage(event) {
        let data;
        try { data = JSON.parse(event.data); } catch { return; }
        if (data.error_code || data.error_message) {
            this.emit('error', { where: 'stt', message: data.error_message || 'ошибка распознавания' });
            return;
        }

        // Запоминаем ДО обновления: иначе выдержка распознавания всегда выходит
        // нулём — текст и метка конца приходят одним сообщением.
        const previousVoiceAt = this.lastVoiceAt;
        const incoming = [];
        let fresh = '';
        let ended = false;
        let voiced = false;
        for (const token of data.tokens || []) {
            const text = token.text || '';
            if (text === '<end>' || text === '<fin>') {
                ended = ended || text === '<end>';
                continue;
            }
            // Неокончательные токены приходят ПЕРЕПИСАННЫМИ целиком на каждом
            // сообщении — накапливать их нельзя, иначе текст множится сам на себя.
            if (token.is_final) {
                this.pending += text;
                this.tokens += 1;
                if (typeof token.confidence === 'number') this.confidences.push(token.confidence);
                if (token.language) this.langs[token.language] = (this.langs[token.language] || 0) + 1;
            } else {
                fresh += text;
            }
            if (text.trim()) {
                voiced = true;
                incoming.push(token);
            }
        }
        this.partial = fresh;

        if (voiced) {
            this.lastVoiceAt = performance.now();
            // Человек говорит — отправку откладываем. Раньше здесь стоял голый
            // clearTimeout БЕЗ перезавода: один шумовой токен гасил выдержку
            // насмерть, и реплика не уходила вовсе, пока человек не заговорит
            // снова. Перезаводим.
            this.scheduleUtterance();

            // Перебивание это или нет — решает speechClock, а не факт звука.
            // Пока не прозвучало ни одного сэмпла речи собеседника, перебивать
            // нечего: это и есть тот случай, который на проде убивал каждую
            // пятую реплику.
            const verdict = bargeVerdict(incoming, {
                audibleFromMicMs: this.audibleFromMicMs,
                atMs: this.micMs,
                saying: this.speechText,
                saidChars: this.paintedChars < 0 ? 0 : this.paintedChars,
                enabled: this.barge.enabled,
                graceMs: this.barge.grace_ms,
                minWords: this.barge.min_words,
                minMs: this.barge.min_ms,
                minConfidence: this.barge.min_confidence,
                echoRatio: this.barge.echo_ratio,
            });
            if (verdict.barge) this.bargeIn(verdict);
            else if (this.audible && verdict.rule !== 'quiet') {
                // Не перебивание, но и не тишина: пригодится при подборе порогов.
                this.emit('heard', { rule: verdict.rule, words: verdict.words });
            }
        }

        const live = (this.pending + this.partial).trim();
        if (live) this.emit('live', { text: live });

        if (ended) {
            // НЕ отправляем сразу. Распознавание ставит точку по паузе в 600 мс,
            // а человек посреди предложения думает дольше — на проде из-за этого
            // собеседник отвечал на обрывок фразы. Ждём выдержку; если человек
            // продолжит, таймер отменится выше, а накопленный текст останется.
            this.endedAt = performance.now();
            this.endpointMs = previousVoiceAt
                ? Math.round(this.endedAt - previousVoiceAt) : null;
            this.scheduleUtterance();
        }
    }

    /** Отправить реплику, если человек не продолжит в течение выдержки. */
    scheduleUtterance() {
        const text = (this.pending + this.partial).trim();
        if (!text) return;
        // Повторная точка на том же тексте таймер НЕ перезапускает: иначе
        // распознавание, прислав её дважды, отодвигало бы отправку без конца.
        if (this.holdTimer && text === this.heldText) return;
        clearTimeout(this.holdTimer);
        this.heldText = text;
        const wait = utteranceHold(text, this.hold);
        this.holdMs = wait;
        this.holdTimer = setTimeout(() => this.flushUtterance(), wait);
    }

    flushUtterance() {
        this.holdTimer = null;
        this.heldText = '';
        const text = (this.pending + this.partial).trim();
        // Собеседник ГОВОРИТ, а сказанное человеком перебиванием не признано:
        // это поддакивание, эхо или шум. Отправлять такое отдельным ходом
        // нельзя — модель ответит на «ага», а озвучка второй реплики оборвёт
        // первую на полуслове. Придерживаем до конца речи: накопленное никуда
        // не денется, а уйдёт следующей репликой.
        if (text && this.audible && !this.pendingBarge) {
            this.heldText = text;
            this.holdTimer = setTimeout(() => this.flushUtterance(), 250);
            return;
        }
        const metrics = {
            stt_confidence: mean(this.confidences),
            stt_tokens: this.tokens,
            stt_langs: this.langs,
            stt_lang: Object.entries(this.langs).sort((a, b) => b[1] - a[1])[0]?.[0] || null,
            // Сколько распознавание думало после того, как человек замолчал…
            endpoint_delay_ms: this.endpointMs ?? null,
            // …и сколько ждали мы сами, чтобы не перебить.
            hold_ms: this.holdMs || null,
            // Сколько звука пришлось на эту реплику. Не «регрессия»: этого поля
            // фронт не слал НИКОГДА с первого коммита раздела, и на проде оно
            // пусто у 41 реплики из 68 — вместе с ним занижена и стоимость.
            stt_audio_ms: Math.round(this.uttMs) || null,
            // Перебил ли человек собеседника. Колонка есть с первого дня и до
            // 22.08.2026 не была заполнена НИ РАЗУ: браузер её не отправлял.
            barge_in: this.pendingBarge,
            // Что человек реально услышал из прошлой реплики собеседника.
            // Едет здесь, а не отдельным PATCH: PATCH уходит «выстрелил и
            // забыл», а сервер режет историю для модели именно по этим числам.
            prev: this.lastSpoken,
        };
        this.pending = '';
        this.partial = '';
        this.confidences = [];
        this.langs = {};
        this.tokens = 0;
        this.uttMs = 0;
        this.pendingBarge = false;
        this.lastSpoken = null;
        if (text) this.emit('utterance', { text, metrics, at: this.lastVoiceAt });
    }

    // ── воспроизведение ──────────────────────────────────────────────────────

    /** Звучит ли речь собеседника прямо сейчас (а не «жив ли поток»). */
    get audible() {
        return this.audibleFromMicMs !== null;
    }

    /** Сколько миллисекунд речи РЕАЛЬНО дошло до уха. */
    playedMs() {
        if (this.startAt === null || !this.ctx) return 0;
        const late = this.ctx.outputLatency || this.ctx.baseLatency || 0;
        const played = (this.ctx.currentTime - late - this.startAt) * 1000 - this.gapMs;
        return Math.max(0, Math.min(played, this.scheduledMs));
    }

    /** Что услышано из текущей реплики — то, что уйдёт на сервер. */
    spokenNow() {
        if (!this.speechText) return null;
        const ms = Math.round(this.playedMs());
        const total = this.totalMs || this.expectedMs;
        const chars = spokenChars(this.speechText, ms, total);
        return {
            turn_id: this.speechTurnId,
            spoken_ms: ms,
            spoken_chars: chars,
            cut: chars < this.speechText.length,
        };
    }

    /** Общий кран для всех источников речи: через него звук и гасится. */
    voiceOut() {
        if (!this.voiceGain && this.ctx) {
            this.voiceGain = this.ctx.createGain();
            this.voiceGain.connect(this.phone ? this.phone.input : this.ctx.destination);
        }
        return this.voiceGain || this.ctx.destination;
    }

    stopPlayback() {
        const spoken = this.spokenNow();
        // Гасим коротким спадом, а не обрывом волны: раньше источник шёл прямо
        // в выход и обрыв был просто щелчком, а теперь ступенька попадает во
        // вход телефонного тракта и раскачивает биквады звоном на 300 Гц.
        if (this.voiceGain && this.ctx) {
            const now = this.ctx.currentTime;
            try {
                this.voiceGain.gain.cancelScheduledValues(now);
                this.voiceGain.gain.setValueAtTime(this.voiceGain.gain.value, now);
                this.voiceGain.gain.linearRampToValueAtTime(0, now + 0.012);
            } catch { /* поддельный контекст в тестах */ }
        }
        this.sources.forEach((source) => { try { source.stop(); } catch { /* уже остановлен */ } });
        this.sources = [];
        this.playAt = 0;
        this.startAt = null;
        this.audibleFromMicMs = null;
        this.scheduledMs = 0;
        this.gapMs = 0;
        return spoken;
    }

    /** Человек действительно перебил: гасим звук и запоминаем услышанное. */
    bargeIn(verdict) {
        // С выключенным гейтом (TRAINER_BARGE_ENABLED=0) перебиваем и до
        // первого звука: прежнее поведение опиралось на флаг, который
        // поднимался ДО запроса к /speak, и аварийный откат обязан возвращать
        // его ЦЕЛИКОМ, а не наполовину.
        if (!this.audible && this.barge.enabled) return;
        if (!this.audible) { this.streaming = false; this.pendingBarge = true; return; }
        const spoken = this.stopPlayback();
        this.streaming = false;
        this.pendingBarge = true;
        this.lastSpoken = spoken;
        this.backoffUntil = performance.now() + this.barge.backoff_ms;
        this.emit('barge', {
            turn_id: spoken?.turn_id ?? null,
            spoken_ms: spoken?.spoken_ms ?? 0,
            spoken_chars: spoken?.spoken_chars ?? 0,
            total_chars: this.speechText.length,
            rule: verdict.rule,
            words: verdict.words,
            voice_ms: verdict.voiceMs,
        });
    }

    /** Частота текущего потока озвучки — её объявляет сервер событием 'start'. */
    get playbackRate() {
        return this.rate;
    }

    playChunk(base64, rate) {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        const pcm = new Int16Array(bytes.buffer);
        const buffer = this.ctx.createBuffer(1, pcm.length, rate);
        const channel = buffer.getChannelData(0);
        for (let i = 0; i < pcm.length; i += 1) channel[i] = pcm[i] / 32768;

        const source = this.ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(this.voiceOut());
        // Небольшой запас перед первым куском: иначе на медленной сети следующий
        // не успевает приехать и речь рвётся щелчками.
        const at = Math.max(this.ctx.currentTime + 0.08, this.playAt);
        if (this.startAt !== null) {
            // Расписание разорвалось — кусок не успел приехать. Эта дыра
            // ТИШИНА, и засчитывать её как прозвучавшую речь нельзя.
            this.gapMs += Math.max(0, (at - this.playAt) * 1000);
        } else {
            this.startAt = at;
            // Слышимая пауза считается ОТСЮДА, а не от прихода куска: между
            // ними лежат запас расписания и задержка вывода, 100-400 мс при
            // типовых двух секундах. Сервер и обещает «когда звук реально
            // зазвучал в колонках».
            if (this.paceFrom) {
                this.paceMs = Math.round(performance.now() - this.paceFrom
                                         + (at - this.ctx.currentTime) * 1000);
                this.paceFrom = 0;
            }
            // Тот же момент по часам микрофона. Обе поправки (запас в
            // расписании и задержка вывода) смещают отметку ВПЕРЁД, то есть в
            // безопасную сторону: лишнее поглощается grace_ms.
            const late = this.ctx.outputLatency || this.ctx.baseLatency || 0;
            this.audibleFromMicMs = this.micMs + (at - this.ctx.currentTime) * 1000 + late * 1000;
            this.emit('speech_start', { turn_id: this.speechTurnId });
        }
        source.start(at);
        this.playAt = at + buffer.duration;
        this.scheduledMs += buffer.duration * 1000;
        this.sources.push(source);
        source.onended = () => { this.sources = this.sources.filter((s) => s !== source); };
    }

    /** Открывать текст под речь: то, что уже прозвучало, — то и на экране. */
    paint() {
        // Молчим, когда речи нет: после перебивания часы сброшены, и очередной
        // тик посчитал бы ноль знаков — экран стирал бы уже показанный текст.
        if (!this.speechText || !this.audible) return;
        const total = this.totalMs || this.expectedMs;
        const chars = spokenChars(this.speechText, this.playedMs(), total);
        if (chars === this.paintedChars) return;
        this.paintedChars = chars;
        this.emit('said', {
            turn_id: this.speechTurnId,
            chars,
            text: this.speechText.slice(0, chars),
        });
    }

    /** Ждём, пока звук РЕАЛЬНО доиграет, а не пока закроется поток.
     *
     * Разница не теоретическая: поток озвучки закрывается за 3,3-3,8 с, а речь
     * при этом звучит 8,8-9,4 с (замер 22.08.2026). Пока фаза «собеседник
     * говорит» снималась по концу потока, раздел объявлял себя слушающим за
     * пять секунд до того, как собеседник замолчал.
     *
     * Ожидание ОБЯЗАНО просыпаться при уходе с раздела: иначе stop() гасит
     * таймер, обещание не исполняется никогда, и «Завершить» повисает.
     */
    untilQuiet() {
        return new Promise((resolve) => {
            const finish = () => {
                clearInterval(this.quietTimer);
                this.quietTimer = null;
                this.quietDone = null;
                resolve();
            };
            this.quietDone = finish;
            const tick = () => {
                if (!this.audible || this.playedMs() >= this.scheduledMs - 20) finish();
            };
            // Потолок обязателен: если вкладку свернули и AudioContext ушёл в
            // suspended, ctx.currentTime замирает, playedMs перестаёт расти —
            // и обещание не исполнилось бы никогда, а раздел навсегда остался
            // бы в фазе «собеседник говорит».
            const deadline = performance.now() + this.scheduledMs + 2000;
            this.quietTimer = setInterval(() => {
                if (performance.now() > deadline) { finish(); return; }
                tick();
            }, 60);
            tick();
        });
    }

    /**
     * Произносит текст: читает SSE от нашего сервера и играет куски по мере
     * прихода. Возвращает замеры, включая слышимую паузу и то, сколько из
     * реплики реально дошло до уха.
     */
    async speak(text, { turnId = null, since = 0, sessionId = null } = {}) {
        // Номер этой реплики. Всё, что пишется в общее состояние ПОСЛЕ любого
        // await, обязано сверяться с ним: пока мы ждём, человек мог заговорить,
        // и следующая реплика уже началась.
        const id = ++this.speakId;
        const mine = () => id === this.speakId;

        // Первой строкой, а не «где-нибудь»: раньше speak обнулял playAt, НЕ
        // остановив прежние источники, и вторая реплика ложилась поверх первой.
        this.stopPlayback();
        // Кран после гашения закрыт — открываем заново, иначе новая реплика
        // будет синтезирована, отправлена и не услышана.
        if (this.voiceGain && this.ctx) {
            try {
                this.voiceGain.gain.cancelScheduledValues(this.ctx.currentTime);
                this.voiceGain.gain.setValueAtTime(1, this.ctx.currentTime);
            } catch { /* поддельный контекст в тестах */ }
        }
        // После подтверждённого перебивания выдерживаем паузу: иначе хвост той
        // же речи человека убьёт и следующую реплику.
        const wait = this.backoffUntil - performance.now();
        if (wait > 0) {
            await new Promise((done) => {
                // Ручку храним: без неё «Завершить», нажатое в эти 800 мс,
                // не гасило таймер, и добуженный speak уходил запросом уже
                // закрытой сессии и падал на закрытом контексте.
                this.backoffTimer = setTimeout(done, wait);
            });
        }
        if (!mine() || !this.ctx) return { superseded: true };

        this.streaming = true;
        this.rate = 24000;
        this.speechText = String(text || '');
        this.speechTurnId = turnId;
        this.speechWeight = weighText(this.speechText).total;
        // Пока поток не кончился, полной длительности мы не знаем. Берём оценку
        // по замеренному темпу речи (13,3 ЗНАКА в секунду на проде) — она нужна
        // только для плавности показа; окончательный разрез считается по
        // фактической длительности.
        //
        // Делим именно ДЛИНУ, а не вес: вес — это «буквенный эквивалент», где
        // точка стоит 6,5, а цифра 3,5, и его отношение к длине на живых
        // репликах 1,06-2,10. Поделив вес на темп в знаках, мы завышали
        // длительность вдвое на коротких репликах — то есть ровно на тех, ради
        // которых переписан промпт. Вес продолжает распределять позицию ВНУТРИ
        // этой длительности, и это верно: пауза на точке звучит дольше буквы.
        this.expectedMs = Math.max(1, (this.speechText.length / this.charsPerSec) * 1000);
        this.totalMs = 0;
        this.paintedChars = -1;
        this.paceFrom = since;      // от какого мига считать слышимую паузу
        this.paceMs = null;
        let done = {};
        let chunks = 0;
        let failure = null;
        let interrupted = false;

        const response = await fetch(`${this.apiBaseUrl}/api/trainer/speak`, {
            method: 'POST',
            headers: { ...this.headers(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, turn_id: turnId, session_id: sessionId }),
        });
        if (!mine()) { try { response.body?.cancel(); } catch { /* уже закрыт */ } 
                       return { superseded: true }; }
        if (!response.ok || !response.body) {
            this.streaming = false;
            this.speechText = '';
            throw new Error(`озвучка недоступна: HTTP ${response.status}`);
        }

        this.painter = setInterval(() => this.paint(), PAINT_MS);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let stopped = false;
        try {
            while (!stopped) {
                const { value, done: finished } = await reader.read();
                if (finished) break;
                buffer += decoder.decode(value, { stream: true });
                const parts = buffer.split('\n\n');
                buffer = parts.pop() || '';
                for (const part of parts) {
                    const line = part.trim();
                    if (!line.startsWith('data:')) continue;
                    let payload;
                    try { payload = JSON.parse(line.slice(5).trim()); } catch { continue; }
                    if (payload.t === 'start') {
                        this.rate = payload.rate || this.rate;
                    } else if (payload.t === 'audio') {
                        // Перебили — дочитывать поток незачем: остаток реплики уже
                        // не прозвучит. Раньше здесь был break из внутреннего цикла,
                        // и чтение продолжалось до конца синтеза впустую.
                        if (!this.streaming) { stopped = true; break; }
                        chunks += 1;
                        try {
                            this.playChunk(payload.b64, this.rate);
                        } catch (error) {
                            // Нечётная длина куска или ноль отсчётов роняли весь
                            // цикл, и объект оставался «говорящим» навсегда.
                            failure = `кусок звука не воспроизвёлся: ${error.message}`;
                            this.emit('error', { where: 'tts', message: failure });
                            stopped = true;
                            break;
                        }
                    } else if (payload.t === 'done') {
                        done = payload;
                    } else if (payload.t === 'error') {
                        failure = payload.message;
                        this.emit('error', { where: 'tts', message: payload.message });
                    }
                }
            }
            if (stopped) { try { await reader.cancel(); } catch { /* поток уже закрыт */ } }

            // ПЕРЕБИЛИ ЛИ — узнаём по флагу потока, который гасит bargeIn, а НЕ
            // по `stopped`. Разница не теоретическая: все куски звука успевают
            // приехать за первые полсекунды, а перебивают на второй, и тогда
            // ни одного события 'audio' после перебивания уже не приходит —
            // `stopped` так и остаётся ложью. Прогон в Chrome показал, чем это
            // кончается: реплика отчитывалась как «прозвучало 0 из 81 знака»,
            // текст на экране обнулялся, и модель считала, что не сказала
            // ничего, — то есть договаривать было бы нечего.
            if (this.streaming) {
                // Поток кончился — длительность известна точно, и разрез
                // считается по ней, а не по оценке темпа.
                this.totalMs = this.scheduledMs || done.audio_ms || this.expectedMs;
                if (chunks) await this.untilQuiet();
            } else if (!this.totalMs) {
                // Перебили ДО конца потока: полной длительности синтеза мы уже
                // не узнаем, но оставлять ноль нельзя — по нулю реплика
                // выпадает из показателя «Дослушано», и он считается только по
                // недоперебитым, то есть систематически завышен.
                this.totalMs = done.audio_ms || this.expectedMs;
            }
            // Проверять ПОСЛЕ ожидания, а не до: поток озвучки закрывается за
            // полсекунды, а речь звучит три, и перебивают как раз в эти три.
            // Прогон в Chrome ловил ровно это — проверка стояла раньше
            // ожидания, флаг оставался ложью, и реплика отчитывалась как
            // «прозвучало 0 из 81 знака».
            interrupted = !this.streaming;
        } finally {
            clearInterval(this.painter);
            this.painter = null;
        }

        if (!mine()) return { superseded: true };
        this.streaming = false;
        // Молчание — это отказ, а не успех. Сервер закрывал соединение с нулём
        // байт, раздел досылал 'done', и человек видел текст реплики при полной
        // тишине. Теперь тишина доходит наверх ошибкой с причиной.
        if (!chunks && !interrupted) {
            this.speechText = '';
            throw new Error(failure || done.error || 'провайдер не дал ни одного куска звука');
        }

        // Перебили — услышанное уже посчитано в момент обрыва, пересчитывать
        // его нельзя: часы к этому моменту сброшены и дадут ноль.
        //
        // Не перебили — значит прозвучало ВСЁ, и считать тут тоже нечего.
        // Пересчёт здесь врал: ожидание конца речи выходит с допуском в 20 мс,
        // а разрез по знакам требует строгого равенства, и дозвучавшая реплика
        // получала «длина минус один знак» и отметку «оборвана».
        // Отказ провайдера посреди речи — это тоже обрыв, хотя поток закрылся
        // штатно: синтез не договорил, и человек этого не слышал.
        const cutByProvider = !!failure && chunks > 0;
        const spoken = interrupted ? this.lastSpoken : {
            turn_id: turnId,
            spoken_ms: Math.round(this.scheduledMs),
            spoken_chars: cutByProvider
                ? spokenChars(this.speechText, this.playedMs(), this.expectedMs)
                : this.speechText.length,
            cut: cutByProvider,
        };
        if (!interrupted) {
            this.paint();
            this.lastSpoken = spoken;
            this.stopPlayback();
        }
        this.emit('speech_end', {
            turn_id: turnId,
            spoken_ms: spoken?.spoken_ms ?? 0,
            spoken_chars: spoken?.spoken_chars ?? this.speechText.length,
            total_ms: this.totalMs,
            cut: !!spoken?.cut,
        });
        this.speechText = '';
        return { ...done, voice_to_voice_ms: this.paceMs, spoken };
    }

    // ── остановка ────────────────────────────────────────────────────────────

    stop() {
        this.closingOnPurpose = true;
        this.speakId += 1;            // хвосты незавершённых реплик обесцениваем
        clearTimeout(this.backoffTimer);
        clearInterval(this.keepalive);
        clearInterval(this.painter);
        this.painter = null;
        // Будим ожидание конца речи, а не просто гасим его таймер: иначе
        // обещание в speak() не исполнится никогда и «Завершить» повиснет.
        if (this.quietDone) this.quietDone();
        clearInterval(this.quietTimer);
        this.quietTimer = null;
        clearTimeout(this.holdTimer);
        this.streaming = false;
        this.stopPlayback();
        if (this.voiceGain) {
            try { this.voiceGain.disconnect(); } catch { /* контекст уже закрыт */ }
            this.voiceGain = null;
        }
        if (this.phone) { this.phone.dispose(); this.phone = null; }
        if (this.node) this.node.port.onmessage = null;
        if (this.stt && this.stt.readyState === WebSocket.OPEN) {
            try { this.stt.send(''); } catch { /* уже закрыт */ }
            try { this.stt.close(); } catch { /* уже закрыт */ }
        }
        if (this.stream) this.stream.getTracks().forEach((track) => track.stop());
        if (this.ctx && this.ctx.state !== 'closed') { try { this.ctx.close(); } catch { /* ok */ } }
        this.ctx = null;
        this.stt = null;
    }
}
