import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { HelpCircle, RotateCcw, X } from 'lucide-react';

import { APPLE_FONT } from '../../ui/ios';
import SnowLeopard from './SnowLeopard';
import { StatusIcons, StatusTime } from './PhoneChrome';
import {
    currentStep, expectedTap, isFinished, progressPercent, restart, speech, stageCount,
    startRun, stepGoal, takeHint, tap, toggle,
} from './runner';
import { IntroScreen, ResultScreen } from './screenKit';
import { EgCode, EgSign, EgSuccess } from './screensEgov';
import {
    SpAd, SpProfile, SpSignAll, TpCheck, TpDocuments, TpHome,
} from './screensTaxiPro';
import {
    ChromeAddress, ChromeBlank, PhoneHome, SaparDocuments, SaparGuest, SaparProfile,
    SaparSave, SaparSignSheet, SaparStatus,
} from './screensSapar';
import './trainer.css';

/* Проигрыватель тренажёра: прогресс сверху, учебный телефон и барс рядом.
 *
 * Раскладка выбрана по требованию владельца и держится на одной мысли: главный
 * объект — телефон, помощник его КОММЕНТИРУЕТ. Поэтому третьей колонки с целями
 * и наградами (она была в исходных тренажёрах) здесь нет: цель — одна строка под
 * репликой барса, а «монет» и баллов нет вовсе. Тренажёр учит, а не начисляет.
 *
 * Экран собирается из двух источников: сценарий говорит, ЧТО показать
 * (screen-ключ шага), карта ниже — ЧЕМ показать. Разводить их нужно потому, что
 * сценарии чистые (их гоняют тесты без React), а экраны — JSX.
 */

/* Экраны по сценарию. Ключи совпадают со step.screen — расхождение здесь
   означало бы пустой телефон посреди урока, поэтому карта одна и рядом. */
const SCREENS = {
    'taxi-pro-avr': {
        intro: IntroScreen,
        tp_home: TpHome,
        tp_documents: TpDocuments,
        sp_ad: SpAd,
        sp_profile: SpProfile,
        sp_sign_all: SpSignAll,
        eg_sign: EgSign,
        eg_success: EgSuccess,
        tp_check: TpCheck,
        result: ResultScreen,
    },
    'sapar-site-avr': {
        intro: IntroScreen,
        phone_home: PhoneHome,
        chrome_blank: ChromeBlank,
        chrome_address: ChromeAddress,
        sapar_guest: SaparGuest,
        eg_code: EgCode,
        eg_sign: EgSign,
        eg_success: EgSuccess,
        sapar_profile: SaparProfile,
        sapar_documents: SaparDocuments,
        sapar_sign_sheet: SaparSignSheet,
        sapar_save: SaparSave,
        sapar_status: SaparStatus,
        result: ResultScreen,
    },
};

/* Выражение барса. Реплика-ошибка и реплика-подсказка выглядят по-разному —
   иначе неверное нажатие проходит незамеченным: текст сменился, а картинка нет. */
const MOOD = { idle: 'speak', error: 'error', hint: 'hint' };

/* Учебный код в реплике барса — красным и жирным.
 *
 * Его переписывают на клавиатуру телефона, и в сплошном тексте четыре цифры
 * теряются: человек читает реплику, не находит, что вводить, и жмёт наугад.
 * Подсвечиваем ТОЛЬКО настоящие коды текущей попытки, а не любое число: иначе
 * красным станет и «за Июль 2026». */
const withCodes = (text, codes) => {
    const values = Object.values(codes || {}).filter((v) => /^\d{4}$/.test(String(v)));
    if (!values.length) return text;
    const parts = String(text).split(new RegExp(`(${values.join('|')})`, 'g'));
    return parts.map((part, index) => (values.includes(part)
        ? <b key={`c${index}`} className="wt-code">{part}</b>
        : <React.Fragment key={`t${index}`}>{part}</React.Fragment>));
};

