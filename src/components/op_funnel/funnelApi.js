/* Запросы раздела «Воронка ОП» к `/api/op_funnel/*`.
 *
 * Один модуль на все ручки — чтобы заголовки, таймауты и разбор ошибок были в
 * одном месте, а не расползались по восьми экранам раздела.
 *
 * Главное, ради чего модуль существует, — защита от гонки ответов. Пользователь
 * щёлкает направления и пресеты периода быстрее, чем отвечает сервер: «Поток»
 * за месяц считается заметно дольше «Яндекс Регистрации» за день, и без защиты
 * на экране «Яндекса» оказывались цифры «Потока». Поэтому у каждого канала
 * чтения свой счётчик, и ответ на устаревший запрос МОЛЧА выбрасывается
 * (приём взят из src/components/salary/TezLeadsPanel.jsx).
 *
 * Устаревшая ошибка тоже выбрасывается молча: отменённый запрос не должен
 * выплёвывать тост «не удалось загрузить» поверх уже показанных свежих данных.
 *
 * Договор с вызывающим кодом:
 *   • успех         → промис с телом ответа;
 *   • устаревание   → промис со значением `null` (проверять `if (!data) return`);
 *   • ошибка живого запроса → промис отклоняется `Error` с русским текстом,
 *     полями `code` (машинный код сервера) и `status`.
 *
 * Правки (sync, сопоставление, нормы, загрузка файла) счётчиком НЕ прикрыты:
 * результат действия человека обязан дойти до него всегда, даже если экран уже
 * переключили.
 */

import axios from 'axios';

/* Чтение идёт по своей базе и укладывается в секунды; потолок нужен только
   чтобы зависший запрос не держал спиннер вечно. */
const READ_TIMEOUT_MS = 60000;

/* `/sync` ходит в СРМ за десятками тысяч лидов — это минуты, а не секунды.
   Потолок waitress 120 с относится к ответу, но выгрузка за длинный период
   идёт партиями, поэтому клиентский запас больше. */
const SYNC_TIMEOUT_MS = 300000;

/* Книга собирается из своей базы, но лист лидов у «Потока» за месяц — это сотни
   тысяч строк, и сборка занимает десятки секунд. */
const EXPORT_TIMEOUT_MS = 180000;

const IMPORT_TIMEOUT_MS = 120000;

/* Каналы чтения: у каждого свой счётчик. Отдельные счётчики, а не один общий,
   потому что сводка, таблица операторов и дни грузятся параллельно — общий
   счётчик гасил бы ответы соседей. */
const READ_CHANNELS = [
    'meta', 'overview', 'operators', 'days', 'reasons', 'leads',
    'mapping', 'reasonDict', 'targets', 'runs',
];

const DIRECTION_TITLES = {
    op_osnova: 'Основа',
    op_potok: 'Поток',
    op_yandex_reg: 'Яндекс Регистрация',
    op_verificator: 'Верификатор',
};

/** Пустые значения в query не отправляем: Flask отличает «параметра нет» от
 *  «параметр пустой», и пустая строка в `bucket` обнуляла бы выборку. */
function clean(params) {
    const out = {};
    Object.entries(params || {}).forEach(([key, value]) => {
        if (value === undefined || value === null || value === '') return;
        out[key] = value;
    });
    return out;
}

/** Ошибка axios → `Error` с русским текстом. Текст сервера важнее нашего: он
 *  объясняет причину («Это направление вам не открыто»), а наш — только место. */
function asFunnelError(error, fallback) {
    const data = error?.response?.data;
    const message = (data && typeof data === 'object' && data.error) || fallback;
    const wrapped = new Error(message || fallback);
    wrapped.code = (data && typeof data === 'object' && data.code) || '';
    wrapped.status = error?.response?.status || 0;
    // Тело ответа кладём целиком: у `/sync` в нём счётчики прогона, и панель
    // показывает, сколько успело записаться до отказа.
    wrapped.payload = data && typeof data === 'object' ? data : null;
    return wrapped;
}

/** То же, но для запросов с `responseType: 'blob'`: сервер прислал JSON, а мы
 *  просили blob — текст ошибки лежит внутри него и без разворачивания теряется. */
