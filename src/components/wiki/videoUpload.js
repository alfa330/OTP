/* Ролики вики: какой файл берём и как он едет в хранилище.
 *
 * Владелец 18.11.2026: «в вики нужно реализовать такую возможность, чтобы можно
 * было загружать ролики как статью». Ролик весит сотни мегабайт, и картинкой
 * (POST /upload через наш сервер, до 25 МБ) его не провезти. Поэтому он едет
 * ОЧЕРЕДЬЮ «Продакшна» (production/productionUpload.js): сервер вики открывает
 * в хранилище сессию докачки, браузер шлёт файл прямо туда, после обрыва
 * продолжает с принятого места, считает CRC32C и снимает кадр-обложку. Второй
 * копии этой механики нет — отсюда в очередь уходят только адреса ручек вики
 * (wiki/routes_videos.py).
 *
 * Какой файл берём, решает videoRules.js (те же правила, что wiki/videos.py):
 * проверка в браузере не заменяет серверную, а избавляет человека от ожидания —
 * MKV на 800 МБ должен получить отказ сразу, а не после открытия сессии.
 */

import axios from 'axios';

import { uploadStore } from '../production/productionUpload';
import { wikiVideoEndpoints } from './videoRules';

/**
 * Поставить ролик в очередь. Возвращает номер строки очереди: по нему узел в
 * тексте статьи следит за загрузкой и по нему же его потом находят.
 *
 * headers — функция: загрузка длится минутами, и брать заголовок входа надо в
 * момент запроса, а не в момент выбора файла.
 * onSettled(key) — строка дошла до конца (готово или ошибка); сама строка —
 * в uploadStore.snapshot().
 */
export const startVideoUpload = ({ file, base, headers, onSettled }) => {
    const [key] = uploadStore.add({
        files: [file],
        apiBaseUrl: '',
        headers,
        endpoints: wikiVideoEndpoints(base),
        onDone: (_taskId, item) => onSettled?.(item.key),
    });
    return key;
};

export const uploadRow = (key) => uploadStore.snapshot().find((item) => item.key === key) || null;

/* Ролики, которые догрузились, а адрес ещё не встал в узел (редактор спрашивает
   его у сервера, а строку очереди та сама убирает через полторы секунды). Без
   этой отметки узел не отличил бы «готовим ролик» от «загрузку отменили»:
   отменённая и возвращённая «Повторить» загрузка висела бы «Готовим ролик…»
   вечно (поймано разбором 18.11.2026). */
const finishing = new Set();
export const markFinishing = (key, on) => {
    if (on) finishing.add(key);
    else finishing.delete(key);
};
export const isFinishing = (key) => finishing.has(key);

/* Узел без адреса, которому уже нечего ждать: отменён, потерян, с ошибкой. */
export const uploadStuck = ({ uploadKey, uploadError }) => Boolean(uploadError
    || !uploadKey
    || uploadRow(uploadKey)?.state === 'failed'
    || (!uploadRow(uploadKey) && !isFinishing(uploadKey)));

/** Готовый ролик с сервера: адрес, обложка, размер кадра, длительность. */
export const fetchVideo = (base, headers, fileId) => axios
    .get(`${base}/videos/${fileId}`, { headers })
    .then((response) => response.data || {});
