/* Скачивание статьи файлом Word — счётная часть и механика сохранения.
 *
 * Вынесено из WikiArticle.jsx отдельным модулем тем же приёмом, что
 * articleLink.js и imageSize.js: витрина тянет React и половину раздела, то
 * есть проверить `node --test` без сборки её нельзя, а ошибаться здесь есть
 * где — в имени файла и в разборе ошибки.
 *
 * ПОЧЕМУ BLOB, А НЕ startFileDownload. Общая механика портала
 * (src/utils/fileDownload.js) открывает в скрытом фрейме ПОДПИСАННУЮ ссылку на
 * хранилище — заголовков она не несёт и не нужна. Файл статьи же собирает наш
 * API, и дверь /articles/<id>/docx требует авторизацию: фрейм без заголовка
 * получил бы страницу входа вместо документа. Поэтому файл приходит ответом
 * axios (responseType: 'blob') и отдаётся браузеру временной ссылкой — ровно
 * так же скачивает книгу «Аналитика» (WikiAnalytics.jsx). Окно при этом не
 * покидается, новая вкладка не открывается.
 */

export const DOCX_EXTENSION = '.docx';
export const FILENAME_MAX = 100;

/* Имя файла из названия статьи. Формула та же, что на сервере
   (wiki/docx_export.py: file_name): знаки, запрещённые в именах Windows,
   заменяются пробелом, пробелы схлопываются, хвостовые точки снимаются
   (Windows отбрасывает их молча), длина — до 100 знаков. Кириллица остаётся:
   «Регламент выплат.docx» читается в загрузках, «reglament-vyplat» — нет. */
export const docxFileName = (title) => {
    let name = String(title ?? '').replace(/[\\/:*?"<>|\u0000-\u001f]+/g, ' ');
    name = name.replace(/\s+/g, ' ').trim().replace(/\.+$/, '').trim();
    name = name.slice(0, FILENAME_MAX).replace(/[\s.]+$/, '') || 'Статья';
    return name + DOCX_EXTENSION;
};

/* Адрес страницы портала без строки запроса и якоря — для внутренних ссылок в
   документе. Сервер своего адреса не знает: фронт живёт на GitHub Pages с
   базовым путём, и собранный на сервере «/?view=wiki» увёл бы на корень
   домена. Пусто вне браузера. */
export const portalAddress = (location = globalThis.window?.location) => {
    if (!location?.origin || !location?.pathname) return '';
    return `${location.origin}${location.pathname}`;
};

/* Отдать байты браузеру как файл с именем name. Возвращает false вне браузера. */
export const saveBlob = (data, name, doc = globalThis.document) => {
    if (!doc?.body || typeof URL === 'undefined' || !URL.createObjectURL) return false;
    const blob = typeof Blob !== 'undefined' && data instanceof Blob ? data : new Blob([data]);
    const url = URL.createObjectURL(blob);
    const link = doc.createElement('a');
    link.href = url;
    link.download = name;
    doc.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    return true;
};

/* Текст ошибки для тоста. У запроса с responseType: 'blob' тело ошибки тоже
   приезжает Blob'ом — JSON сервера («Скачивать статьи файлом могут
   администраторы и выше») надо прочитать, иначе человек видит «Request failed
   with status code 403». */
export const downloadErrorText = async (error, fallback) => {
    const body = error?.response?.data;
    if (body && typeof body.text === 'function') {
        try {
            const parsed = JSON.parse(await body.text());
            if (parsed?.error) return String(parsed.error);
        } catch (parseError) {
            // не JSON — ниже возьмём общее сообщение
        }
    }
    if (body?.error) return String(body.error);
    return error?.message || fallback;
};
