/*
 * Куда ведёт источник под ответом помощника. Чистая логика: её сверяет
 * tests/assistant_source_target.test.mjs — чип, который перестал вести в раздел
 * или стал неактивным, иначе заметили бы только на проде.
 *
 *   статья           slug — витрина вики на статье
 *   справочник       tab 'offices' | 'cities' — вкладка вики на записи
 *   «Списки Байги»   tab 'baiga' — раздел портала на неделе и водителе из ответа
 *   классификатор    slug статьи и tab 'classifier' — статья-калькулятор, уже
 *                    заполненная машиной, годом и городом из ответа
 */
export const BAIGA_TAB = 'baiga';
export const CLASSIFIER_TAB = 'classifier';

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

/** Заготовка калькулятора из источника «Классификатор авто» (wiki/classifier.py):
 *  машина лежит в ref_key как «марка|модель», год — в ref_id, id города из
 *  файла данных — в ref_city. null — источник не про машину (список марки,
 *  города и тарифы): тогда классификатор открывается как есть. */
export const classifierPrefillOf = (source) => {
    if (!source || source.available === false || source.tab !== CLASSIFIER_TAB) return null;
    const key = String(source.ref_key || '');
    const cut = key.indexOf('|');
    if (cut <= 0 || cut === key.length - 1) return null;
    return {
        brand: key.slice(0, cut),
        model: key.slice(cut + 1),
        year: source.ref_id ? String(source.ref_id) : '',
        cityId: String(source.ref_city || ''),
    };
};

/* «Отправить супервайзеру» — пока вопрос не передан и только под ответом вики.
   Ответ, собранный по разделу за своим доступом («Списки Байги»: ФИО, номер ВУ и
   доход водителя), в очередь супервайзера не уходит — сервер его не примет
   (wiki/questions.py), и кнопки под ним нет. */
export const canEscalateMessage = (message, kinds) => !message?.escalation
    && !message?.gated_by
    && kinds.includes(message?.kind);
