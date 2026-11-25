import React, { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import lazyWithRetry from '../../utils/lazyWithRetry';
import callScenario from '../wiki/trainers/scenarioOperatorCall.js';
import '../wiki/trainers/trainer.css';
import { createAiCall } from './aiCall';
import { VoiceLink } from './voice';

const TrainerModal = lazyWithRetry(() => import('../wiki/trainers/TrainerPlayer'));

/* Смена на линии со звонком ИИ-водителя — раздел «Тренажёр».
 *
 * Владелец, 25.11.2026: «при запуске — как среда Windows на весь экран:
 * браузер с CRM и звонилка наш iCORE Phone; с айкорфона приходит звонок, там
 * ИИ-водитель говорит свои данные — так обрабатывается звонок; перезапуском
 * выбирается тип водителя, и у каждого водителя свои придуманные данные».
 *
 * Здесь сходятся три части:
 *   — дело водителя (GET /api/trainer/cases/<ключ>): его данные в учебной CRM,
 *     у каждого сценария своё — ФИО, телефон, парк, ведомость, заказы;
 *   — рабочее место (TrainerPlayer в среде Windows): CRM, iCORE Phone, звонок;
 *   — голос (aiCall.js): ответили в iCORE Phone — заговорил ИИ-водитель.
 * «Новый звонок…» в меню «Учебная смена» — выбор водителя и новая смена с
 * его делом; после постобработки — разбор звонка от ИИ.
 */

/* Смена в среде Windows: без вступительной карточки (рабочий стол открывается
   сразу) и без экрана итога (после попытки остаётся рабочий стол — поверх
   него разбор). */
export const DESK_SCENARIO = {
    ...callScenario,
    steps: callScenario.steps
        .filter((step) => step.key !== 'intro')
        .map((step) => (step.screen === 'result' ? { ...step, screen: 'desk' } : step)),
};

const LANG = { ru: 'говорит по-русски', kk: 'говорит по-казахски' };

/** Случайный водитель — не тот же, что звонил только что (если есть выбор). */
export const pickDriver = (scenarios, previous = null, random = Math.random) => {
    const keys = (scenarios || []).map((item) => item.key);
    const pool = keys.length > 1 ? keys.filter((key) => key !== previous) : keys;
    return pool.length ? pool[Math.floor(random() * pool.length) % pool.length] : null;
};

/** Дело водителя не пришло. Идёт смена — она остаётся, хватит уведомления поверх;
    смены нет — окно «Смена не началась» с «Повторить» (иначе экран пуст). */
export const launchFailure = (hasShift, choice, error) => {
    const text = `Не удалось открыть дело водителя: ${error?.message || error}`;
    return hasShift ? { problem: text } : { failed: { choice, text } };
};

const scoreTone = (score) => (score >= 80 ? 'is-good' : score >= 50 ? 'is-mid' : 'is-low');

/** Окно Windows 11 поверх рабочего стола (ContentDialog). */
const WinDialog = ({ title, children, footer, wide = false }) => (
    <div className="wt-winlayer__smoke">
        <div className={`wt-windlg${wide ? ' is-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
            <div className="wt-windlg__body">
                <h2 className="wt-windlg__title">{title}</h2>
                {children}
            </div>
            <div className="wt-windlg__foot">{footer}</div>
        </div>
    </div>
);

/** «Кто позвонит?» — выбор водителя для следующего звонка. */
const DriverPicker = ({ scenarios, onPick, onCancel, busy }) => {
    const [choice, setChoice] = useState('random');
    return (
        <WinDialog
            title="Кто позвонит?"
            footer={(
                <>
                    <button type="button" className="wt-winbtn is-accent" disabled={busy} onClick={() => onPick(choice)}>
                        {busy ? 'Соединяем…' : 'Начать смену'}
                    </button>
                    {onCancel ? <button type="button" className="wt-winbtn" onClick={onCancel}>Отмена</button> : null}
                </>
            )}
        >
            <p className="wt-windlg__text">
                Выберите водителя — у каждого свой характер и свои данные в CRM. Звонок придёт в iCORE Phone
                через несколько секунд после начала смены.
            </p>
            <div className="wt-windlg__options" role="radiogroup" aria-label="Водитель">
                {[{ key: 'random', title: 'Случайный водитель', note: 'как на линии: не знаешь, кто позвонит' },
                    ...scenarios.map((item) => ({
                        key: item.key,
                        title: item.title,
                        note: `сложность ${item.difficulty}/10 · ${LANG[item.lang] || item.lang}`,
                    }))].map((item) => (
                    <button key={item.key} type="button" role="radio" aria-checked={choice === item.key}
                        className={`wt-winradio${choice === item.key ? ' is-on' : ''}`}
                        onClick={() => setChoice(item.key)}>
                        <i aria-hidden="true" />
                        <span>
                            <b>{item.title}</b>
                            <small>{item.note}</small>
                        </span>
                    </button>
                ))}
            </div>
        </WinDialog>
    );
};

/** «Разбор звонка» — оценка ИИ по критериям сценария. */
const ReviewDialog = ({ title, result, onNext, onExit }) => {
    const review = result?.review;
    return (
        <WinDialog
            title="Разбор звонка"
            wide
            footer={(
                <>
                    <button type="button" className="wt-winbtn is-accent" onClick={onNext}>Следующий звонок…</button>
                    <button type="button" className="wt-winbtn" onClick={onExit}>Выйти из тренажёра</button>
                </>
            )}
        >
            <p className="wt-windlg__text">{title}</p>
            {review ? (
                <>
                    <div className="wt-windlg__score">
                        <b className={scoreTone(review.score)}>{review.score}</b><span>из 100</span>
                    </div>
                    <div className="wt-windlg__criteria">
                        {(review.criteria || []).map((item, index) => (
                            <div key={index} className="wt-windlg__criterion">
                                <span>
                                    <b>{item.name}</b>
                                    <small>{item.comment}</small>
                                </span>
                                <em>{item.points}/{item.max}</em>
                            </div>
                        ))}
                    </div>
                    {(review.critical || []).length ? (
                        <div className="wt-windlg__critical">
                            <b>Критические ошибки</b>
                            {review.critical.map((item, index) => <span key={index}>{item}</span>)}
                        </div>
                    ) : null}
                    {review.recommendation ? <p className="wt-windlg__text">{review.recommendation}</p> : null}
                </>
            ) : (
                <p className="wt-windlg__text">
                    Разбора нет{result?.review_error ? `: ${result.review_error}` : ''}.
                </p>
            )}
        </WinDialog>
    );
};

const enterFullscreen = () => {
    try {
        const node = document.documentElement;
        if (!document.fullscreenElement && node.requestFullscreen) node.requestFullscreen().catch(() => {});
    } catch { /* браузер не дал — остаёмся в окне */ }
};
const leaveFullscreen = () => {
    try {
        if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
    } catch { /* уже не полноэкранный */ }
};

/**
 * api        — (path, init) => Promise<json> раздела «Тренажёр»;
 * apiBaseUrl, headers — для голоса (VoiceLink);
 * scenarios  — водители (GET /scenarios);
 * initialKey — с кем начать ('random' — случайный);
 * makeLink   — подмена VoiceLink (стенд и тесты);
 * onExit     — выход из тренажёра.
 */
export default function VoiceDesk({
    api, apiBaseUrl, headers, scenarios, initialKey = 'random', makeLink = null, onExit,
}) {
    const [shift, setShift] = useState(null);       // { key, title, caseData, call, nonce }
    const [picking, setPicking] = useState(false);
    const [review, setReview] = useState(null);     // { title, result }
    const [problem, setProblem] = useState('');
    // Смена не открылась вовсе (дело водителя не пришло): окно с «Повторить» —
    // иначе экран пуст, а повторное «Начать смену» в разделе ничего не меняет.
    const [failed, setFailed] = useState(null);     // { choice, text }
    const [busy, setBusy] = useState(false);
    const shiftRef = useRef(null);
    shiftRef.current = shift;

    const launch = useCallback(async (choice) => {
        const key = choice === 'random' ? pickDriver(scenarios, shiftRef.current?.key) : choice;
        if (!key) { setProblem('Нет ни одного сценария водителя.'); return; }
        setBusy(true);
        try {
            const caseData = await api(`/cases/${key}`);
            const title = (scenarios.find((item) => item.key === key) || {}).title || key;
            const call = createAiCall({
                api,
                makeLink: makeLink || ((onEvent) => new VoiceLink({ apiBaseUrl, headers, mode: 'driver', onEvent })),
                scenarioKey: key,
                client: {
                    ua: typeof navigator !== 'undefined' ? navigator.userAgent : '',
                    screen: typeof window !== 'undefined' ? `${window.screen?.width}x${window.screen?.height}` : '',
                    place: 'windows-desk',
                },
                onProblem: (text) => setProblem(text),
                onReview: (result) => setReview({ title, result }),
            });
            shiftRef.current?.call.dispose();
            setShift({ key, title, caseData, call, nonce: Date.now() });
            setReview(null);
            setPicking(false);
            setFailed(null);
        } catch (error) {
            const outcome = launchFailure(Boolean(shiftRef.current), choice, error);
            if (outcome.problem) setProblem(outcome.problem);
            else setFailed(outcome.failed);
        } finally {
            setBusy(false);
        }
    }, [api, apiBaseUrl, headers, makeLink, scenarios]);

    // Первый звонок — сразу, с водителем, выбранным в разделе.
    useEffect(() => { launch(initialKey); }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // Ушли из тренажёра — микрофон, сессия и полный экран не висят.
    useEffect(() => () => { shiftRef.current?.call.dispose(); leaveFullscreen(); }, []);

    useEffect(() => {
        if (!problem) return undefined;
        const id = setTimeout(() => setProblem(''), 9000);
        return () => clearTimeout(id);
    }, [problem]);

    const exit = useCallback(() => {
        shiftRef.current?.call.dispose();
        leaveFullscreen();
        onExit?.();
    }, [onExit]);

    const menu = [
        { label: 'Новый звонок…', hint: 'выбрать водителя', onClick: () => setPicking(true) },
        { label: 'Выйти из тренажёра', danger: true, onClick: exit },
    ];

    const layer = picking || review || problem || failed || (!shift && busy);
    return (
        <>
            {shift ? (
                <Suspense fallback={null}>
                    <TrainerModal
                        key={shift.nonce}
                        scenario={DESK_SCENARIO}
                        caseData={shift.caseData}
                        voice={shift.call}
                        onCall={shift.call.onCall}
                        environment="windows"
                        desktopMenu={menu}
                        onClose={exit}
                    />
                </Suspense>
            ) : null}
            {layer && typeof document !== 'undefined' ? createPortal(
                <div className="wt-winlayer">
                    {!shift && busy ? <div className="wt-winlayer__smoke"><span className="wt-winlayer__wait">Готовим рабочее место…</span></div> : null}
                    {failed && !shift && !busy ? (
                        <WinDialog
                            title="Смена не началась"
                            footer={(
                                <>
                                    <button type="button" className="wt-winbtn is-accent"
                                        onClick={() => launch(failed.choice)}>Повторить</button>
                                    <button type="button" className="wt-winbtn" onClick={exit}>Выйти из тренажёра</button>
                                </>
                            )}
                        >
                            <p className="wt-windlg__text">{failed.text}</p>
                        </WinDialog>
                    ) : picking ? (
                        <DriverPicker scenarios={scenarios} busy={busy} onPick={launch}
                            onCancel={() => setPicking(false)} />
                    ) : review ? (
                        <ReviewDialog title={review.title} result={review.result}
                            onNext={() => { setReview(null); setPicking(true); }} onExit={exit} />
                    ) : null}
                    {problem ? (
                        <div className="wt-winlayer__toast" role="alert">
                            <b>Голосовой тренажёр</b>
                            <span>{problem}</span>
                        </div>
                    ) : null}
                </div>,
                document.body,
            ) : null}
        </>
    );
}

export { enterFullscreen };
