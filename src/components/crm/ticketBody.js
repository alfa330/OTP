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
