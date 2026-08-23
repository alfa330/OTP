import { useCallback, useEffect, useMemo, useRef } from 'react';
import axios from 'axios';

/* Учёт попытки: кто сел за тренажёр, откуда и чем это кончилось.
 *
 * Запросов на урок два — «начал» и «закрыл». Промежуточных нет намеренно:
 * шагов в сценарии восемь, и отправка на каждом превратила бы пятиминутный
 * урок в десяток запросов ради цифры, которую всё равно смотрят раз в неделю.
 *
 * Что делать с брошенной попыткой. Строка заводится на СТАРТЕ и живёт со
 * статусом «начал», пока урок не закроют. Закрыли крестиком — досылаем, докуда
 * дошли. Закрыли вкладку — досылает `fetch(keepalive)` на pagehide.
 *
 * Исключение — finishedOnly. У тренажёра, где итог это СДЕЛАННАЯ работа
 * (заведённая карточка обращения в CRM), брошенная попытка не значит ничего:
 * карточки нет, показывать в статистике нечего. Такой тренажёр молчит до
 * конца и присылает всё ОДНИМ запросом после «Сохранить» — вместе с самой
 * карточкой. Режим включается сценарием, а не глобально: у прогулок по
 * инструкции брошенные попытки — половина ценности отчёта.
 *
 * Почему keepalive, а не navigator.sendBeacon. Маячок не умеет заголовки, а
 * раздел авторизуется Bearer-токеном в Authorization: маячок ушёл бы в 401 и
 * попытка потерялась бы ровно в том случае, ради которого он и нужен.
 *
 * Учёт НИКОГДА не мешает уроку. Любая ошибка сети гасится молча: тренажёр
 * работает и без записи, а красный тост поверх учебного телефона стоит дороже
 * пропущенной строки в статистике.
 */

/* pagehide, а не beforeunload: на телефонах вкладку чаще не «закрывают», а
   уводят в фон, и beforeunload там не приходит вовсе. visibilitychange не
   годится — он срабатывает и на переключение вкладки, то есть посреди урока. */
const CLOSE_EVENT = 'pagehide';