const HOURS = () => {
    const now = new Date();
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
};

/* Появление тренажёра разложено на такты, и порядок здесь смысловой:
 *
 *   'rise'  — телефон выезжает снизу поверх статьи. Фон ещё чистый: читатель
 *             видит, ОТКУДА взялся экран, и не теряет место в тексте.
 *   'peek'  — телефон доехал. Появляется полупрозрачная подложка, а из-за
 *             корпуса выглядывает ГОЛОВА барса — на той же высоте, где он потом
 *             будет сидеть в карточке. Пауза около секунды: на выглядывание надо
 *             успеть посмотреть, иначе оно не считывается вовсе.
 *   'cards' — из-за телефона выезжают карточки помощника и прогресса.
 *   'run'   — барс выходит из-за телефона и на четырёх лапах бежит к своему
 *             месту в карточке. Место не задано числом: оно измеряется по факту,
 *             поэтому бег заканчивается ровно там, где барс потом сидит.
 *   'done'  — всё на местах.
 *
 * Такты, а не один общий переход: когда всё выезжает разом, глазу не за чем
 * следить и появление читается как мигание. */
export function TrainerPlayer({
    scenario, onClose = null, animateEntrance = true, leaving = false, onExited = null,
}) {
    const [run, setRun] = useState(() => startRun(scenario));
    const phoneRef = useRef(null);
    const stages = useMemo(() => stageCount(scenario), [scenario]);
    const reduceMotion = useReducedMotion();
    /* На узком экране барс не выглядывает и не бежит: телефон занимает почти всю
       ширину, места для зверя слева просто нет — он вылезал бы за край окна.
       Там остаётся то, что работает: телефон выезжает, появляется подложка,
       следом карточки. Замеряем один раз при открытии — поворот устройства
       посреди анимации не тот случай, ради которого стоит усложнять. */
    const [narrow] = useState(() => (typeof window !== 'undefined'
        && window.matchMedia('(max-width: 1023px)').matches));
    const [phase, setPhase] = useState(
        animateEntrance && !reduceMotion ? 'rise' : 'done');

    // Выключили анимации в системе уже после открытия — доигрывать нечего.
    useEffect(() => { if (reduceMotion) setPhase('done'); }, [reduceMotion]);

    const shown = phase !== 'rise';
    const cardsOut = phase === 'cards' || phase === 'run' || phase === 'done';
    const running = phase === 'run';
    const settled = phase === 'done';

    /* Барс выглядывает и ждёт: без паузы голова мелькает за долю секунды, и
       вместо «подглядывает» получается «что-то дёрнулось». */
    useEffect(() => {
        if (phase !== 'peek' || leaving) return undefined;
        const timer = setTimeout(() => setPhase('cards'), narrow ? 120 : 1000);
        return () => clearTimeout(timer);
    }, [phase, narrow, leaving]);

    /* Маршрут пробежки считается по факту: откуда барс выглядывает и где стоит
       его место в карточке. Числами это не задать — карточка и телефон меняют
       размеры вместе с окном. */
    const runnerRef = useRef(null);
    const slotRef = useRef(null);
    const phoneWrapRef = useRef(null);
    const [runPath, setRunPath] = useState(null);
    useEffect(() => {
        if (phase !== 'run' || leaving) return;
        const from = runnerRef.current?.getBoundingClientRect();
        const to = slotRef.current?.getBoundingClientRect();
        // Не смогли измерить — не задерживаем человека: барс просто окажется на
        // месте, а тренажёр останется рабочим.
        if (!from || !to || !to.width) { setPhase('done'); return; }
        setRunPath({
            x: to.left + (to.width - from.width) / 2 - from.left,
            y: to.top + (to.height - from.height) / 2 - from.top,
            scale: Math.min(1.15, Math.max(0.6, to.width / from.width)),
        });
    }, [phase]);

    /* Появление и уход — РАЗНЫЕ кривые, и это не украшательство.
       EASE (та же, что у модалок портала) — быстрый старт и мягкое приземление:
       предмет влетает и встаёт на место. На уходе она даёт обратное: телефон
       сразу проваливался почти на весь экран, а последние полсотни пикселей полз
       ещё четверть секунды — «упал, завис и снова поехал». Уход должен
       РАЗГОНЯТЬСЯ: медленно тронулся, дальше быстрее, и предмет уносит вниз. */
    const EASE = [0.16, 1, 0.3, 1];
    const EASE_LEAVE = [0.4, 0, 1, 1];

    // Сценарий сменился (в списке тренажёров их два) — попытка начинается заново.
    useEffect(() => { setRun(startRun(scenario)); }, [scenario]);

    const step = currentStep(run);
    const target = expectedTap(run);
    const finished = isFinished(run);
    const said = speech(run);
    const percent = progressPercent(run);

    /* Функциональные обновления, а не tap(run, …): между нажатием и отрисовкой
       может прийти второе нажатие (двойной тап по кнопке телефона), и на
       замкнутом run второе применилось бы к устаревшему состоянию. */
    const doTap = useCallback((id, payload) => {
        setRun((prev) => tap(prev, id, payload).run);
    }, []);

    const doToggle = useCallback((key) => setRun((prev) => toggle(prev, key)), []);
    const doHint = useCallback(() => setRun((prev) => takeHint(prev)), []);
    const doRestart = useCallback(() => setRun((prev) => restart(prev)), []);

    /* Фокус переезжает на кнопку, которую ждут. Это и подсказка для мыши
       (кнопка подсвечена), и единственный способ пройти тренажёр с клавиатуры:
       иначе после каждого шага пришлось бы «дотабиваться» до нужной кнопки. */
    useEffect(() => {
        const node = phoneRef.current?.querySelector('.is-target:not([disabled])');
        if (node) {
            const frame = requestAnimationFrame(() => node.focus({ preventScroll: true }));
            return () => cancelAnimationFrame(frame);
        }
        return undefined;
    }, [run.index, run.world]);

    const Screen = SCREENS[scenario.key]?.[step.screen];
    const purpose = scenario.egovPurpose ? scenario.egovPurpose(step.key) : 'docs';

    return (
        /* Пока идёт анимация, прокрутка выключена.
           Телефон в этот момент стоит ниже экрана (translateY 110 %), а
           сдвинутый трансформом элемент увеличивает область прокрутки: полоса
           появлялась на время выезда и пропадала в конце, дёргая раскладку.
           После того как всё встало на места, прокрутка возвращается — на
           низком окне она нужна по-настоящему, иначе низ карточки прогресса
           было бы не достать. */
        <div
            className={`wt-root${settled && !leaving ? '' : ' wt-root--locked'}`}
            style={{ fontFamily: APPLE_FONT }}
        >
            {/* Полупрозрачная подложка. Появляется ПОСЛЕ того, как телефон
                доехал: пока он едет, статья под ним видна целиком, и переход
                читается как «экран поднялся поверх текста», а не как «страница
                моргнула». Сквозь подложку статья остаётся различима — тренажёр
                открыт из неё и туда же возвращает. */}
            <motion.div
                className="wt-veil"
                aria-hidden="true"
                initial={{ opacity: animateEntrance && !reduceMotion ? 0 : 1 }}
                animate={{ opacity: leaving ? 0 : (shown ? 1 : 0) }}
                transition={{
                    duration: reduceMotion ? 0 : (leaving ? 0.36 : 0.34),
                    ease: leaving ? 'easeIn' : 'easeOut',
                }}
            />

            {/* ── СЛЕВА: помощник ────────────────────────────────────────────
                Барс и его реплика занимают целую колонку, и текст здесь крупнее,
                чем в остальном портале: это не подпись к картинке, а то, ради
                чего экран открыт, — человек читает объяснение и идёт делать.
                Выезжает из-за телефона: карточка стартует сдвинутой ВПРАВО, к
                телефону, и уходит на своё место влево. */}
            <motion.aside
                className="wt-helper"
                initial={animateEntrance && !reduceMotion
                    ? { opacity: 0, x: 120, scale: 0.94 } : false}
                animate={leaving
                    ? { opacity: 0, x: 120, scale: 0.94 }
                    : (cardsOut ? { opacity: 1, x: 0, scale: 1 } : {})}
                transition={{
                    duration: leaving ? 0.24 : 0.46,
                    ease: leaving ? EASE_LEAVE : EASE,
                }}
                onAnimationComplete={() => {
                    if (!leaving) setPhase((p) => (p === 'cards' ? (narrow ? 'done' : 'run') : p));
                }}
            >
                {/* Место барса в карточке. Пока он бежит, слот пуст — иначе
                    зверь окажется в двух местах сразу. */}
                <div className="wt-helper__leo" ref={slotRef}
                    style={{ opacity: settled || leaving ? 1 : 0 }}>
                    <SnowLeopard state={finished ? 'success' : (MOOD[said.tone] || 'speak')} />
                </div>
                <div className={`wt-bubble wt-bubble--${said.tone}`}>
                    <span className="wt-bubble__who">Барс</span>
                    {/* aria-live: реплика меняется без перехода фокуса, и без
                        объявления человек со скринридером не узнаёт, что нажал
                        не туда. */}
                    <p aria-live="polite">{withCodes(said.text, run.world.codes)}</p>
                </div>

                {!finished && (
                    <div className="wt-goal">
                        <span>Сейчас</span>
                        <b>{stepGoal(run)}</b>
                    </div>
                )}

                <button type="button" className="wt-hint-btn" onClick={doHint} disabled={finished}>
                    <HelpCircle size={15} /> Подсказка
                </button>
            </motion.aside>

            {/* ── ЦЕНТР: учебный телефон ───────────────────────────────────
                Высота считается от окна, ширина — от пропорций корпуса, поэтому
                на большом экране телефон крупный, а на маленьком не вылезает. */}
            <div className="wt-stage">
                <div className="wt-phone-wrap" ref={phoneWrapRef}>
                    {/* ГОЛОВА барса из-за корпуса. Окно с обрезкой, а не весь
                        зверь целиком: выглядывать половиной туловища — это не
                        «подглядывает», это «стоит рядом». Высота окна выбрана
                        так, чтобы голова оказалась там же, где барс потом сидит
                        в карточке, — тогда пробежка читается как продолжение
                        одного движения, а не как телепорт. */}
                    {!narrow && (phase === 'peek' || phase === 'cards') && (
                        <motion.div
                            className="wt-peek"
                            aria-hidden="true"
                            initial={{ x: 46, opacity: 0 }}
                            animate={{ x: 0, opacity: 1 }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: 0.42, ease: [0.34, 1.4, 0.64, 1] }}
                        >
                            <SnowLeopard state="idle" />
                        </motion.div>
                    )}

                    {/* Тот же барс, но целиком: появляется ровно там, где была
                        голова, и убегает на своё место в карточке. */}
                    {running && (
                        <motion.div
                            className="wt-runner"
                            aria-hidden="true"
                            ref={runnerRef}
                            initial={{ opacity: 0, x: 0, y: 0, scale: 1 }}
                            animate={runPath
                                ? { opacity: 1, x: runPath.x, y: runPath.y, scale: runPath.scale }
                                : { opacity: 1 }}
                            transition={{
                                opacity: { duration: 0.16 },
                                default: { duration: 0.78, ease: [0.4, 0, 0.2, 1] },
                            }}
                            /* Проверяем, ЧТО именно доиграло: барс появляется в
                               два приёма — сначала проявляется на месте головы,
                               потом бежит. Без этой проверки «готово» срабатывало
                               на проявлении, и пробежка обрывалась через четверть
                               секунды в двадцати пикселях от старта. */
                            onAnimationComplete={(definition) => {
                                if (leaving) return;
                                if (definition && definition.x !== undefined) setPhase('done');
                            }}
                        >
                            <SnowLeopard state="run" />
                        </motion.div>
                    )}

                <motion.div
                    className="wt-phone"
                    ref={phoneRef}
                    data-screen={step.screen}
                    initial={animateEntrance && !reduceMotion ? { y: '110%' } : false}
                    animate={{ y: leaving ? '112%' : 0 }}
                    transition={{
                        duration: reduceMotion ? 0 : (leaving ? 0.44 : 0.62),
                        ease: leaving ? EASE_LEAVE : EASE,
                    }}
                    onAnimationComplete={() => {
                        if (leaving) onExited?.();
                        else setPhase((p) => (p === 'rise' ? 'peek' : p));
                    }}
                >
                    {/* Боковые клавиши и вырез — корпус должен читаться телефоном,
                        а не прямоугольником: учебный экран тем и работает, что
                        человек узнаёт в нём своё устройство. */}
                    <span className="wt-phone__side wt-phone__side--mute" aria-hidden="true" />
                    <span className="wt-phone__side wt-phone__side--vol-up" aria-hidden="true" />
                    <span className="wt-phone__side wt-phone__side--vol-down" aria-hidden="true" />
                    <span className="wt-phone__side wt-phone__side--power" aria-hidden="true" />

                    <div className="wt-phone__screen">
                        <div className="wt-phone__notch" aria-hidden="true">
                            <i className="wt-phone__speaker" />
                            <i className="wt-phone__cam" />
                        </div>
                        <div className="wt-phone__status" aria-hidden="true">
                            <StatusTime time={HOURS()} />
                            <StatusIcons battery={90} />
                        </div>
                        <div className="wt-phone__app">
                            {Screen ? (
                                <Screen
                                    /* key по шагу: экран кода встречается дважды, и без
                                       пересоздания во второй сессии остались бы цифры,
                                       набранные в первой. */
                                    key={step.key}
                                    scenario={scenario}
                                    world={run.world}
                                    tap={doTap}
                                    toggle={doToggle}
                                    target={target}
                                    purpose={purpose}
                                    period={run.world.period?.label}
                                    onRestart={doRestart}
                                />
                            ) : (
                                <div className="wt-screen"><p>Экран «{step.screen}» не найден.</p></div>
                            )}
                        </div>
                    </div>
                </motion.div>
                </div>
            </div>

            {/* ── СПРАВА: прогресс ─────────────────────────────────────────
                Полоса, доля и список шагов инструкции вместе: полоса отвечает
                «сколько осталось», список — «что именно осталось». В отдельной
                колонке список помещается целиком, поэтому прятать его больше
                не нужно. Выезжает из-за телефона зеркально помощнику. */}
            <motion.aside
                className="wt-side"
                initial={animateEntrance && !reduceMotion
                    ? { opacity: 0, x: -120, scale: 0.94 } : false}
                animate={leaving
                    ? { opacity: 0, x: -120, scale: 0.94 }
                    : (cardsOut ? { opacity: 1, x: 0, scale: 1 } : {})}
                transition={{
                    duration: leaving ? 0.24 : 0.46,
                    ease: leaving ? EASE_LEAVE : EASE,
                }}
            >
                <header className="wt-side__head">
                    <span>{scenario.subtitle}</span>
                    <strong>{scenario.title}</strong>
                </header>

                <div className="wt-side__progress">
                    <div className="wt-side__percent">
                        <b>{percent}</b><i>%</i>
                    </div>
                    <div className="wt-bar" role="progressbar" aria-valuenow={percent}
                        aria-valuemin={0} aria-valuemax={100}
                        aria-label={`Прогресс тренажёра: ${percent}%`}>
                        <i style={{ width: `${percent}%` }} />
                    </div>
                    <span>{finished ? 'Урок пройден' : `Шаг ${Math.max(1, step.stage)} из ${stages}`}</span>
                </div>

                <ol className="wt-steps">
                    {(scenario.checklist || []).map((item, index) => {
                        const number = index + 1;
                        const state = finished || step.stage > number ? 'is-done'
                            : (step.stage === number ? 'is-current' : '');
                        return (
                            <li key={`${index}-${item}`} className={state}>
                                <i aria-hidden="true">{finished || step.stage > number ? '✓' : number}</i>
                                {item}
                            </li>
                        );
                    })}
                </ol>

                <div className="wt-side__foot">
                    <span className="wt-counters">
                        Промахов: {run.errors} · подсказок: {run.hints}
                    </span>
                    <div className="wt-side__buttons">
                        <button type="button" className="wt-side__btn" onClick={doRestart}>
                            <RotateCcw size={14} /> Заново
                        </button>
                        {onClose && (
                            <button type="button" className="wt-side__btn wt-side__btn--close"
                                onClick={onClose}>
                                <X size={15} /> Закрыть
                            </button>
                        )}
                    </div>
                </div>
            </motion.aside>
        </div>
    );
}

