/* Подписи и форматирование журнала раздела «Чаты водителей» (задача #271).
 *
 * Двойник на бэкенде — driver_chats/report.py: те же коды, те же слова. Второй
 * словарь неизбежен (питон не читает js), поэтому его сторожит тест: разойдись
 * подписи, человек увидел бы в выгрузке не то слово, что на экране.
 *
 * ЧЕСТНОСТЬ ФОРМУЛИРОВОК. «Открыл переписку», а не «Сделал скриншот»: система
 * видит факт открытия чата, а не нажатие Cmd+Shift+4. Снимок экрана делается
 * средствами операционной системы и не наблюдаем в принципе — называть одно
 * другим в журнале, по которому потом разбирают утечку, нельзя.
 */

export const KIND_LABELS = {
    search: 'Искал номер',
    open: 'Открыл переписку',
    handoff: 'Передал чат-менеджеру',
};

export const ROLE_LABELS = {
    operator: 'Оператор',
    trainee: 'Стажёр',
    sv: 'Супервайзер',
    supervisor: 'Супервайзер',
    admin: 'Админ',
    super_admin: 'Супер-админ',
    trainer: 'Тренер',
};

/* Цвет — только там, где он несёт смысл. «Передал» — единственное действие,
 * которое меняет чужую систему и которое нельзя отозвать; искал и открыл —
 * нейтральные, их не красим вовсе. */
export const KIND_TONE = {
    search: 'slate',
    open: 'slate',
    handoff: 'blue',
};

export const kindLabel = (kind) => KIND_LABELS[kind] || kind || '—';
export const roleLabel = (role) => ROLE_LABELS[role] || role || '—';

/* Телефон читается группами, как его диктуют вслух: 8 776 003 44 05. */
export const formatPhone = (value) => {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length !== 11) return value || '—';
    return `8 ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7, 9)} ${digits.slice(9)}`;
};

export const formatDateTime = (iso) => {
    if (!iso) return '—';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '—';
    return parsed.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
};

export const formatTime = (iso) => {
    if (!iso) return '';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

export const formatDayShort = (iso) => {
    if (!iso) return '';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
};

const ruDate = (value) => {
    if (!value) return '—';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleDateString('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric',
    });
};

/* Имя файла выгрузки. Двойник на бэкенде — report.export_file_name: заголовок
 * Content-Disposition до фронта не доходит (его нет в
 * Access-Control-Expose-Headers), поэтому имя собирается с двух сторон и обязано
 * совпадать. */
export const exportFileName = (periodFrom, periodTo) => {
    const left = ruDate(periodFrom);
    const right = ruDate(periodTo);
    if (left === '—' && right === '—') return 'Журнал чатов водителей.xlsx';
    if (left === right) return `Журнал чатов водителей ${left}.xlsx`;
    return `Журнал чатов водителей ${left} — ${right}.xlsx`;
};
