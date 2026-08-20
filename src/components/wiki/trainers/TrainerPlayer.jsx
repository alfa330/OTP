import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { HelpCircle, RotateCcw, X } from 'lucide-react';

import { APPLE_FONT, IOS_MODAL_MOTION, IOS_MODAL_MOTION_REDUCED } from '../../ui/ios';
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

export function TrainerPlayer({ scenario, onClose = null, compact = false }) {
    const [run, setRun] = useState(() => startRun(scenario));
    const [showSteps, setShowSteps] = useState(false);
    const phoneRef = useRef(null);
    const stages = useMemo(() => stageCount(scenario), [scenario]);

    // Сценарий сменился (в списке тренажёров их два) — попытка начинается заново.
    useEffect(() => { setRun(startRun(scenario)); setShowSteps(false); }, [scenario]);

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
        <div className={`wt-root${compact ? ' wt-root--compact' : ''}`} style={{ fontFamily: APPLE_FONT }}>
            {/* ── Прогресс сверху ────────────────────────────────────────────
                Полоса и «шаг N из M» вместе: полоса отвечает «сколько осталось»,
                число — «где я по инструкции». Одного из двух не хватает: полоса
                без числа не соотносится с текстом статьи, число без полосы не
                показывает, что до конца недалеко. */}
            <header className="wt-top">
                <div className="wt-top__title">
                    <span>{scenario.subtitle}</span>
                    <strong>{scenario.title}</strong>
                </div>
                <div className="wt-top__progress">
                    <div className="wt-bar" role="progressbar" aria-valuenow={percent}
                        aria-valuemin={0} aria-valuemax={100}
                        aria-label={`Прогресс тренажёра: ${percent}%`}>
                        <i style={{ width: `${percent}%` }} />
                    </div>
                    <b>{finished ? 'Готово' : `Шаг ${Math.max(1, step.stage)} из ${stages}`}</b>
                </div>
                <div className="wt-top__actions">
                    <button type="button" className="wt-top__btn" onClick={doRestart}>
                        <RotateCcw size={14} /> Заново
                    </button>
                    {onClose && (
                        <button type="button" className="wt-top__btn wt-top__btn--icon"
                            onClick={onClose} aria-label="Закрыть тренажёр">
                            <X size={16} />
                        </button>
                    )}
                </div>
            </header>

            <div className="wt-stage">
                {/* ── Телефон ─────────────────────────────────────────────── */}
                <div className="wt-phone" ref={phoneRef} data-screen={step.screen}>
                    <div className="wt-phone__notch" aria-hidden="true" />
                    <div className="wt-phone__status" aria-hidden="true">
                        <span>{HOURS()}</span>
                        <span>LTE ▮</span>
                    </div>
                    <div className="wt-phone__screen">
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
                    <div className="wt-phone__home" aria-hidden="true" />
                </div>

                {/* ── Помощник ────────────────────────────────────────────── */}
                <aside className="wt-helper">
                    {/* Барс и его реплика — одна группа: на узком экране они
                        встают в строку (барс слева, реплика справа), и это не
                        косметика. На телефоне помощник обязан быть ВЫШЕ учебного
                        экрана: инструкцию читают до действия, а не после того,
                        как пролистали телефон до конца. */}
                    <div className="wt-helper__say">
                        <div className="wt-helper__leo">
                            <SnowLeopard state={finished ? 'success' : (MOOD[said.tone] || 'speak')} />
                        </div>
                        <div className={`wt-bubble wt-bubble--${said.tone}`}>
                            <span className="wt-bubble__who">Барс</span>
                            {/* aria-live: реплика меняется без перехода фокуса, и без
                                объявления человек со скринридером не узнаёт, что
                                нажал не туда. */}
                            <p aria-live="polite">{said.text}</p>
                        </div>
                    </div>

                    {!finished && (
                        <div className="wt-goal">
                            <span>Сейчас</span>
                            <b>{stepGoal(run)}</b>
                        </div>
                    )}

                    <div className="wt-helper__foot">
                        <button type="button" className="wt-hint-btn" onClick={doHint}
                            disabled={finished}>
                            <HelpCircle size={14} /> Подсказка
                        </button>
                        <span className="wt-counters">
                            Промахов: {run.errors} · подсказок: {run.hints}
                        </span>
                    </div>

                    {/* Полный список шагов спрятан: развёрнутым он превращается в
                        вечную колонку текста рядом с экраном, а нужен один раз —
                        когда хочется понять, сколько ещё впереди. */}
                    <button type="button" className="wt-steps__toggle"
                        onClick={() => setShowSteps((v) => !v)} aria-expanded={showSteps}>
                        {showSteps ? 'Скрыть шаги инструкции' : `Все шаги инструкции (${stages})`}
                    </button>
                    {showSteps && (
                        <ol className="wt-steps">
                            {(scenario.checklist || []).map((item, index) => {
                                const number = index + 1;
                                const state = finished || step.stage > number ? 'is-done'
                                    : (step.stage === number ? 'is-current' : '');
                                return <li key={`${index}-${item}`} className={state}>{item}</li>;
                            })}
                        </ol>
                    )}
                </aside>
            </div>
        </div>
    );
}

/* Тренажёр во весь экран.
 *
 * Портал в document.body, а не div рядом со статьёй: телефон и барс не должны
 * зависеть от ширины колонки статьи, а внутри .wiki-prose у текста свои отступы
 * и типографика, которые тут же начали бы красить учебные экраны.
 */
export default function TrainerModal({ scenario, onClose }) {
    const reduceMotion = useReducedMotion();
    const motions = reduceMotion ? IOS_MODAL_MOTION_REDUCED : IOS_MODAL_MOTION;

    // Esc закрывает. Слушатель на документе, потому что фокус в момент нажатия
    // стоит на кнопке внутри учебного телефона.
    useEffect(() => {
        const onKey = (event) => { if (event.key === 'Escape') onClose?.(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    if (!scenario) return null;

    return createPortal(
        <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={scenario.title}>
            <motion.div className="wt-overlay__backdrop" {...motions.backdrop}
                onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.(); }} />
            <motion.div className="wt-overlay__panel" {...motions.panel}>
                <TrainerPlayer scenario={scenario} onClose={onClose} />
            </motion.div>
        </div>,
        document.body,
    );
}
