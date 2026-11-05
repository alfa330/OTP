/*
 * Кому писать, если человек забыл пароль от портала (окно «Забыли пароль?» на
 * экране входа). Решение владельца 05.11.2026: автоматического сброса нет,
 * доступ восстанавливают Руслан и Шерзад вручную — человек пишет им в Telegram.
 *
 * Отдельным модулем, а не прямо в окне: ссылки и текст сообщения проверяются
 * тестом без отрисовки (tests/forgot_password_window.test.mjs).
 */

export const PASSWORD_HELP_CONTACTS = Object.freeze([
    Object.freeze({ username: 'nalsuur', name: 'Руслан', dative: 'Руслану' }),
    Object.freeze({ username: 'sherzAdh', name: 'Шерзад', dative: 'Шерзаду' }),
]);

/* Логин в сообщении — ровно то, что человек набрал в форме входа. Длинную
   строку (вставил по ошибке абзац) обрезаем: ссылка не должна разрастаться. */
const LOGIN_MAX_LENGTH = 64;

/* Текст, который Telegram подставит в поле сообщения. Без логина — заготовка
   «ФИО:», чтобы помогающему не пришлось переспрашивать, кто пишет: имя в
   Telegram часто не совпадает с карточкой сотрудника. «Не помню», а не
   «забыл(а)» — фраза одна для всех. */
export function passwordHelpDraft(login) {
    const value = String(login ?? '').replace(/\s+/g, ' ').trim().slice(0, LOGIN_MAX_LENGTH).trim();
    const intro = 'Здравствуйте! Не помню пароль от iCORE.';
    return value ? `${intro} Логин: ${value}` : `${intro} ФИО: `;
}

/* Чат с человеком; text= заполняет поле ввода (core.telegram.org/api/links,
   «Public username links»). https://t.me, а не tg://: без установленного
   Telegram ссылка tg:// молча ничего не делает, а t.me откроет страницу с
   кнопкой и веб-версией. */
export function telegramChatUrl(username, draft = '') {
    const base = `https://t.me/${encodeURIComponent(username)}`;
    return draft ? `${base}?text=${encodeURIComponent(draft)}` : base;
}

/* Публичное фото профиля Telegram. 160 px — с запасом на экран с тройной
   плотностью при кружке в 44 px, и в три раза легче 320. */
export function telegramUserpicUrl(username) {
    return `https://t.me/i/userpic/160/${encodeURIComponent(username)}.jpg`;
}
