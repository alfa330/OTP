/* Раздел «Тренажёр»: телефонный тракт поверх синтеза.
 *
 * Человек узнаёт запись не по голосу, а по КАНАЛУ. Настоящий звонок водителя в
 * поддержку — это 8000 Гц: замер по 220 боевым записям Oktell (194 000 кадров
 * с голосом) показал, что внутри телефонной полосы у них яркость
 * E(3400-4000)/E(300-3400) равна 0,055 % с разбросом 0,016-0,187 %. Наш синтез
 * отдаёт 24 кГц студийной чистоты, и та же величина у него 0,272-0,449 % — в
 * пять-восемь раз ярче настоящего звонка.
 *
 * (Прежняя формулировка «в 30-180 раз» была ошибкой методики: она сравнивала
 * всю энергию выше 3400 Гц у 8-килогерцовой записи, где выше 4 кГц физически
 * ничего нет, с 24-килогерцовым синтезом, где есть 4-12 кГц. Сравнивать надо
 * одну и ту же полосу.)
 *
 * Цепочка ниже возвращает яркость в измеренный коридор и НЕ ТРОГАЕТ
 * разборчивость. Проверено тем же способом, которым в разделе проверяют
 * казахский, — обратным прогоном через Soniox:
 *
 *     как есть          ru 0,449 %  kk 0,272 %   WER 0 %   языки ru 42/42, kk 49/49
 *     после тракта      ru 0,120 %  kk 0,067 %   WER 0 %   языки те же
 *
 * Всё это стоит ноль: ни запроса к провайдеру, ни миллисекунды задержки, ни
 * строчки на сервере. Считает звуковая подсистема браузера.
 *
 * Почему цепочка собирается ОДИН РАЗ на контекст, а не на кусок: у биквадов
 * есть внутреннее состояние, и новый фильтр на каждый кусок SSE рвал бы его на
 * стыках — речь пошла бы щелчками.
 */

/* Полоса телефона. Крутизна важнее номиналов: BiquadFilterNode — это фильтр
 * второго порядка, 12 дБ на октаву, и одного мало.
 *
 * Замер глубины (тот же обратный прогон, WER 0 % во всех вариантах):
 *     lowpass ×2  →  ru 0,80 %  kk 0,28 %   ещё слышен «воздух»
 *     lowpass ×4  →  ru 0,15 %  kk 0,04 %   попадает в боевой коридор
 *     lowpass ×6  →  ru 0,03 %  kk 0,01 %   глуше настоящего звонка
 * Берём ×4. */
export const PHONE = {
    highpass: { hz: 300, count: 2 },
    lowpass: { hz: 3400, count: 4 },
    q: 0.7071,
    // Полоса и компандирование поднимают пик-фактор, поэтому чуть убираем
    // громкость, а от клиппинга страхует не компрессор, а лимитер: замер
    // показал, что синтез УЖЕ сжатее настоящего звонка, и компрессия уводила бы
    // от цели, а не к ней.
    trim: 0.85,
    limiter: { threshold: -3, ratio: 20, knee: 0, attack: 0.003, release: 0.08 },
};

/* Кривая G.711 μ-law для WaveShaperNode: восемь бит на отсчёт, как в линии.
 * На яркость почти не влияет (0,13 % против 0,14 % в замере), но даёт ту самую
 * зернистость, по которой ухо и узнаёт телефон. */
const mulawCurve = (points = 4096, mu = 255) => {
    const curve = new Float32Array(points);
    for (let i = 0; i < points; i += 1) {
        const x = (i / (points - 1)) * 2 - 1;
        const sign = Math.sign(x);
        const encoded = Math.round(sign * (Math.log1p(mu * Math.abs(x)) / Math.log1p(mu)) * 127) / 127;
        curve[i] = Math.sign(encoded) * ((((1 + mu) ** Math.abs(encoded)) - 1) / mu);
    }
    return curve;
};

/**
 * Собирает тракт в графе браузера и возвращает вход, к которому подключают
 * источники звука.
 *
 * @param {AudioContext} ctx
 * @param {object} options  { mulaw = true }  — компандирование можно выключить,
 *        если на первых занятиях речь хочется оставить почище.
 * @returns {{input: AudioNode, dispose: function}}
 */
export const connectTelephone = (ctx, { mulaw = true } = {}) => {
    const nodes = [];
    const biquad = (type, hz) => {
        const node = ctx.createBiquadFilter();
        node.type = type;
        node.frequency.value = hz;
        node.Q.value = PHONE.q;
        nodes.push(node);
        return node;
    };

    const chain = [];
    for (let i = 0; i < PHONE.highpass.count; i += 1) chain.push(biquad('highpass', PHONE.highpass.hz));
    for (let i = 0; i < PHONE.lowpass.count; i += 1) chain.push(biquad('lowpass', PHONE.lowpass.hz));

    if (mulaw) {
        const shaper = ctx.createWaveShaper();
        shaper.curve = mulawCurve();
        shaper.oversample = 'none';     // компандирование работает по отсчётам
        nodes.push(shaper);
        chain.push(shaper);
    }

    const gain = ctx.createGain();
    gain.gain.value = PHONE.trim;
    nodes.push(gain);
    chain.push(gain);

    const limiter = ctx.createDynamicsCompressor();
    limiter.threshold.value = PHONE.limiter.threshold;
    limiter.ratio.value = PHONE.limiter.ratio;
    limiter.knee.value = PHONE.limiter.knee;
    limiter.attack.value = PHONE.limiter.attack;
    limiter.release.value = PHONE.limiter.release;
    nodes.push(limiter);
    chain.push(limiter);

    chain.forEach((node, index) => {
        if (index + 1 < chain.length) node.connect(chain[index + 1]);
    });
    chain[chain.length - 1].connect(ctx.destination);

    return {
        input: chain[0],
        dispose: () => nodes.forEach((node) => {
            try { node.disconnect(); } catch { /* контекст уже закрыт */ }
        }),
    };
};