async function asBlobError(error, fallback) {
    const blob = error?.response?.data;
    if (blob && typeof blob.text === 'function') {
        try {
            const parsed = JSON.parse((await blob.text()) || '{}');
            const wrapped = new Error(parsed.error || fallback);
            wrapped.code = parsed.code || '';
            wrapped.status = error?.response?.status || 0;
            wrapped.payload = parsed;
            return wrapped;
        } catch (_) {
            /* не JSON — останется общая фраза ниже */
        }
    }
    return asFunnelError(error, fallback);
}

/** Имя файла из заголовка ответа.
 *
 *  Может не прийти вовсе: `Content-Disposition` виден кросс-доменному коду
 *  только если сервер перечислил его в `Access-Control-Expose-Headers`. Поэтому
 *  у выгрузки всегда есть запасное имя — иначе файл лёг бы в «Загрузки» как
 *  `blob` без расширения.
 */
function filenameFromHeaders(headers) {
    const raw = headers?.['content-disposition'] || headers?.['Content-Disposition'] || '';
    const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(raw);
    if (utf8) {
        try {
            return decodeURIComponent(utf8[1].trim());
        } catch (_) {
            /* битая кодировка — пойдём в запасное имя */
        }
    }
    const plain = /filename="?([^";]+)"?/i.exec(raw);
    return plain ? plain[1].trim() : '';
}

/** Запасное имя книги — ровно как его строит `op_funnel/report.report_filename`:
 *  по выбранному периоду, а не по дате сборки, иначе две разные выгрузки за один
 *  день назывались бы одинаково. */
function fallbackExportName(direction, from, to) {
    const title = DIRECTION_TITLES[direction] || direction || '';
    const ru = (value) => {
        const parts = String(value || '').slice(0, 10).split('-');
        return parts.length === 3 && parts[0] ? `${parts[2]}.${parts[1]}.${parts[0]}` : '';
    };
    const head = title ? `Воронка ОП — ${title}` : 'Воронка ОП';
    const left = ru(from);
    const right = ru(to);
    if (!left && !right) return `${head}.xlsx`;
    if (left === right) return `${head} ${left}.xlsx`;
    return `${head} ${left} — ${right}.xlsx`;
}

