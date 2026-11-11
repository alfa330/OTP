import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import axios from 'axios';
import { AlertCircle, Loader2, RotateCw } from 'lucide-react';
import { KNOWN_ERROR_CODES, describeError, isWindowUrl, portalTheme, readError, windowRequestBody } from './syntonyRules';

/*
 * Окно Syntony: фрейм с одноразовой ссылкой и всё, что вокруг неё.
 *
 * ССЫЛКА ОДНОРАЗОВАЯ И ЖИВЁТ 60 СЕКУНД (ТЗ, раздел 8), поэтому её просят в момент
 * открытия и нигде не хранят: ни в состоянии выше, ни в localStorage. Каждое
 * «Открыть заново» — новый запрос и НОВЫЙ фрейм (key по ссылке): подмена src у
 * прежнего дописывала бы шаг в историю вкладки, и «назад» в браузере листало бы окно.
 *
 * Фрейм — как просит ТЗ (задача 4): allow="microphone" (запись голосовых) и без
 * sandbox (с ним не открылась бы кнопка звонка icore-phone:). Размер окно берёт у
 * контейнера и своего не сообщает — высоту задаёт тот, кто его ставит.
 *
 * Ошибка разбирается по error.code (syntonyRules.js), а не по статусу и не по тексту.
 */

const frameClass = 'block h-full w-full border-0 bg-white';
const FRAME_REVEAL_MS = 8000;

const SyntonyWindow = forwardRef(({
    apiBaseUrl, withAccessTokenHeader, scope, phones = null, manager = false, title = 'Syntony', onPhase = null,
    guard = false,
}, ref) => {
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);
    const phaseRef = useRef(onPhase);
    useEffect(() => { phaseRef.current = onPhase; }, [onPhase]);

    const [state, setState] = useState({ phase: 'loading', url: '', error: null });
    const [frameReady, setFrameReady] = useState(false);
    const requestRef = useRef(0);
    // Номера — значением, а не ссылкой на массив: новый массив с теми же номерами
    // не должен перезапрашивать окно.
    const phonesKey = Array.isArray(phones) ? phones.join('|') : '';

    const load = useCallback(async () => {
        const current = ++requestRef.current;
        setFrameReady(false);
        setState({ phase: 'loading', url: '', error: null });
        try {
            const { data } = await axios.post(
                `${apiBaseUrl}/api/syntony/window`,
                windowRequestBody({ scope, phones: phonesKey ? phonesKey.split('|') : null, theme: portalTheme() }),
                { headers: headersRef.current ? headersRef.current() : {} },
            );
            if (current !== requestRef.current) return;
            if (isWindowUrl(data?.url)) setState({ phase: 'ready', url: data.url, error: null });
            else setState({ phase: 'error', url: '', error: readError(data) });
        } catch (failure) {
            if (current !== requestRef.current) return;
            const error = readError(failure?.response?.data);
            if (error.code && !KNOWN_ERROR_CODES.includes(error.code)) {
                // Незнакомый код — в журнал (ТЗ, задача 5); оператору — общий текст.
                console.warn('Syntony: незнакомый код ошибки окна', error.code);
            }
            setState({ phase: 'error', url: '', error });
        }
    }, [apiBaseUrl, scope, phonesKey]);

    useEffect(() => {
        load();
        // Ответ запроса, ушедшего до закрытия окна, уже никому не нужен.
        return () => { requestRef.current += 1; };
    }, [load]);

    useImperativeHandle(ref, () => ({ reload: load }), [load]);

    /* Фрейм показываем по его load, но не ждём вечно: если браузер так и не сообщил о
       загрузке (отказ встроить страницу — адрес портала не внесён у Syntony), человек
       должен увидеть то, что браузер нарисовал во фрейме, а не вечное «открываем». */
    useEffect(() => {
        if (state.phase !== 'ready' || frameReady) return undefined;
        const timer = setTimeout(() => setFrameReady(true), FRAME_REVEAL_MS);
        return () => clearTimeout(timer);
    }, [state.phase, state.url, frameReady]);

    const errorCode = state.error?.code || '';
    useEffect(() => { phaseRef.current?.(state.phase, errorCode); }, [state.phase, errorCode]);

    if (state.phase === 'error') {
        const view = describeError(state.error, { manager });
        return (
            <div className="flex h-full min-h-[220px] items-center justify-center bg-white px-6" role="alert" data-testid="syntony-window-error">
                <div className="flex max-w-md flex-col items-center text-center">
                    <span className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <AlertCircle size={22} aria-hidden="true" />
                    </span>
                    <p className="mt-3 text-[14px] leading-relaxed text-slate-700">{view.text}</p>
                    {view.note && <p className="mt-1.5 font-mono text-[11.5px] text-slate-400">{view.note}</p>}
                    <button type="button" onClick={load}
                        className="mt-4 inline-flex h-9 items-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98]">
                        <RotateCw size={14} aria-hidden="true" /> Открыть заново
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="relative h-full min-h-[220px] bg-white">
            {!frameReady && (
                <div className="absolute inset-0 flex items-center justify-center gap-2 text-[13px] text-slate-500" data-testid="syntony-window-loading">
                    <Loader2 size={16} className="animate-spin" aria-hidden="true" /> Открываем окно Syntony…
                </div>
            )}
            {state.phase === 'ready' && (
                <iframe
                    key={state.url}
                    src={state.url}
                    title={title}
                    allow="microphone"
                    onLoad={() => setFrameReady(true)}
                    className={`${frameClass}${frameReady ? '' : ' opacity-0'}`}
                    data-testid="syntony-window-frame"
                />
            )}
            {/* Заслонка — прозрачный слой портала поверх фрейма, пока развёрнут сайдбар или
                мышь на строке над окном. Нужна только окну в разделе (guard): его стили живут
                в syntony.css, который подключает SyntonyView; окну в модалке она ни к чему. */}
            {guard && state.phase === 'ready' && <div className="syntony-frame-guard" aria-hidden="true" />}
        </div>
    );
});

SyntonyWindow.displayName = 'SyntonyWindow';

export default SyntonyWindow;
