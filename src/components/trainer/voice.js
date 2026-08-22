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
 */

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

export class VoiceLink {
    /**
     * @param {object} config
     * @param {string} config.apiBaseUrl
     * @param {function} config.headers  () => объект заголовков авторизации
     * @param {function} config.onEvent  (type, payload) — единственный выход наружу
     */
    constructor({ apiBaseUrl, headers, onEvent, hold }) {
        this.apiBaseUrl = apiBaseUrl;
        this.headers = headers;
        this.emit = onEvent || (() => {});
        // Выдержка против перебивания. Значения приходят с сервера в /tokens,
        // чтобы подбирать их переменной окружения, а не пересборкой фронта.
        this.hold = { short: 300, long: 1200, ...(hold || {}) };

        this.ctx = null;
        this.stream = null;
        this.node = null;
        this.stt = null;
        this.keepalive = null;

        this.sources = [];
        this.playAt = 0;
        this.speaking = false;
        // Частота приходит от сервера событием 'start': у провайдеров озвучки
        // она своя, и угадывать её на клиенте значит играть речь не с той
        // скоростью. 24 000 — только значение до первого события.
        this.rate = 24000;

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

        await this.openStt(tokens);
        this.startedAt = performance.now();

        const source = this.ctx.createMediaStreamSource(this.stream);
        this.node = new AudioWorkletNode(this.ctx, 'trainer-capture');
        this.node.port.onmessage = (event) => {
            if (this.stt && this.stt.readyState === WebSocket.OPEN) {
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
            socket.onclose = () => { clearInterval(this.keepalive); };
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
            if (text.trim()) voiced = true;
        }
        this.partial = fresh;

        if (voiced) {
            this.lastVoiceAt = performance.now();
            // Человек продолжил — отменяем отправку. Накопленное НЕ сбрасываем:
            // продолжение приклеится к нему само, потому что pending копится до
            // самой отправки.
            clearTimeout(this.holdTimer);
            this.holdTimer = null;
            if (this.speaking) {
                this.stopPlayback();
                this.emit('barge', {});
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
        const metrics = {
            stt_confidence: mean(this.confidences),
            stt_tokens: this.tokens,
            stt_langs: this.langs,
            stt_lang: Object.entries(this.langs).sort((a, b) => b[1] - a[1])[0]?.[0] || null,
            // Сколько распознавание думало после того, как человек замолчал…
            endpoint_delay_ms: this.endpointMs ?? null,
            // …и сколько ждали мы сами, чтобы не перебить.
            hold_ms: this.holdMs || null,
        };
        this.pending = '';
        this.partial = '';
        this.confidences = [];
        this.langs = {};
        this.tokens = 0;
        if (text) this.emit('utterance', { text, metrics, at: this.lastVoiceAt });
    }

    // ── воспроизведение ──────────────────────────────────────────────────────

    stopPlayback() {
        this.sources.forEach((source) => { try { source.stop(); } catch { /* уже остановлен */ } });
        this.sources = [];
        this.playAt = 0;
        this.speaking = false;
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
        source.connect(this.ctx.destination);
        // Небольшой запас перед первым куском: иначе на медленной сети следующий
        // не успевает приехать и речь рвётся щелчками.
        this.playAt = Math.max(this.ctx.currentTime + 0.08, this.playAt);
        source.start(this.playAt);
        this.playAt += buffer.duration;
        this.sources.push(source);
        source.onended = () => { this.sources = this.sources.filter((s) => s !== source); };
    }

    /**
     * Произносит текст: читает SSE от нашего сервера и играет куски по мере
     * прихода. Возвращает замеры, включая слышимую паузу.
     */
    async speak(text, { turnId = null, since = 0, sessionId = null } = {}) {
        this.speaking = true;
        this.playAt = 0;
        this.rate = 24000;
        let firstAudibleMs = null;
        let done = {};
        let chunks = 0;
        let failure = null;

        const response = await fetch(`${this.apiBaseUrl}/api/trainer/speak`, {
            method: 'POST',
            headers: { ...this.headers(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, turn_id: turnId, session_id: sessionId }),
        });
        if (!response.ok || !response.body) {
            this.speaking = false;
            throw new Error(`озвучка недоступна: HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let stopped = false;
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
                    if (!this.speaking) { stopped = true; break; }
                    if (firstAudibleMs === null && since) {
                        firstAudibleMs = Math.round(performance.now() - since);
                    }
                    chunks += 1;
                    this.playChunk(payload.b64, this.rate);
                } else if (payload.t === 'done') {
                    done = payload;
                } else if (payload.t === 'error') {
                    failure = payload.message;
                    this.emit('error', { where: 'tts', message: payload.message });
                }
            }
        }
        if (stopped) { try { await reader.cancel(); } catch { /* поток уже закрыт */ } }
        this.speaking = false;
        // Молчание — это отказ, а не успех. Сервер закрывал соединение с нулём
        // байт, раздел досылал 'done', и человек видел текст реплики при полной
        // тишине. Теперь тишина доходит наверх ошибкой с причиной.
        if (!chunks && !stopped) {
            throw new Error(failure || done.error || 'провайдер не дал ни одного куска звука');
        }
        return { ...done, voice_to_voice_ms: firstAudibleMs };
    }

    // ── остановка ────────────────────────────────────────────────────────────

    stop() {
        clearInterval(this.keepalive);
        clearTimeout(this.holdTimer);
        this.stopPlayback();
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
