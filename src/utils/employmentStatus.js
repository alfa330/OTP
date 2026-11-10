/*
 * Подписи статусов по виду оформления (ТЗ HR «iCore 4», п. 5).
 *
 * Коды графика не новые: annual_leave, bs, sick_leave живут в графиках, заявках,
 * отчётах и ночной синхронизации. Меняется только подпись — по виду оформления
 * человека: у ТД «Трудовой отпуск» и «Отпуск без содержания», у ГПХ и СМЗ —
 * «Оплачиваемый отпуск» и «Приостановление договора». «Больничный» по ТЗ — у ТД.
 * Вид не указан — нейтральные подписи и все пункты.
 *
 * Зеркало hr_office/rules.py (status_label, status_options) — тест сверяет их.
 */

const LABELS = {
    of: { annual_leave: 'Трудовой отпуск', bs: 'Отпуск без содержания', sick_leave: 'Больничный' },
    gph: { annual_leave: 'Оплачиваемый отпуск', bs: 'Приостановление договора', sick_leave: 'Больничный' },
    smz: { annual_leave: 'Оплачиваемый отпуск', bs: 'Приостановление договора', sick_leave: 'Больничный' },
};
const NEUTRAL = { annual_leave: 'Отпуск', bs: 'Б/С', sick_leave: 'Больничный' };
const COMMON = { working: 'Работает', fired: 'Уволен', dismissal: 'Увольнение' };
const BY_TYPE = {
    of: ['annual_leave', 'bs', 'sick_leave'],
    gph: ['annual_leave', 'bs'],
    // ТЗ п. 8: у СМЗ из статусов — только «Приостановление договора».
    smz: ['bs'],
};
export const PERIOD_STATUSES = ['annual_leave', 'bs', 'sick_leave'];

export const cleanEmploymentType = (value) => {
    const text = String(value || '').trim().toLowerCase();
    return ['gph', 'of', 'smz'].includes(text) ? text : null;
};

export function employmentStatusLabel(code, employmentType) {
    const value = code === 'unpaid_leave' ? 'bs' : String(code || '').trim().toLowerCase();
    if (COMMON[value]) return COMMON[value];
    const kind = cleanEmploymentType(employmentType);
    if (kind && LABELS[kind][value]) return LABELS[kind][value];
    return NEUTRAL[value] || value;
}

/* Пункты выбора: «Работает» первым, дальше — что положено виду. Текущий статус в
   списке остаётся всегда (больничный у ГПХ, поставленный до ТЗ), иначе выбор
   показал бы «Работает» у человека на больничном. */
export function employmentStatusOptions(employmentType, current, { withFired = false } = {}) {
    const kind = cleanEmploymentType(employmentType);
    const codes = [...(BY_TYPE[kind] || PERIOD_STATUSES)];
    const value = current === 'unpaid_leave' ? 'bs' : current;
    if (PERIOD_STATUSES.includes(value) && !codes.includes(value)) codes.push(value);
    const options = [{ value: 'working', label: COMMON.working }];
    if (withFired) options.push({ value: 'fired', label: COMMON.fired });
    return options.concat(codes.map((code) => ({ value: code, label: employmentStatusLabel(code, kind) })));
}

/* Оформление по умолчанию (ТЗ HR «iCore 4», п. 3): «для ОП и СЗоВ — договор ГПХ на ИП Eazy Go
   Rent, для операторов TEZ — ИП GaBa». Вид и компанию выбирает кадровик в «Приеме», это —
   подстановка и подсказка в окне создания учётки. Зеркало hr_office/rules.py (DEFAULT_HIRING,
   DEFAULT_HIRING_FOR) — тест сверяет их. */
export const DEFAULT_HIRING_BY_DEPARTMENT = {
    szov: { type: 'ГПХ', company: 'ИП «Eazy Go Rent»', for: 'ОП и СЗоВ' },
    op: { type: 'ГПХ', company: 'ИП «Eazy Go Rent»', for: 'ОП и СЗоВ' },
    tez: { type: 'ГПХ', company: 'ИП «GaBa»', for: 'операторов TEZ' },
};

export function defaultHiringFor(departmentCode) {
    return DEFAULT_HIRING_BY_DEPARTMENT[String(departmentCode || '').trim().toLowerCase()] || null;
}

/* Статусы карточки до ТЗ «iCore 4» — для всех вне пилота «Кадров» (профиль hr_office_features):
   пока идёт проверка, остальным окно учётки показывает прежние пункты и подписи. */
export const LEGACY_STATUS_OPTIONS = [
    { value: 'working', label: 'Работает' },
    { value: 'fired', label: 'Уволен' },
    { value: 'bs', label: 'Б/С' },
    { value: 'sick_leave', label: 'Больничный' },
    { value: 'annual_leave', label: 'Ежегодный отпуск' },
];