export default function useTrainerRun({
    base, headers, trainerKey, stagesTotal, articleId = null, source = 'article',
    enabled = true, finishedOnly = false,
}) {
    /* Всё состояние в ref, а не в state: перерисовывать проигрыватель ради
       учёта нельзя — на нём висят анимации появления, и лишний рендер во время
       выезда телефона виден глазом. */
    const runIdRef = useRef(null);
    const startedAtRef = useRef(0);
    const restartsRef = useRef(0);
    const closedRef = useRef(false);
    const progressRef = useRef({ done: 0, total: stagesTotal || 0, errors: 0, hints: 0, result: null });

    /* Заголовки и адрес — в ref: они попадают в обработчик pagehide, который
       вешается один раз. Через замыкание там застыли бы значения первого
       рендера, и досылка ушла бы со старым токеном. */
    const wire = useRef({ base, headers });
    wire.current = { base, headers };

    /** Что фронт знает о попытке прямо сейчас. Зовётся на каждом шаге —
     *  дешёвая запись в ref, без запроса. */
    const track = useCallback((progress) => {
        progressRef.current = { ...progressRef.current, ...progress };
    }, []);

    const payload = useCallback((status) => {
        const { done, total, errors, hints, result } = progressRef.current;
        return {
            status,
            stages_done: status === 'finished' ? (total || done) : done,
            errors,
            hints,
            restarts: restartsRef.current,
            duration_ms: startedAtRef.current ? Date.now() - startedAtRef.current : null,
            // ЧТО человек сделал. Есть не у всех тренажёров: у прогулки по
            // инструкции итог один на всех — «дошёл до конца».
            result: result || undefined,
        };
    }, []);

    /** Досылка при закрытии вкладки. keepalive переживает выгрузку страницы,
     *  axios — нет: его XHR браузер обрывает вместе с документом. */
    const beacon = useCallback((status) => {
        const id = runIdRef.current;
        // Тренажёру с записью только завершённых досылать нечего: он пишет
        // строку по факту «Сохранить», а закрытая на полпути вкладка означает
        // ровно отсутствие результата.
        if (!id || finishedOnly) return;
        const { base: url, headers: auth } = wire.current;
        try {
            fetch(`${url}/trainers/runs/${id}`, {
                method: 'POST',
                keepalive: true,
                headers: { ...(auth || {}), 'Content-Type': 'application/json' },
                body: JSON.stringify(payload(status)),
            }).catch(() => {});
        } catch {
            /* Учёт не мешает уроку. */
        }
    }, [payload, finishedOnly]);

    /* Завести строку попытки. Отдельной функцией, потому что зовут её двое:
       открытие проигрывателя и «Пройти ещё раз». */
    const open = useCallback(() => {
        if (!enabled || !base || !trainerKey) return;
        runIdRef.current = null;
        closedRef.current = false;
        startedAtRef.current = Date.now();
        progressRef.current = { done: 0, total: stagesTotal || 0, errors: 0, hints: 0, result: null };
        /* Тренажёр, у которого итог — сделанная работа (заведённая карточка
           обращения), строку на старте не заводит: до «Сохранить» записывать
           нечего, а брошенная попытка без карточки в его статистике только
           мешает. Такой тренажёр присылает всё одним запросом в конце. */
        if (finishedOnly) return;
        axios.post(`${base}/trainers/runs`, {
            key: trainerKey,
            article_id: articleId,
            source,
            stages_total: stagesTotal || 0,
        }, { headers })
            .then((r) => { runIdRef.current = r.data?.run_id || null; })
            .catch(() => { /* Учёт не мешает уроку. */ });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, base, trainerKey, stagesTotal, articleId, source, finishedOnly]);

    // Первая попытка заводится один раз на открытие проигрывателя.
    useEffect(() => { open(); }, [open]);

    // Закрытие вкладки прямо посреди урока.
    useEffect(() => {
        if (!enabled) return undefined;
        const onHide = () => {
            if (closedRef.current) return;
            closedRef.current = true;
            beacon('abandoned');
        };
        window.addEventListener(CLOSE_EVENT, onHide);
        return () => window.removeEventListener(CLOSE_EVENT, onHide);
    }, [enabled, beacon]);

    /** Урок закрыт по-человечески: крестиком или дойдя до конца. */
    const close = useCallback((status) => {
        if (closedRef.current) return;
        const { base: url, headers: auth } = wire.current;

        if (finishedOnly) {
            // Пишем ТОЛЬКО дошедшего до конца, и одним запросом: строки,
            // которую можно было бы дополнить, ещё не существует.
            if (status !== 'finished' || !url || !trainerKey) return;
            closedRef.current = true;
            axios.post(`${url}/trainers/runs`, {
                key: trainerKey,
                article_id: articleId,
                source,
                stages_total: stagesTotal || 0,
                ...payload('finished'),
            }, { headers: auth }).catch(() => { /* Учёт не мешает уроку. */ });
            return;
        }

        if (!runIdRef.current) return;
        closedRef.current = true;
        axios.post(`${url}/trainers/runs/${runIdRef.current}`, payload(status), { headers: auth })
            .catch(() => { /* Учёт не мешает уроку. */ });
    }, [payload, finishedOnly, trainerKey, articleId, source, stagesTotal]);

    /* «Пройти ещё раз» — ОТДЕЛЬНАЯ попытка: закрываем текущую и заводим новую.
       Продолжать в той же строке нельзя, потому что движок обнуляет промахи и
       подсказки (runner.js, restart), и в строке остались бы счётчики только
       последнего захода при времени от первого. Отчёту по людям это не вредит:
       людей он считает по DISTINCT, а не по строкам, зато «прошёл с третьего
       раза» становится видно как три честные попытки. */
    const restart = useCallback(() => {
        restartsRef.current += 1;
        close('abandoned');
        open();
    }, [close, open]);

    /* Возвращаем ОДИН И ТОТ ЖЕ объект, а не новый на каждый рендер. Это не
       микрооптимизация: проигрыватель кладёт его в зависимости эффекта, у
       которого в очистке стоит «попытка брошена». С новым объектом очистка
       срабатывала бы на каждом рендере и помечала урок брошенным на втором
       нажатии. */
    return useMemo(() => ({ track, close, restart }), [track, close, restart]);
}
