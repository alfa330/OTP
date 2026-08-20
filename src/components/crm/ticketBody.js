/* Готовый текст обращения → строки для карточки.
 *
 * Текст собирает сервер (crm/scenarios.py::render_body) и он же уходит в
 * Telegram: это ЗАПИСЬ о том, что увидели в группе, поэтому карточка показывает
 * ровно его, а не пересобирает из ответов заново. Разбор здесь — только про
 * оформление: одним серым полотном перечень «вопрос: ответ» не читается, глазу
 * негде зацепиться.
 *
 * Формат текста — договорённость с сервером и всего из двух правил:
 *   пустая строка   — граница смыслового блока;
 *   «подпись: ответ» — строка перечня, всё до первого «: » это подпись.
 * Строка без «: » (метка «возможный массовый сбой», строка контекста) остаётся
 * как есть — она и так читается.
 *
 * Живёт отдельным модулем, а не в разметке, потому что это ЛОГИКА разбора:
 * в JSX её никто бы не проверил (на этом раздел уже обжигался — см. wizardRules).
 */

// Длинная фраза с двоеточием внутри — это знак препинания, а не подпись.
// Порог тот же, что на сервере в crm/telegram.py::format_body: разойдутся —
// одно и то же сообщение будет выглядеть по-разному в группе и в карточке.
export const MAX_LABEL = 64;

export const splitBodyLine = (line) => {
    const text = String(line ?? '');
    const at = text.indexOf(': ');
    if (at <= 0 || at > MAX_LABEL) return { text };
    return { label: text.slice(0, at + 1), value: text.slice(at + 2) };
};

export const formatTicketBody = (body) => {
    const blocks = [];
    let current = [];
    for (const line of String(body ?? '').split('\n')) {
        if (!line.trim()) {
            if (current.length) blocks.push(current);
            current = [];
            continue;
        }
        current.push(splitBodyLine(line));
    }
    if (current.length) blocks.push(current);
    return blocks;
};

/* ─── Смысловые виды блоков ───────────────────────────────────────────────
 *
 * Блоки у render_body не равноценны, и рисовать их одинаково — как раз то, из
 * чего получалось «полотно текста». Сервер собирает их в строгом порядке
 * (crm/scenarios.py::render_body):
 *
 *     ⚠️ метка                        → предупреждение
 *     парк · город · период           → где и когда
 *     подпись: ответ                  → суть обращения
 *     ✅ Проверено / ❗ Не выполнено   → что оператор сделал руками
 *     ✔️ Чек-лист выполнен: 3 из 3
 *
 * Вид блока выводится из его содержимого, а не из номера по порядку: у
 * тематики может не быть ни метки, ни контекста, ни чек-листа, и «второй блок
 * это всегда контекст» развалилось бы на первой же такой.
 *
 * Разбор живёт здесь, а не в разметке, по той же причине, что и всё остальное
 * в этом файле: в JSX его никто не проверит.
 */

export const BLOCK_WARNING = 'warning';
export const BLOCK_CONTEXT = 'context';
export const BLOCK_CHECKS = 'checks';
export const BLOCK_LIST = 'list';

// Разделитель перечня внутри одной строки — тот же, что в scenarios.ITEM_SEP.
const ITEM_SEP = ' · ';

/* Маркеры хвостового блока. Тон — не украшение: «проверено» и «не выполнено»
 * это ровно противоположные сообщения, и одинаково серыми они читались бы как
 * один список. */
const MARKERS = [
    { mark: '✅', tone: 'green' },
    { mark: '❗', tone: 'red' },
    { mark: '✔️', tone: 'blue' },
    // Метка массового сбоя.
    { mark: '⚠️', tone: 'amber' },
];

const markerOf = (text) => MARKERS.find((m) => String(text || '').startsWith(m.mark)) || null;

/* Строка перечня → маркер + подпись + элементы.
 *
 * «✅ Проверено: перезашёл · сменил устройство» это не подпись со значением, а
 * заголовок со списком: элементы стоит показать метками, тогда их видно
 * поштучно. Строка без перечня («Чек-лист выполнен: 3 из 3») остаётся текстом —
 * дробить «3 из 3» по пробелам было бы вредительством.
 */
export const describeMarkedLine = (line) => {
    const text = String(line ?? '');
    const marker = markerOf(text);
    const rest = marker ? text.slice(marker.mark.length).trim() : text;
    const parsed = splitBodyLine(rest);
    const items = parsed.value && parsed.value.includes(ITEM_SEP)
        ? parsed.value.split(ITEM_SEP).map((part) => part.trim()).filter(Boolean)
        : null;
    return {
        tone: marker ? marker.tone : null,
        label: parsed.label ? parsed.label.replace(/:$/, '') : null,
        value: parsed.label ? parsed.value : rest,
        items,
    };
};

const isContextRow = (row) => !row.label && String(row.text || '').includes(ITEM_SEP);

/* Готовый текст → блоки с видом. Возвращает [{ kind, rows, chips }]:
 *   warning — rows: описанные маркером строки
 *   context — chips: парк / город / период поштучно
 *   checks  — rows: описанные маркером строки
 *   list    — rows: как отдавал formatTicketBody («подпись: ответ» или текст)
 */
export const describeBody = (body) => formatTicketBody(body).map((block) => {
    const marked = block.every((row) => markerOf(row.label || row.text));
    if (marked) {
        const warning = block.every((row) => String(row.label || row.text).startsWith('⚠️'));
        return {
            kind: warning ? BLOCK_WARNING : BLOCK_CHECKS,
            rows: block.map((row) => describeMarkedLine(
                row.label ? row.label + ' ' + row.value : row.text,
            )),
        };
    }
    if (block.length === 1 && isContextRow(block[0])) {
        return {
            kind: BLOCK_CONTEXT,
            chips: String(block[0].text).split(ITEM_SEP).map((part) => part.trim()).filter(Boolean),
        };
    }
    return { kind: BLOCK_LIST, rows: block };
});

/* Одна строка для сворачивания: чем обращение опознают, когда весь текст не
 * нужен. Контекст (парк · город · период) для этого и существует; если его нет,
 * берём первую строку перечня. */
export const bodyDigest = (body) => {
    const blocks = describeBody(body);
    const context = blocks.find((block) => block.kind === BLOCK_CONTEXT);
    if (context) return context.chips.join(ITEM_SEP);
    const list = blocks.find((block) => block.kind === BLOCK_LIST);
    if (!list) return '';
    const row = list.rows[0];
    return row.label ? `${row.label} ${row.value}` : String(row.text || '');
};