function saveBlob(blob, filename) {
    if (typeof document === 'undefined' || typeof URL === 'undefined') return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

/**
 * @param {object} options
 * @param {string} options.apiBaseUrl            база портала (без `/api`)
 * @param {function} options.withAccessTokenHeader  заголовки авторизации портала
 */
export function createFunnelApi({ apiBaseUrl, withAccessTokenHeader } = {}) {
    const base = `${apiBaseUrl || ''}/api/op_funnel`;

    /* Счётчики живут в замыкании, а не в модуле: два экземпляра раздела (раздел
       и его полноэкранный режим) не должны гасить запросы друг друга. */
    const counters = new Map(READ_CHANNELS.map((name) => [name, 0]));

    const authHeaders = () => (
        typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader() : {}
    );

    const nextId = (channel) => {
        const id = (counters.get(channel) || 0) + 1;
        counters.set(channel, id);
        return id;
    };
    const isCurrent = (channel, id) => counters.get(channel) === id;

    const read = (channel, path, params, fallback, extra = {}) => {
        const id = nextId(channel);
        return axios
            .get(`${base}${path}`, {
                headers: authHeaders(),
                params: clean(params),
                timeout: READ_TIMEOUT_MS,
                ...extra,
            })
            .then((response) => (isCurrent(channel, id) ? (response?.data ?? null) : null))
            .catch((error) => {
                if (!isCurrent(channel, id)) return null;
                throw asFunnelError(error, fallback);
            });
    };

    const write = (path, payload, fallback, extra = {}) => axios
        .post(`${base}${path}`, payload, {
            headers: authHeaders(),
            timeout: READ_TIMEOUT_MS,
            ...extra,
        })
        .then((response) => response?.data ?? null)
        .catch((error) => {
            throw asFunnelError(error, fallback);
        });

    return {
        base,

        /** Сбросить каналы чтения: вызывать при смене направления или периода в
         *  том же обновлении состояния, что и сам сброс страницы. Ответы всех
         *  запросов, ушедших до сброса, будут выброшены. */
        reset(...channels) {
            const names = channels.length ? channels : READ_CHANNELS;
            names.forEach((name) => counters.set(name, (counters.get(name) || 0) + 1));
        },

        // ── чтение ───────────────────────────────────────────────────────────

        meta() {
            return read('meta', '/meta', {}, 'Не удалось загрузить настройки раздела');
        },

        overview({ direction, from, to }) {
            return read('overview', '/overview', { direction, from, to },
                'Не удалось загрузить сводку по направлению');
        },

        operators({ direction, from, to }) {
            return read('operators', '/operators', { direction, from, to },
                'Не удалось загрузить таблицу операторов');
        },

        days({ direction, from, to }) {
            return read('days', '/days', { direction, from, to },
                'Не удалось загрузить разбивку по дням');
        },

        reasons({ direction, from, to, bucket, userId }) {
            return read('reasons', '/reasons',
                { direction, from, to, bucket, user_id: userId },
                'Не удалось загрузить разбивку по причинам');
        },

        /** Страница лидов за причиной. Пагинация серверная: у «Потока» за месяц
         *  их около ста тысяч, и тянуть всё ради пятидесяти строк нельзя. */
        leads({
            direction, from, to, bucket, reasonCode, userId, workDay,
            reachOutcome, dialogOutcome, streamType, limit = 50, offset = 0,
        }) {
            return read('leads', '/leads', {
                direction, from, to, bucket,
                reason_code: reasonCode,
                user_id: userId,
                work_day: workDay,
                reach_outcome: reachOutcome,
                dialog_outcome: dialogOutcome,
                stream_type: streamType,
                limit, offset,
            }, 'Не удалось загрузить список лидов');
        },

        mapping() {
            return read('mapping', '/mapping', {},
                'Не удалось загрузить сопоставление операторов');
        },

        reasonDict({ source } = {}) {
            return read('reasonDict', '/reasons/dict', { source },
                'Не удалось загрузить справочник причин');
        },

        targets({ direction }) {
            return read('targets', '/targets', { direction },
                'Не удалось загрузить нормы направления');
        },

        runs({ direction }) {
            return read('runs', '/runs', { direction },
                'Не удалось загрузить журнал прогонов');
        },

        // ── действия ─────────────────────────────────────────────────────────

        /** Выгрузка за период. `force` перечитывает уже зафиксированные сутки —
         *  сервер разрешит это только руководителю отдела. */
        sync({ direction, from, to, force = false }) {
            return write('/sync', { direction, from, to, force: Boolean(force) },
                'Не удалось обновить данные', { timeout: SYNC_TIMEOUT_MS });
        },

        saveMapping({ source, externalKey, userId, isIgnored = false }) {
            return write('/mapping', {
                source,
                external_key: externalKey,
                user_id: userId ?? null,
                is_ignored: Boolean(isIgnored),
            }, 'Не удалось сохранить сопоставление');
        },

        saveReason({ source, reasonCode, bucket, title, isHidden }) {
            return write('/reasons/dict', {
                source,
                reason_code: reasonCode,
                bucket,
                title,
                is_hidden: isHidden,
            }, 'Не удалось сохранить причину');
        },

        saveTarget({ direction, metric, value, shiftKind = 'any', effectiveFrom }) {
            return write('/targets', {
                direction,
                metric,
                value,
                shift_kind: shiftKind,
                effective_from: effectiveFrom,
            }, 'Не удалось сохранить норму');
        },

        /** Загрузка выгрузки СРМ (обращения Верификаторов).
         *
         *  `Content-Type` не задаём руками: его обязан выставить axios вместе с
         *  границей multipart, иначе Flask не разберёт поле `file`. */
        manualImport({ direction, file }) {
            const form = new FormData();
            form.append('direction', direction);
            form.append('file', file);
            return write('/manual/import', form, 'Не удалось загрузить файл',
                { timeout: IMPORT_TIMEOUT_MS });
        },

        /** Книга xlsx за период: скачивает файл и возвращает его имя. */
        async exportFile({ direction, from, to }) {
            let response;
            try {
                response = await axios.get(`${base}/export`, {
                    headers: authHeaders(),
                    params: clean({ direction, from, to }),
                    responseType: 'blob',
                    timeout: EXPORT_TIMEOUT_MS,
                });
            } catch (error) {
                throw await asBlobError(error, 'Не удалось собрать выгрузку');
            }
            const filename = filenameFromHeaders(response?.headers)
                || fallbackExportName(direction, from, to);
            saveBlob(response.data, filename);
            return { filename };
        },
    };
}

export default createFunnelApi;
