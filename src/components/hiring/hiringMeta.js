/*
 * Подписи и форматирование раздела «Воронка найма» (#404).
 *
 * Время с сервера — наивное время Алматы («2026-10-30T14:00:00»), поэтому разбирается по
 * цифрам строки, а не через new Date(): в браузере с другим поясом Date сдвинул бы часы.
 * Коды этапов, подстатусов и причин — те же, что в hiring/rules.py; подписи приходят в
 * meta первого ответа, здесь — только то, что нужно до загрузки и для форматирования.
 */

export const STAGES = [
    'contacts', 'responses', 'in_work', 'invited_office', 'invited_training', 'refused', 'on_training',
    'on_line', 'dropped', 'hired',
];
export const STAGE_LABELS = {
    contacts: 'Контакты',
    responses: 'Отклики',
    in_work: 'В работе',
    invited_office: 'Приглашен в офис',
    invited_training: 'Приглашен на обучение',
    refused: 'Отказ',
    on_training: 'Вышел на обучение',
    on_line: 'Вышел на линию',
    dropped: 'Отсев',
    hired: 'Отработал 7 дней',
};
export const POOL_STAGES = ['contacts', 'responses'];
export const FINAL_STAGES = ['refused', 'dropped', 'hired'];
export const FUNNEL_STAGES = ['in_work', 'dialed', 'invited_office', 'invited_training', 'on_training', 'on_line', 'hired'];
export const FUNNEL_LABELS = { ...STAGE_LABELS, dialed: 'Дозвон' };

export const SUBSTAGE_LABELS = {
    dial: 'Дозвон', no_answer: 'Недозвон', whatsapp: 'Переписка в WhatsApp',
    expected: 'Ожидается', came: 'Пришел', no_show: 'Не пришел',
    ours: 'Наш отказ', candidate: 'Отказ кандидата', self: 'Сам', we: 'Мы',
};

/* Цвет только со смыслом: завершения (отказ, отсев — красный; найм закрыт — зелёный),
   просрочка — янтарный. Рабочие этапы не красятся. */
export const STAGE_TONES = { refused: 'red', dropped: 'red', hired: 'green' };
export const SUBSTAGE_TONES = { no_answer: 'amber', no_show: 'red', came: 'green', dial: 'green' };

export const TOUCH_LABELS = {
    call: 'Звонок', whatsapp: 'WhatsApp', comment: 'Комментарий', repeat_response: 'Повторный отклик', system: 'Система',
};
export const MOTIVATION_LABELS = {
    pending: 'Ожидает подтверждения', counted: 'Засчитано', paid: 'Выплачено', cancelled: 'Отменено',
};
export const MOTIVATION_TONES = { pending: 'amber', counted: 'green', paid: 'green', cancelled: 'red' };
export const GRANT_LABELS = { lead: 'Руководитель рекрутинга', staff: 'Штатный рекрутер', remote: 'Удалённый рекрутер' };
export const REASON_KIND_LABELS = {
    refusal_ours: 'Наш отказ', refusal_candidate: 'Отказ кандидата', drop_self: 'Отсев «сам»', drop_we: 'Отсев «мы»',
};
/* Инициатор перехода → вид причины (как rules.REASON_KIND_FOR). */
export const reasonKindFor = (stage, initiator) => ({
    'refused:ours': 'refusal_ours', 'refused:candidate': 'refusal_candidate',
    'dropped:self': 'drop_self', 'dropped:we': 'drop_we',
}[`${stage}:${initiator}`] || null);

export const ASSIGNMENT_LABELS = {
    take: 'Взял в работу', reassign: 'Переназначена', release: 'Возвращена в пул', auto_timeout: 'Вернулась в пул по сроку',
    reactivate: 'Повторная активация', recruiter_off: 'Рекрутер отключён',
};
export const INTEGRATION_LABELS = { hh: 'HeadHunter', olx: 'OLX', webhook: 'Вебхук площадок' };
export const INTEGRATION_STATE_LABELS = { ok: 'Работает', not_configured: 'Не настроена', error: 'Ошибка' };

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
    'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_NOM = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август',
    'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

const parts = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(String(iso || ''));
    if (!match) return null;
    const [, year, month, day, hour, minute] = match;
    return { year: +year, month: +month, day: +day, hour: hour || '00', minute: minute || '00', hasTime: Boolean(hour) };
};

/** «30.10 14:00» (без времени — «30.10»). */
export const shortTime = (iso) => {
    const p = parts(iso);
    if (!p) return '';
    const date = `${String(p.day).padStart(2, '0')}.${String(p.month).padStart(2, '0')}`;
    return p.hasTime ? `${date} ${p.hour}:${p.minute}` : date;
};

/** «30 октября, 14:00». */
export const longTime = (iso) => {
    const p = parts(iso);
    if (!p) return '';
    return `${p.day} ${MONTHS[p.month - 1]}${p.hasTime ? `, ${p.hour}:${p.minute}` : ''}`;
};

/** «30.10.2026». */
export const fullDate = (iso) => {
    const p = parts(iso);
    return p ? `${String(p.day).padStart(2, '0')}.${String(p.month).padStart(2, '0')}.${p.year}` : '';
};

