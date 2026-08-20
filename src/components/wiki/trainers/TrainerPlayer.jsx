import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { HelpCircle, RotateCcw, X } from 'lucide-react';

import { APPLE_FONT } from '../../ui/ios';
import SnowLeopard from './SnowLeopard';
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

const HOURS = () => {
    const now = new Date();
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
};

export function TrainerPlayer({ scenario, onClose = null }) {
    const [run, setRun] = useState(() => startRun(scenario));
    const phoneRef = useRef(null);
    const stages = useMemo(() => stageCount(scenario), [scenario]);

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
        <div className="wt-root" style={{ fontFamily: APPLE_FONT }}>
            {/* ── СЛЕВА: помощник ────────────────────────────────────────────
                Барс и его реплика занимают целую колонку, и текст здесь крупнее,
                чем в остальном портале: это не подпись к картинке, а то, ради
                чего экран открыт, — человек читает объяснение и идёт делать. */}
            <aside className="wt-helper">
                <div className="wt-helper__leo">
                    <SnowLeopard state={finished ? 'success' : (MOOD[said.tone] || 'speak')} />
                </div>
                <div className={`wt-bubble wt-bubble--${said.tone}`}>
                    <span className="wt-bubble__who">Барс</span>
                    {/* aria-live: реплика меняется без перехода фокуса, и без
                        объявления человек со скринридером не узнаёт, что нажал
                        не туда. */}
                    <p aria-live="polite">{said.text}</p>
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
            </aside>

            {/* ── ЦЕНТР: учебный телефон ───────────────────────────────────
                Высота считается от окна, ширина — от пропорций корпуса, поэтому
                на большом экране телефон крупный, а на маленьком не вылезает. */}
            <div className="wt-stage">
                <div className="wt-phone" ref={phoneRef} data-screen={step.screen}>
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
                            <span>{HOURS()}</span>
                            <span>LTE ▮</span>
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
                </div>
            </div>

            {/* ── СПРАВА: прогресс ─────────────────────────────────────────
                Полоса, доля и список шагов инструкции вместе: полоса отвечает
                «сколько осталось», список — «что именно осталось». В отдельной
                колонке список помещается целиком, поэтому прятать его больше
                не нужно. */}
            <aside className="wt-side">
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
            </aside>
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

    // Esc закрывает. Слушатель на документе, потому что фокус в момент нажатия
    // стоит на кнопке внутри учебного телефона.
    useEffect(() => {
        const onKey = (event) => { if (event.key === 'Escape') onClose?.(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

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
        <motion.div
            className="wt-overlay"
            role="dialog"
            aria-modal="true"
            aria-label={scenario.title}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: reduceMotion ? 0 : 0.18, ease: 'easeOut' }}
        >
            <TrainerPlayer scenario={scenario} onClose={onClose} />
        </motion.div>,
        document.body,
    );
}
