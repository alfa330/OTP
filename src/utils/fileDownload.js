/*
 * Скачивание файла по ссылке — в ТОМ ЖЕ окне. Общая механика портала.
 *
 * ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ. Дистрибутивы (iCORE Phone, агент Oktell) лежат в GCS и
 * отдаются подписанной на час ссылкой, за которой ходят три места: пункт меню
 * «Скачать iCore Phone», «Настройки SIP» и раздел «Ограничитель Перезвона».
 * Все три открывали ссылку через window.open(url, '_blank'): браузер заводил
 * новую вкладку, та начинала загрузку и оставалась висеть пустой с адресом
 * хранилища — выглядело как сбой (замечание владельца 22.09.2026). Чинить в
 * трёх копиях значило бы разъехаться на четвёртой, поэтому механика здесь одна.
 *
 * ПОЧЕМУ СКРЫТЫЙ IFRAME, А НЕ <a download> И НЕ location.assign.
 *   * <a download> для чужого origin браузер игнорирует: ссылка ведёт на
 *     storage.googleapis.com, и клик по ней — обычный переход верхнего окна.
 *   * Любой переход верхнего окна поднимает beforeunload, а он в портале не
 *     пустой: редактор вики с несохранённым текстом спрашивает «покинуть
 *     страницу?» (WikiEditor.jsx). На нажатие «Скачать» такой вопрос — поломка.
 *   * Переход внутри iframe окно не трогает. Файл подписан с
 *     Content-Disposition: attachment (_icore_phone_signed_url в bot_schedule2.py
 *     и signed_download_url в oktell_guard/routes.py), поэтому браузер не
 *     рисует его во фрейме, а кладёт в загрузки — как обычную ссылку на файл.
 *
 * ФРЕЙМ ОДИН И ЖИВЁТ ДО КОНЦА СТРАНИЦЫ. Убрать его сразу после src нельзя:
 * загрузка, не дошедшая до заголовков ответа, отменяется вместе с фреймом.
 * Повторное нажатие переиспользует тот же фрейм — начатая загрузка уже у
 * менеджера загрузок браузера, и новый адрес её не прерывает.
 */

const FRAME_ID = 'otp-file-download-frame';

/** Годится ли ссылка в src фрейма: только http(s) — никаких javascript: и data:. */
export const isDownloadableUrl = (url) => /^https?:\/\/\S+$/i.test(String(url ?? '').trim());

/**
 * Начать скачивание файла по ссылке в этом же окне.
 *
 * Бросает Error словами для тоста, если ссылки нет или она не годится, — тем
 * же catch, которым вызывающий ловит ошибку своего запроса за ссылкой.
 * Возвращает false вне браузера (нет document), true — когда ссылка отдана.
 */
export const startFileDownload = (url) => {
    const href = String(url ?? '').trim();
    if (!isDownloadableUrl(href)) throw new Error('Сервер не вернул ссылку на файл');
    if (typeof document === 'undefined') return false;
    let frame = document.getElementById(FRAME_ID);
    if (!frame) {
        frame = document.createElement('iframe');
        frame.id = FRAME_ID;
        frame.title = 'Загрузка файла';
        frame.tabIndex = -1;
        frame.setAttribute('aria-hidden', 'true');
        frame.style.display = 'none';
        document.body.appendChild(frame);
    }
    frame.src = href;
    return true;
};
