/* Раздел «Тренажёр»: часы речи и правило «когда собеседника можно перебить».
 *
 * Здесь только чистые функции — ни AudioContext, ни сокетов, ни состояния.
 * Ровно поэтому их можно проверить без браузера, а это единственный способ
 * закрепить поведение, которое иначе живёт в голове.
 *
 * ЗАЧЕМ ЭТОТ ФАЙЛ ПОЯВИЛСЯ. До 22.08.2026 перебиванием считался ЛЮБОЙ непустой
 * токен распознавания (voice.js: `if (text.trim()) voiced = true`). На проде
 * это убило пятую часть реплик собеседника: из 45 реплик ИИ в настоящих
 * браузерных разговорах 9 не прозвучали НИ ОДНИМ БАЙТОМ. Механизм виден в самой
 * ленте разговора — сессия 24:
 *
 *     15:08:51  стажёр   Сәлеметсіз бе. Здравствуйте, меня зовут Руслан.
 *     15:08:53  водитель  ← сгенерирована, замеров озвучки нет вовсе
 *     15:08:53  << перебивание >>
 *     15:08:54  стажёр   Помочь?
 *
 * «Помочь?» — это хвост фразы САМОГО стажёра («…чем могу вам помочь?»), который
 * Soniox дослал отдельным сообщением уже после отправки реплики. Отставание
 * события перебивания от реплики по всем десяти прод-случаям: 176-2461 мс, а
 * первый звук физически не может появиться раньше ~700 мс (круг до Render плюс
 * задержка синтеза 470-974 мс). То есть девять перебиваний из десяти случились
 * ДО ТОГО, как прозвучал хоть один сэмпл.
 *
 * Отсюда главное правило: пока не сыграно ни одного сэмпла, перебивать нечего.
 */

/* Поддакивание. Список не сочинён: это верхушка частот в 6529 репликах живых
 * водителей, где 35,5 % реплик — одно-два слова, и почти все они здесь.
 * Такое слово посреди речи собеседника значит «я слушаю», а не «замолчи». */
export const BACKCHANNEL = new Set([
    'ага', 'угу', 'мгм', 'мхм', 'ммм', 'аха', 'да', 'дада', 'ok', 'окей', 'ок',
    'так', 'ясно', 'понял', 'поняла', 'понятно', 'хорошо', 'ладно', 'угу-угу',
    'иә', 'ия', 'жақсы', 'жарайды', 'түсінікті', 'түсіндім', 'жақсы-жақсы',
]);

/* Слова, которые перебивают ОДНИМ словом, мимо порога длины. Без них гейт
 * глушил бы ровно то, ради чего перебивание и существует. */
export const BREAK_WORDS = new Set([
    'стоп', 'стойте', 'стой', 'подождите', 'подожди', 'погодите', 'погоди',
    'нет', 'алло', 'секунду', 'минуту',
    'тоқта', 'тоқтаңыз', 'жоқ', 'ау', 'күте', 'күтіңіз',
]);

const WORDS = /[\p{L}\d]+/gu;
const wordsOf = (text) => String(text || '').toLowerCase().match(WORDS) || [];

/* Вес одного знака в «буквенном эквиваленте». Буквы — единица, цифра дороже
 * (её произносят словом), знаки препинания несут паузу, а не звук.
 *
 * Считаем по БУКВАМ, а не по слогам: слоговой счёт пришлось бы вести по
 * гласным, а казахские ә, і, ө, ұ, ү в такой список попадают не всегда — и
 * тогда целое казахское слово весит ноль. Буква одинакова во всех трёх
 * алфавитах, которые здесь встречаются. */
const CHAR_WEIGHT = (char) => {
    if (/\d/.test(char)) return 3.5;              // «5» произносится как «пять»
    if (/[.!?…]/.test(char)) return 6.5;          // пауза в конце фразы, ≈0,5 с
    if (/[,;:—–-]/.test(char)) return 3;          // пауза внутри фразы, ≈0,25 с
    if (/\p{L}/u.test(char)) return 1;
    return 0.25;                                  // пробелы, кавычки, скобки
};

/**
 * Вес текста по знакам: сколько «звучания» приходится на каждый префикс.
 *
 * Возвращает { total, upto: Float64Array }, где upto[i] — вес первых i знаков.
 * Нужен, чтобы перевести «прозвучало 1180 мс из 8480» в позицию в тексте:
 * пословных меток синтез не отдаёт ни у Vertex, ни у Live API.
 */
export const weighText = (text) => {
    const value = String(text || '');
    const upto = new Float64Array(value.length + 1);
    for (let i = 0; i < value.length; i += 1) {
        upto[i + 1] = upto[i] + CHAR_WEIGHT(value[i]);
    }
    return { total: upto[value.length], upto };
};

/**
 * Сколько ЗНАКОВ реплики прозвучало, если сыграно playedMs из totalMs.
 *
 * Два правила, и оба про то, чтобы не соврать в бо́льшую сторону:
 *   — режем всегда по границе слова: половина слова на экране выглядит как
 *     ошибка, а в истории для модели превращается в новое слово;
 *   — округляем ВНИЗ. Обе систематические ошибки замера (задержка вывода звука
 *     и хвостовая тишина в длительности синтеза) и так смещают оценку вниз,
 *     так что худший исход — собеседник повторит одно слово, а не проглотит.
 */