/** «Октябрь 2026» по «2026-10». */
export const monthLabel = (month) => {
    const match = /^(\d{4})-(\d{2})/.exec(String(month || ''));
    return match ? `${MONTHS_NOM[+match[2] - 1]} ${match[1]}` : '';
};

/** «2026-10» текущего месяца браузера (месяц отчёта по умолчанию). */
export const currentMonth = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
};

/** Сдвиг месяца: «2026-10», −1 → «2026-09». */
export const shiftMonth = (month, delta) => {
    const match = /^(\d{4})-(\d{2})/.exec(String(month || ''));
    if (!match) return currentMonth();
    const total = +match[1] * 12 + (+match[2] - 1) + delta;
    return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}`;
};

/** Сегодня «2026-10-30» (локальная дата браузера). */
export const todayIso = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

/** Дата ISO + дни → ISO (без поясов браузера). */
export const addDays = (iso, days) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    if (!match) return '';
    const moment = new Date(Date.UTC(+match[1], +match[2] - 1, +match[3] + days));
    return moment.toISOString().slice(0, 10);
};

/** Дата и время из полей формы → «2026-10-30T14:00». */
export const joinDateTime = (date, time) => (date ? `${date}T${time || '00:00'}` : '');

/** Разобрать «2026-10-30T14:00:00» на поля формы. */
export const splitDateTime = (iso) => {
    const p = parts(iso);
    if (!p) return { date: '', time: '' };
    return { date: `${p.year}-${String(p.month).padStart(2, '0')}-${String(p.day).padStart(2, '0')}`, time: `${p.hour}:${p.minute}` };
};

/** Сколько дней прошло от момента до сегодня (по датам, без часов): «3 дн». */
export const daysSince = (iso, today = todayIso()) => {
    const a = parts(iso);
    const b = parts(today);
    if (!a || !b) return null;
    return Math.round((Date.UTC(b.year, b.month - 1, b.day) - Date.UTC(a.year, a.month - 1, a.day)) / 86400000);
};

/*
 * Звонок из карточки — в iCore Phone на этом же компьютере (владелец 30.10.2026: «заменить tel:
 * на наш icore-phone:call?number=»). Формат — docs/CLICK-TO-CALL.md телефона:
 * icore-phone:call?number=<2–15 цифр>, префикс не нужен — рекрутеры сидят на линиях Binotel.
 * Открывается скрытым iframe (как помощник icore-phone.js), а не переходом страницы: переход
 * поднял бы «Покинуть страницу?» у несохранённых форм. Вызывать прямо в обработчике клика.
 */
export const ICORE_PHONE_FRAME_ID = 'icore-phone-call-frame';

export const buildICorePhoneUrl = (phone) => {
    const digits = String(phone || '').replace(/\D/g, '');
    if (digits.length < 2 || digits.length > 15) return null;
    return `icore-phone:call?number=${digits}`;
};

export const openICorePhoneCall = (phone, doc = typeof document === 'undefined' ? null : document) => {
    const url = buildICorePhoneUrl(phone);
    if (!url) throw new Error('Номер для звонка не разобран');
    if (!doc) return url;
    let frame = doc.getElementById(ICORE_PHONE_FRAME_ID);
    if (!frame) {
        frame = doc.createElement('iframe');
        frame.id = ICORE_PHONE_FRAME_ID;
        frame.title = 'Звонок в iCore Phone';
        frame.tabIndex = -1;
        frame.setAttribute('aria-hidden', 'true');
        frame.style.display = 'none';
        doc.body.appendChild(frame);
    }
    frame.src = url;
    return url;
};

export const errorOf = (error, fallback) => error?.response?.data?.error || fallback;
export const codeOf = (error) => error?.response?.data?.code || '';

/** «+7 705 123 45 67» из «+77051234567». */
export const prettyPhone = (phone) => {
    const digits = String(phone || '').replace(/\D/g, '');
    if (digits.length !== 11) return String(phone || '');
    return `+7 ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7, 9)} ${digits.slice(9)}`;
};

/** Инициалы для круглого бейджа рекрутера. */
export const initials = (name) => String(name || '').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();

/** Конверсия для таблицы: «42,1 %» / «—». */
export const percent = (value) => (value === null || value === undefined ? '—' : `${String(value).replace('.', ',')} %`);

/** Склонение: 1 карточка, 2 карточки, 5 карточек. */
export const plural = (n, one, few, many) => {
    const abs = Math.abs(n) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
};

/** Отбор карточек по строке поиска на клиенте: имя, телефон, город, вакансия, номер. */
export const matchesQuery = (card, query) => {
    const text = String(query || '').trim().toLowerCase();
    if (!text) return true;
    const digits = text.replace(/\D/g, '');
    const hay = [card.full_name, card.city, card.vacancy_name, card.vacancy_text, card.source_name, card.recruiter_name]
        .map((v) => String(v || '').toLowerCase());
    if (hay.some((v) => v.includes(text))) return true;
    // Номер кандидата — точным совпадением цифр, телефон — вхождением от трёх цифр.
    if (digits && digits === text && String(card.candidate_no || '') === digits) return true;
    if (digits.length >= 3 && String(card.phone || '').replace(/\D/g, '').includes(digits)) return true;
    return false;
};
