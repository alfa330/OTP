import { useCallback, useEffect, useRef, useState } from 'react';
import { createPhotoPreviewCache, taskPhotoIds } from './taskPhotos';

/* Ответы переживают закрытие карточки: открыть ту же задачу второй раз — это
   ноль запросов и те же адреса, то есть картинки из кэша браузера. */
const previewCache = createPhotoPreviewCache();

/* Адреса картинок карточки задачи: { previews, settled, broken, markBroken, refresh }.
 *
 * Один запрос на карточку и только если в ней есть картинки. `load` держим в
 * ref: он приходит из раздела заново на рендерах, а список зависимостей
 * эффекта — это список причин сходить на сервер ещё раз.
 *
 * ДОСЛАЛИ ФАЙЛ В ОТКРЫТУЮ КАРТОЧКУ (уточнение, результат, обновление списка)
 * — набор картинок меняется, но адреса уже показанных остаются на месте до
 * ответа сервера: иначе все плитки мигнули бы пустыми, а открытый просмотр
 * закрылся бы и открылся сам. Пустой плиткой ждёт только новая картинка.
 *
 * refresh — для просмотра, открытого дольше, чем живёт ссылка: <img> ответит
 * ошибкой, и адреса перезапрашиваются. Не больше раза на карточку — иначе
 * картинка, которую браузер не может нарисовать в принципе, крутила бы запросы
 * по кругу: ошибка → перезапрос → тот же адрес → ошибка.
 */
export default function useTaskPhotoPreviews(task, load) {
    const taskId = Number(task?.id || 0);
    const idsKey = taskPhotoIds(task).join(',');
    const key = `${taskId}:${idsKey}`;
    const loadRef = useRef(load);
    loadRef.current = load;
    const retriedRef = useRef('');
    const [reloadTick, setReloadTick] = useState(0);
    const [state, setState] = useState({ key: '', taskId: 0, previews: {}, settled: false });
    const [broken, setBroken] = useState({ key: 0, ids: new Set() });

    useEffect(() => {
        if (!taskId || !idsKey || typeof loadRef.current !== 'function') {
            setState({ key, taskId, previews: {}, settled: true });
            return undefined;
        }
        const ids = idsKey.split(',').map(Number);
        const cached = previewCache.read(taskId, ids, Date.now());
        if (cached) {
            setState({ key, taskId, previews: cached, settled: true });
            return undefined;
        }
        let cancelled = false;
        // Та же задача — прежние адреса остаются до прихода новых (см. шапку).
        setState((prev) => (prev.key === key ? prev : {
            key, taskId, previews: prev.taskId === taskId ? prev.previews : {}, settled: false,
        }));
        Promise.resolve()
            .then(() => loadRef.current(taskId))
            .then((photos) => {
                if (cancelled) return;
                setState({ key, taskId, previews: previewCache.write(taskId, ids, photos, Date.now()), settled: true });
            })
            .catch(() => {
                // Сбой не кэшируем: следующее открытие карточки попробует снова.
                // Уже показанные адреса оставляем, остальные картинки станут
                // обычными кнопками файлов — скачать их можно.
                if (!cancelled) {
                    setState((prev) => ({
                        key, taskId, previews: prev.taskId === taskId ? prev.previews : {}, settled: true,
                    }));
                }
            });
        return () => { cancelled = true; };
    }, [idsKey, key, reloadTick, taskId]);

    // Привязка к задаче, а не к набору: досланный файл не должен возвращать
    // плитку картинке, которую браузер уже не смог нарисовать.
    const markBroken = useCallback((id) => {
        setBroken((prev) => {
            const ids = new Set(prev.key === taskId ? prev.ids : []);
            ids.add(Number(id));
            return { key: taskId, ids };
        });
    }, [taskId]);

    const refresh = useCallback(() => {
        if (retriedRef.current === key) return false;
        retriedRef.current = key;
        previewCache.drop(taskId);
        setReloadTick((tick) => tick + 1);
        return true;
    }, [key, taskId]);

    // Между сменой набора и эффектом один кадр рисуется со старым состоянием:
    // у той же задачи прежние адреса годятся, у другой — нет.
    const current = state.key === key;
    const sameTask = state.taskId === taskId;
    return {
        previews: current || sameTask ? state.previews : {},
        settled: current ? state.settled : !idsKey,
        broken: broken.key === taskId ? broken.ids : EMPTY_SET,
        markBroken,
        refresh,
    };
}

const EMPTY_SET = new Set();
