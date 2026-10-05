/*
 * Куда ведёт источник под ответом помощника. Чистая логика: её сверяет
 * tests/assistant_source_target.test.mjs — чип, который перестал вести в раздел
 * или стал неактивным, иначе заметили бы только на проде.
 *
 *   статья           slug — витрина вики на статье
 *   справочник       tab 'offices' | 'cities' — вкладка вики на записи
 *   «Списки Байги»   tab 'baiga' — раздел портала на неделе и водителе из ответа
 */
export const BAIGA_TAB = 'baiga';

/** Чип нажимается, пока источник открыт человеку и ему есть куда вести. */
export const sourceDisabled = (source) => source?.available === false || !(source?.slug || source?.tab);

/** Дверь источника: 'article' | 'baiga' | 'directory' | null. */
export const sourceDoor = (source) => {
    if (!source || source.available === false) return null;
    if (source.slug) return 'article';
    if (source.tab === BAIGA_TAB) return 'baiga';
    return source.tab ? 'directory' : null;
};

/** Просьба разделу «Списки Байги»: неделя (id загрузки) и водитель (номер ВУ). */
export const baigaFocusOf = (source) => ({
    weekId: source?.ref_id ?? null,
    query: String(source?.ref_key || ''),
});

/* «Отправить супервайзеру» — пока вопрос не передан и только под ответом вики.
   Ответ, собранный по разделу за своим доступом («Списки Байги»: ФИО, номер ВУ и
   доход водителя), в очередь супервайзера не уходит — сервер его не примет
   (wiki/questions.py), и кнопки под ним нет. */
export const canEscalateMessage = (message, kinds) => !message?.escalation
    && !message?.gated_by
    && kinds.includes(message?.kind);