/* Тренажёр во весь экран.
 *
 * Не модалка. Модальное окно с затемнением и карточкой годится для формы на
 * полтора поля, а здесь на экране одновременно живут телефон, помощник и
 * прогресс — карточка внутри окна отнимала бы у них высоту дважды (её поля
 * плюс поля страницы) и заставляла бы прокручивать то, что должно помещаться
 * целиком. Поэтому тренажёр занимает окно полностью и ведёт себя как отдельный
 * экран портала, а не как всплывающее окно поверх статьи.
 *
 * Портал в document.body остаётся: телефон не должен зависеть от ширины колонки
 * статьи, а внутри .wiki-prose у текста своя типографика, которая тут же начала
 * бы красить учебные экраны.
 */
export default function TrainerModal({ scenario, onClose }) {
    const reduceMotion = useReducedMotion();
    /* Закрытие тоже анимируется — тем же движением, что и открытие, только в
       обратную сторону: экран, который выехал снизу, обязан туда же и уехать.
       Поэтому onClose зовётся не сразу: сначала «уходим», потом размонтируемся. */
    const [leaving, setLeaving] = useState(false);
    const requestClose = useCallback(() => {
        if (reduceMotion) { onClose?.(); return; }
        setLeaving(true);
    }, [reduceMotion, onClose]);

    // Esc закрывает. Слушатель на документе, потому что фокус в момент нажатия
    // стоит на кнопке внутри учебного телефона.
    useEffect(() => {
        const onKey = (event) => { if (event.key === 'Escape') requestClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [requestClose]);

    /* Страховка: если анимация почему-то не доиграет (вкладку свернули, кадры не
       идут), окно всё равно закроется. Молча «не закрывается» — худший исход из
       возможных, а лишний таймер стоит ничего. */
    useEffect(() => {
        if (!leaving) return undefined;
        const timer = setTimeout(() => onClose?.(), 900);
        return () => clearTimeout(timer);
    }, [leaving, onClose]);

    /* Страница под тренажёром не прокручивается: экран занят целиком, и вторая
       полоса прокрутки за ним — это прокрутка «не того», как в режиме чтения во
       весь экран у статьи. */
    useEffect(() => {
        const previous = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => { document.body.style.overflow = previous; };
    }, []);

    if (!scenario) return null;

    return createPortal(
        <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={scenario.title}>
            <TrainerPlayer
                scenario={scenario}
                onClose={requestClose}
                leaving={leaving}
                onExited={() => onClose?.()}
            />
        </div>,
        document.body,
    );
}
