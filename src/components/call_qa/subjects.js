/* Субъекты оценки на фронте — одно место вместо литералов в каждом файле.
 *
 * Зеркало call_qa/config.py (SUBJECT_*, CHAT_SUBJECT_BY_DEPARTMENT). Раньше
 * строка 'wz_episode' была вписана в четырёх компонентах, и с появлением
 * переписки СЗоВ и Тез КЦ каждая из них по-своему решала бы, чат перед ней или
 * звонок: карточка чата открывалась бы как звонок, а «Случайный чат» тянул бы
 * чужой источник. Здесь же лежит и подпись источника — она нужна там, где в
 * одном списке встречаются переписки из трёх систем.
 */

export const SUBJECT_CALL = 'call';
export const SUBJECT_IMPORTED_CALL = 'imported_call';
export const SUBJECT_WZ_EPISODE = 'wz_episode';
export const SUBJECT_C2D_SNAPSHOT = 'c2d_snapshot';
export const SUBJECT_CA_EPISODE = 'ca_episode';

export const CHAT_SUBJECTS = [SUBJECT_WZ_EPISODE, SUBJECT_C2D_SNAPSHOT, SUBJECT_CA_EPISODE];
export const CALL_SUBJECTS = [SUBJECT_CALL, SUBJECT_IMPORTED_CALL];

/* Семейство субъектов — то, что спрашивает ВКЛАДКА, а не одна таблица. Вкладка
 * «Оценки» показывает телефонию отдела, но «звонок отдела» — это сразу два вида:
 * у ОП оценивают строки журнала (`call`), у СЗоВ и Тез КЦ — подтянутые из АТС
 * записи без оценки в журнале (`imported_call`), и в одном отделе встречаются
 * оба. Пока вкладка просила один вид `call`, у СЗоВ и Тез КЦ она была пуста при
 * живых оценках в базе — это и выглядело как «оценки не сохраняются».
 * Зеркало call_qa/config.py SUBJECT_FAMILIES. */
export const SUBJECT_FAMILY_CALLS = 'calls';
export const SUBJECT_FAMILY_CHATS = 'chats';

/** Источник переписки у отдела. Пусто — у отдела нет чатов в разделе. */
export const CHAT_SUBJECT_BY_DEPARTMENT = {
    op: SUBJECT_WZ_EPISODE,
    szov: SUBJECT_C2D_SNAPSHOT,
    tez: SUBJECT_CA_EPISODE,
};

export const chatSubjectOf = (department) => (
    CHAT_SUBJECT_BY_DEPARTMENT[String(department || '').toLowerCase()] || SUBJECT_WZ_EPISODE
);

export const isChat = (subject) => (
    CHAT_SUBJECTS.includes(subject) || subject === SUBJECT_FAMILY_CHATS
);

/** Единица оценки словами — она разная, и в подписях это видно. */
export const subjectUnit = (subject) => {
    if (subject === SUBJECT_C2D_SNAPSHOT) return 'заявка';
    if (isChat(subject)) return 'чат';
    return 'звонок';
};

export const subjectTitle = (subject, id) => (
    subject === SUBJECT_C2D_SNAPSHOT ? `Заявка #${id}`
        : isChat(subject) ? `Чат #${id}` : `Звонок #${id}`
);

/** Откуда взялся субъект — показываем там, где отделы могут смешаться. */
export const SOURCE_LABEL = {
    [SUBJECT_CALL]: 'Журнал оценок',
    [SUBJECT_IMPORTED_CALL]: 'Из АТС, без оценки в журнале',
    [SUBJECT_WZ_EPISODE]: 'Wazzup',
    [SUBJECT_C2D_SNAPSHOT]: 'Chat2Desk',
    [SUBJECT_CA_EPISODE]: 'ChatApp',
};

/** Отделы, куда звонок можно подтянуть прямо из АТС (у ОП записи грузят руками). */
export const PULL_CALL_DEPARTMENTS = ['szov', 'tez'];
export const canPullCalls = (department) => (
    PULL_CALL_DEPARTMENTS.includes(String(department || '').toLowerCase())
);