export const spokenChars = (text, playedMs, totalMs) => {
    const value = String(text || '');
    if (!value) return 0;
    if (!(totalMs > 0) || !(playedMs > 0)) return 0;
    if (playedMs >= totalMs) return value.length;

    const { total, upto } = weighText(value);
    const target = total * (playedMs / totalMs);
    let index = 0;
    while (index < value.length && upto[index + 1] <= target) index += 1;
    if (index >= value.length) return value.length;

    // Назад до конца последнего ЦЕЛОГО слова.
    let cut = index;
    while (cut > 0 && /[\p{L}\d]/u.test(value[cut - 1]) && /[\p{L}\d]/u.test(value[cut] || '')) {
        cut -= 1;
    }
    while (cut > 0 && /\s/.test(value[cut - 1])) cut -= 1;
    return cut;
};

/**
 * Перебивание это или нет.
 *
 * @param {Array} tokens  токены Soniox с последнего сообщения:
 *                        { text, is_final, confidence, end_ms }
 * @param {object} opts
 *   audibleFromMicMs — момент по часам микрофона, когда пошёл ПЕРВЫЙ сэмпл
 *                      речи собеседника; null — ещё не звучало ничего;
 *   atMs             — те же часы «сейчас» (запасной вариант, если у токена
 *                      нет end_ms: у realtime-протокола метки есть не всегда);
 *   saying           — текст, который собеседник произносит прямо сейчас;
 *   saidChars        — сколько его знаков уже прозвучало (окно для эха);
 *   enabled, graceMs, minWords, minMs, minConfidence, echoRatio — пороги,
 *   приходят с сервера в /tokens.
 *
 * @returns {{barge: boolean, rule: string, words: string[], voiceMs: number}}
 *   rule: off | quiet | tail | noise | backchannel | echo | stop | speech
 */
export const bargeVerdict = (tokens, opts = {}) => {
    const {
        audibleFromMicMs = null, atMs = 0, saying = '', saidChars = 0,
        enabled = true, graceMs = 250, minWords = 2, minMs = 350,
        minConfidence = 0.7, echoRatio = 0.6,
    } = opts;

    const voiced = (tokens || []).filter((t) => (t.text || '').trim()
        && t.text !== '<end>' && t.text !== '<fin>');
    if (!voiced.length) return { barge: false, rule: 'quiet', words: [], voiceMs: 0 };

    // Аварийный выключатель: поведение до 22.08.2026, любой звук перебивает.
    if (!enabled) {
        return { barge: true, rule: 'off', words: wordsOf(voiced.map((t) => t.text).join('')),
                 voiceMs: 0 };
    }

    // 1. МОЛЧАНИЕ. Собеседник ещё не издал ни звука — перебивать нечего, а
    //    речь человека это просто продолжение его собственной реплики.
    //    Одно это правило закрывает девять прод-случаев из десяти.
    if (audibleFromMicMs === null) {
        return { barge: false, rule: 'quiet', words: [], voiceMs: 0 };
    }

    // 2. ХВОСТ. Токен, договорённый ДО первого звука, реакцией на собеседника
    //    быть не может по определению. Сравниваем два числа на одной шкале, а
    //    не гадаем по порогу. Ровно так выглядел прод-случай «Помочь?».
    const edge = audibleFromMicMs + graceMs;
    const fresh = voiced.filter((t) => (t.end_ms ?? atMs) > edge);
    if (!fresh.length) {
        return { barge: false, rule: 'tail', words: wordsOf(voiced.map((t) => t.text).join('')),
                 voiceMs: 0 };
    }

    // 3. ШУМ. Черновой токен с низкой уверенностью в расчёт не берём. Ждать
    //    is_final нельзя — это до секунды задержки на настоящем перебивании.
    const solid = fresh.filter((t) => t.is_final
        || typeof t.confidence !== 'number' || t.confidence >= minConfidence);
    if (!solid.length) {
        return { barge: false, rule: 'noise', words: [], voiceMs: 0 };
    }

    const text = solid.map((t) => t.text).join('');
    const words = wordsOf(text);
    const starts = solid.map((t) => t.start_ms).filter((v) => typeof v === 'number');
    const ends = solid.map((t) => t.end_ms).filter((v) => typeof v === 'number');
    const voiceMs = (starts.length && ends.length)
        ? Math.max(0, Math.max(...ends) - Math.min(...starts)) : 0;

    // 4. КОМАНДА. «Стоп», «тоқта», «алло» — одно слово, но именно оно и есть
    //    настоящее перебивание. Проверяем ДО порога длины.
    if (words.some((w) => BREAK_WORDS.has(w))) {
        return { barge: true, rule: 'stop', words, voiceMs };
    }

    // 5. ПОДДАКИВАНИЕ. «Ага», «иә», «мхм» значат «я слушаю».
    if (words.length && words.length <= 2 && words.every((w) => BACKCHANNEL.has(w))) {
        return { barge: false, rule: 'backchannel', words, voiceMs };
    }

    // 6. ЭХО. Микрофон и колонки в одной комнате; подавление эха у браузера
    //    работает не всегда, а распознавание честно расшифрует собственный
    //    голос собеседника. Сверяем с тем, что он произносит ПРЯМО СЕЙЧАС.
    if (words.length && saying) {
        const window = wordsOf(String(saying).slice(Math.max(0, saidChars - 120),
                                                    saidChars + 60));
        const hits = words.filter((w) => window.includes(w)).length;
        if (hits / words.length >= echoRatio) {
            return { barge: false, rule: 'echo', words, voiceMs };
        }
    }

    // 7. ВЕС. Настоящее перебивание — либо несколько слов, либо заметная
    //    длительность. Намеренно ИЛИ: по-русски и по-казахски живое
    //    перебивание часто укладывается в одно слово.
    if (words.length >= minWords || voiceMs >= minMs) {
        return { barge: true, rule: 'speech', words, voiceMs };
    }
    return { barge: false, rule: 'noise', words, voiceMs };
};
