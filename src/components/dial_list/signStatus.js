/*
 * Подписание документов водителем (Sapar) — подписи и цвета статусов для журнала,
 * карточки и аналитики раздела «Обзвон» (запрос владельца 29.09.2026).
 *
 * Двойник серверного dial_list/signing.py::SIGN_STATUS_LABELS: набор ключей и
 * подписи сверяет tests/test_dial_list_signing.py.
 *
 * С 19.11.2026 «на проверке у Яндекса» — это подписал: успешка засчитывается
 * сразу, строка в журнале и в аналитике — среди подписавших. Синий статус и доля
 * «На проверке у Яндекса» остались для строк, которые проверка ещё не обновила.
 *
 * Цвет — только со смыслом и один на весь раздел: подписал — зелёный, на
 * проверке у Яндекса — синий (водитель уже подписал, ждём Яндекс), не подписал —
 * жёлтый, срок истёк — красный (звонить поздно). Серые — «ничего не известно»:
 * документы не поступили, ещё не проверяли, нет ИИН. Четыре цветных прошли
 * проверку различимости при дальтонизме (validate_palette: ΔE ≥ 15), серые —
 * нейтральная подложка, их числа всегда подписаны в легенде.
 */

export const SIGN_STATUS = {
    signed: { label: 'Подписал', color: '#16a34a' },
    processing: { label: 'На проверке у Яндекса', color: '#2a78d6' },
    unsigned: { label: 'Не подписал', color: '#eda100' },
    not_formed: { label: 'Документы не сформированы', color: '#eda100' },
    expired: { label: 'Срок подписания истёк', color: '#e34948' },
    rejected: { label: 'Документы отклонены', color: '#e34948' },
    no_docs: { label: 'Документы не поступили', color: '#8e99ab' },
    not_checked: { label: 'Ещё не проверяли', color: '#c2cad6' },
    no_iin: { label: 'Нет ИИН', color: '#c2cad6' },
};

export const signMeta = (code) => SIGN_STATUS[code] || { label: code || '—', color: '#c2cad6' };

/* Фильтр журнала «Документы» — ключи совпадают с service.JOURNAL_SIGN_FILTERS. */
export const SIGN_FILTER_OPTIONS = [
    { value: '', label: 'Любые документы' },
    { value: 'success', label: 'Подписали — успешка' },
    { value: 'self', label: 'Подписали сами' },
    // Подписавшие, чьи документы ещё у Яндекса: успешка у них уже засчитана (19.11.2026).
    { value: 'processing', label: 'Подписали — ждут Яндекса' },
    { value: 'unsigned', label: 'Не подписали' },
    { value: 'expired', label: 'Срок подписания истёк' },
    { value: 'no_docs', label: 'Документы не поступили' },
    { value: 'not_checked', label: 'Ещё не проверяли' },
    { value: 'no_iin', label: 'Нет ИИН' },
];

/* Полоса «Документы» в аналитике: от лучшего к худшему, серые — в конце. Ключ
   totals на сервере — в поле `total`; `sign` — фильтр журнала для перехода. */
export const SIGN_BREAKDOWN = [
    { key: 'signed', total: 'signed', label: 'Подписали', sign: '' , stage: 'signed' },
    // Только строки, которые проверка ещё не обновила (у остальных «на проверке у Яндекса» — уже «Подписали»):
    // переход — на этап журнала, а не на фильтр «Подписали — ждут Яндекса», где и подписавшие.
    { key: 'processing', total: 'processing', label: 'На проверке у Яндекса', sign: 'processing', stage: 'sign_wait' },
    { key: 'unsigned', total: 'unsigned', label: 'Не подписали', sign: 'unsigned' },
    { key: 'expired', total: 'expired', label: 'Срок подписания истёк', sign: 'expired' },
    { key: 'no_docs', total: 'no_docs', label: 'Документы не поступили', sign: 'no_docs' },
    { key: 'not_checked', total: 'not_checked', label: 'Ещё не проверяли', sign: 'not_checked' },
    { key: 'no_iin', total: 'without_iin', label: 'Нет ИИН', sign: 'no_iin' },
];
