import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { HelpCircle, RotateCcw, X } from 'lucide-react';

import { APPLE_FONT } from '../../ui/ios';
import SnowLeopard from './SnowLeopard';
import useTrainerRun from './useTrainerRun';
import { StatusIcons, StatusTime } from './PhoneChrome';
import {
    browse, currentStep, expectedTap, isFinished, progressPercent, restart, speech,
    stageCount, startRun, stepGoal, takeHint, tap, toggle,
} from './runner';
import { IntroScreen, ResultScreen } from './screenKit';
import { DeskScreen } from './screensCrm';
import { callNext, emptyCall, syncReady, talkMs } from './callMachine';
import { EVENT_TITLES, createEventLog } from './trainerEvents';
import { applyEdit } from './caseData';
import { EgCode, EgSign, EgSuccess } from './screensEgov';
import {
    SpAd, SpProfile, SpSignAll, TpCheck, TpDocuments, TpHome,
} from './screensTaxiPro';
import {
    ChromeAddress, ChromeBlank, PhoneHome, SaparDocuments, SaparGuest, SaparProfile,
    SaparSave, SaparSignSheet, SaparStatus,
} from './screensSapar';
import {
    YpConsent, YpEdo, YpLegal, YpNews, YpProfile, YpProviders, YpSheet,
} from './screensYandexPro';
import './trainer.css';

/* Сцена с машиной приезжает отдельным чанком вместе с three.js.
 *
 * Обычным импортом библиотека попала бы в общий чанк тренажёров, и человек,
 * открывший урок про подписание документов, скачивал бы трёхмерный движок,
 * который там не нужен ни на одном шаге. */
const CarStage = lazy(() => import('./CarStage'));

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
    /* Единственный тренажёр не на телефоне, а за компьютером: оператор заводит
       обращение в браузере. Экран формы один на все шаги — меняется не он, а
       состав полей, и приходят они из мира (см. scenarioCrmTicket). */
    'crm-ticket-create': {
        intro: IntroScreen,
        desk: DeskScreen,
        result: ResultScreen,
    },
    /* Та же среда, но со звонком: экраны общие, разный только режим. */
    'operator-call': {
        intro: IntroScreen,
        desk: DeskScreen,
        result: ResultScreen,
    },
    'yandex-pro-edo-provider': {
        intro: IntroScreen,
        yp_news: YpNews,
        yp_profile: YpProfile,
        yp_legal: YpLegal,
        // Экран ЭДО один и тот же до смены и после: разница — строка активного
        // провайдера, по ней и проверяют результат.
        yp_edo: YpEdo,
        yp_providers: YpProviders,
        yp_sheet: YpSheet,
        yp_consent: YpConsent,
        result: ResultScreen,
    },
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
    'photo-control-car': {
        intro: IntroScreen,
        result: ResultScreen,
        // Экран pc_camera — не экран телефона, а трёхмерная сцена вокруг него,
        // поэтому он живёт не здесь, а в ветке world ниже.
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

/* Подпись состояния звонка в карточке прогресса. */
const CALL_TEXT = {
    offline: 'не на линии',
    ready: 'на линии, ждём',
    ringing: 'входящий',
    talking: 'разговор',
    held: 'на удержании',
    wrapup: 'постобработка',
    ended: 'завершён',
};

/* Закрыть попытку можно, только когда звонок уже позади: посреди разговора
   «Завершить» означало бы бросить водителя на линии. */
const CAN_FINISH = new Set(['wrapup', 'ended']);

/* Сколько шагов ленты кладём в итог попытки.
 *
 * Колонка result в базе режется по объёму, и слишком длинный итог там НЕ
 * обрезается, а отбрасывается целиком (wiki/trainers.py, _result_json) — то
 * есть карточка обращения потерялась бы вместе с лентой. Поэтому в итог едут
 * только коды, и не больше шестидесяти: полная лента живёт в памяти попытки и
 * уедет на сервер отдельной ручкой, когда та появится. */
const RESULT_EVENTS = 60;

const withEvents = (result, log) => {
    if (!result || !log) return result;
    const all = log.all();
    if (!all.length) return result;
    return {
        ...result,
        events_total: all.length,
        events: all.slice(-RESULT_EVENTS).map((item) => item.code),
    };
};

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
    record = null,
    /* Слепок дела. Не приехал — сценарий берёт запасной DEFAULT_CASE: тренажёр
       обязан открываться и без сервера. */
    caseData = null,
    /* Голос. null — панель звонка работает вручную, и тренажёр проходится
       целиком: это рабочий режим, а не заглушка. */
    voice = null,
    /* Наружу отдаём контроллер звонка: сюда сядет ИИ. Пропсом, не глобальной
       переменной. */
    onCallApi = null,
    /* Наружу — что сделал стажёр со звонком (answer | reject | hold | unhold |
       transfer | end). */
    onCall = null,
}) {
    const [run, setRun] = useState(() => startRun(scenario, { caseData }));
    const phoneRef = useRef(null);
    const stageRef = useRef(null);
    const stages = useMemo(() => stageCount(scenario), [scenario]);
    const reduceMotion = useReducedMotion();
    /* На узком экране барс не выглядывает и не бежит: телефон занимает почти всю
       ширину, места для зверя слева просто нет — он вылезал бы за край окна.
       Там остаётся то, что работает: телефон выезжает, появляется подложка,
       следом карточки. Замеряем один раз при открытии — поворот устройства
       посреди анимации не тот случай, ради которого стоит усложнять. */
    const [narrow] = useState(() => (typeof window !== 'undefined'
        && window.matchMedia('(max-width: 1023px)').matches));
    /* Тренажёр фотоконтроля устроен наоборот остальных: главный объект —
       машина, а телефон человек держит в руках. Поэтому телефон снизу не
       выезжает (ему неоткуда), и появление начинается сразу с карточек. */
    const worldMode = scenario.stage === 'world';
    /* Рабочее место оператора: вместо телефона окно браузера на компьютере.
       Оно широкое и стоит на месте, поэтому снизу тоже не выезжает — выезд из-за
       края экрана читается как жест телефона, а окно на компьютере так себя не
       ведёт. */
    const deskMode = scenario.stage === 'desktop';
    /* Свободная среда: шагов нет, значит нет ни прогресса, ни целей, ни
       подсказок. Показывать «шаг 1 из 1» и кнопку «Подсказка», за которой
       ничего не стоит, — обещать урок, которого не будет. */
    const sandbox = scenario.mode === 'sandbox';
    /* Режим смены: та же среда, но приходит звонок и попытка живёт до
       «Завершить попытку», а не до «Сохранить». */
    const callMode = scenario.mode === 'call';
    /* Отладочная кнопка «Позвонить» — только в dev-сборке: пока ИИ нет, звонок
       надо чем-то запускать, но в проде такой кнопки быть не должно. */
    const devMode = typeof import.meta !== 'undefined' && import.meta.env
        ? Boolean(import.meta.env.DEV) : false;
    /* Ни свободная среда, ни смена уроком не являются: шагов, целей и подсказок
       в них нет. Прогресс «50 %» и «шаг 1 из 1» там означали бы урок, которого
       не будет, поэтому на их месте — карта систем. */
    const noLesson = sandbox || callMode;
    const [phase, setPhase] = useState(() => {
        if (!animateEntrance || reduceMotion) return 'done';
        return worldMode || deskMode ? 'cards' : 'rise';
    });

    // Выключили анимации в системе уже после открытия — доигрывать нечего.
    useEffect(() => { if (reduceMotion) setPhase('done'); }, [reduceMotion]);

    /* Пробежка барса измеряется от корпуса телефона. В режиме мира корпуса на
       его прежнем месте нет, и бежать зверю неоткуда — он просто появляется в
       карточке, как на узком экране. */
    const skipRunner = narrow || worldMode || deskMode;
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

    /* Смена содержимого карточек — отдельная, НЕ такая же, как появление.
     *
     * Шаг меняет сразу три вещи: выражение барса, реплику и строку цели. Пока
     * они переключались мгновенно, глаз получал три вспышки подряд и не успевал
     * прочитать ни одну: человек нажимал, экран мигал, и приходилось искать
     * заново, что изменилось. Теперь всё три уходят и приходят одной кривой и
     * одной длительностью — смена читается как один плавный переход.
     *
     * Барсу нужен перекрёстный переход (старое настроение гаснет, новое
     * проявляется поверх), тексту — простое проявление на месте: два слоя
     * текста разной длины наложились бы друг на друга нечитаемой кашей. */
    const SWAP = reduceMotion ? 0 : 0.42;
    const SWAP_EASE = [0.32, 0.72, 0.28, 1];

    // Сценарий сменился (в списке тренажёров их два) — попытка начинается заново.
    /* Смена сценария начинает попытку заново. Слепок сюда обязателен: без него
       эффект отрабатывал на МОНТИРОВАНИИ и молча заменял переданное дело
       запасным — экраны показывали не того водителя, а тесты этого не видели,
       потому что зовут startRun напрямую. */
    useEffect(() => { setRun(startRun(scenario, { caseData })); }, [scenario, caseData]);

    /* Учёт попытки. Хук зовётся всегда (правило хуков), а молчит по флагу:
       без record проигрыватель работает ровно как раньше — так его открывает
       стенд и так он открылся бы, если раздел не отдал адрес и токен. */
    const runLog = useTrainerRun({
        base: record?.base,
        headers: record?.headers,
        trainerKey: scenario.key,
        stagesTotal: stages,
        articleId: record?.articleId ?? null,
        source: record?.source || 'article',
        enabled: !!record?.base,
        /* Сценарий сам решает, писать ли брошенные попытки. Там, где итог —
           сделанная работа, а не пройденный путь, писать до конца нечего. */
        finishedOnly: !!scenario.recordOnFinishOnly,
    });

    const step = currentStep(run);
    const target = expectedTap(run);
    const finished = isFinished(run);
    const said = speech(run);
    const percent = progressPercent(run);
    const mood = finished ? 'success' : (MOOD[said.tone] || 'speak');
    const goal = stepGoal(run);

    /* Функциональные обновления, а не tap(run, …): между нажатием и отрисовкой
       может прийти второе нажатие (двойной тап по кнопке телефона), и на
       замкнутом run второе применилось бы к устаревшему состоянию. */
    const doTap = useCallback((id, payload) => {
        setRun((prev) => tap(prev, id, payload).run);
    }, []);

    const doToggle = useCallback((key) => setRun((prev) => toggle(prev, key)), []);
    /* Свободное перемещение по учебной среде: вкладка браузера, раздел соседнего
       кабинета. Не ход и не промах — см. runner.browse. */
    const doBrowse = useCallback((patch) => setRun((prev) => browse(prev, patch)), []);

    /* ── Лента событий интерфейса ─────────────────────────────────────────
       Разбор говорит стажёру «ты не посмотрел Ведомость, а ответ лежал там» —
       без ленты такой фразы не получится. Сессии пока нет: лента копится в
       памяти и уезжает вместе с итогом попытки. */
    const logRef = useRef(null);
    if (!logRef.current) logRef.current = createEventLog();
    /* Счётчик нужен ТОЛЬКО отладочной панели: без него она не перерисуется.
       В проде панели нет, и лишних рендеров тоже. */
    const [logTick, setLogTick] = useState(0);
    const doEmit = useCallback((code, payload) => {
        const added = logRef.current.emit(code, payload);
        if (added && devMode) setLogTick((n) => n + 1);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [devMode]);

    /* ── Действия, меняющие данные ────────────────────────────────────────
       Половина ошибок новичка — не «не нашёл», а «полез менять то, что менять
       нельзя». Поэтому такие кнопки в среде есть, но правят они ТОЛЬКО копию
       слепка внутри попытки и всегда оставляют след в ленте. Наружу как данные
       не уходят никогда. */
    const doAct = useCallback((what, args = {}) => {
        doEmit('ui.action', { what, args });
        setRun((prev) => browse(prev, {
            // Правка ложится на КОПИЮ слепка: экран сразу показывает новый
            // баланс, а исходное дело остаётся нетронутым.
            case: applyEdit(prev.world.case, what, args),
            edits: [...(prev.world.edits || []), { what, args }],
        }));
    }, [doEmit]);

    /* ── Звонок ───────────────────────────────────────────────────────────
       Автомат живёт в callMachine (чистый и покрыт тестами), здесь только
       проводка: применить переход, разложить его события в ленту и сообщить
       наружу — туда, где будет ИИ. */
    const applyCall = useCallback((action, payload = {}) => {
        setRun((prev) => {
            const result = callNext(prev.world.call || emptyCall(), action, payload);
            result.events.forEach(([code, data]) => logRef.current.emit(code, data));
            return browse(prev, { call: result.call });
        });
    }, []);

    /* Сохранение обращения — НЕ шаг движка.
     *
     * В свободной среде «Сохранить» заодно заканчивает попытку, а в режиме
     * смены после звонка идёт постобработка, и попытку закрывает стажёр. Пока
     * сохранение было ходом, в режиме смены оно засчитывалось промахом:
     * ожидалось «Завершить попытку», а пришло «save» — карточка не
     * записывалась, а счётчик ошибок рос на ровном месте.
     *
     * Поэтому: пишем карточку и событие всегда, а шаг двигаем только там, где
     * сохранение действительно является шагом. */
    const doSave = useCallback(() => {
        const form = runRef.current?.world?.form || {};
        doEmit('crm.save', { form });
        setRun((prev) => browse(prev, { saved: true }));
        if (expectedTap(runRef.current) === 'save') doTap('save');
    }, [doEmit, doTap]);

    const handleCall = useCallback((type, payload = {}) => {
        applyCall(type, payload);
        if (onCall) onCall(type, payload);
    }, [applyCall, onCall]);

    /* Линия гаснет и загорается вслед за Okapp: не вошёл в call-центр —
       звонков нет, поставил перерыв — звонков нет. Надпись об этом на экране
       клиента была и раньше; теперь это правда. */
    useEffect(() => {
        if (!callMode) return;
        setRun((prev) => {
            const next = syncReady(prev.world.call || emptyCall(), prev.world);
            return next === prev.world.call ? prev : browse(prev, { call: next });
        });
    }, [callMode, run.world.oktLogged, run.world.oktIn, run.world.oktStatus]);

    /* Входящий приходит сам через 5–20 секунд после того, как стажёр встал на
       линию. Разброс намеренный: с фиксированной паузой человек начинает ждать
       секундомер, а не работу. */
    const callState = run.world.call?.state;
    useEffect(() => {
        if (!callMode || callState !== 'ready') return undefined;
        const wait = 5000 + Math.floor(Math.random() * 15000);
        const id = setTimeout(() => {
            const call = run.world.case.call || {};
            applyCall('ring', {
                phone: call.phone_pretty || call.phone || '',
                queue: call.queue || '',
                waitedSec: call.waited_sec || 0,
            });
        }, wait);
        return () => clearTimeout(id);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [callMode, callState]);

    /* Контроллер наружу — место, куда сядет ИИ. Отдаём один раз: пересоздание
       объекта заставило бы ту сторону переподписываться на каждый рендер. */
    const runRef = useRef(run);
    runRef.current = run;

    const callApiRef = useRef(null);
    if (!callApiRef.current) {
        callApiRef.current = {
            ring: (caller = {}) => applyCall('ring', caller),
            answered: () => applyCall('answer'),
            hangup: (reason = 'driver') => applyCall('end', { by: reason }),
            state: () => (runRef.current?.world?.call?.state || 'offline'),
        };
    }
    useEffect(() => {
        if (onCallApi) onCallApi(callApiRef.current);
    }, [onCallApi]);

    /* ── Голос ────────────────────────────────────────────────────────────
       Своего голосового слоя здесь нет и не будет: он уже написан и живёт
       отдельно. Наша часть — оставить ему место и не мешать. */
    const [aiSpeaking, setAiSpeaking] = useState(false);
    const [micLevel, setMicLevel] = useState(0);
    const [micError, setMicError] = useState('');

    useEffect(() => {
        if (!voice) return undefined;
        const handler = (type, payload) => {
            if (type === 'speech') setAiSpeaking(Boolean(payload?.text));
            if (type === 'speech_end') setAiSpeaking(false);
            if (type === 'level') setMicLevel(Number(payload?.level) || 0);
        };
        if (typeof voice.subscribe === 'function') return voice.subscribe(handler);
        // eslint-disable-next-line no-param-reassign
        voice.onEvent = handler;
        return () => { /* отписка не требуется */ };
    }, [voice]);

    /* Разрешение на микрофон спрашиваем ДО звонка, а не в момент «Ответить»:
       окно браузера поверх плашки вызова — верный способ пропустить звонок.
       Без голоса микрофон не нужен вовсе. */
    useEffect(() => {
        if (!callMode || !voice || callState !== 'ready') return;
        if (typeof navigator === 'undefined' || !navigator.mediaDevices) return;
        navigator.mediaDevices.getUserMedia({ audio: true })
            .then((stream) => { stream.getTracks().forEach((t) => t.stop()); setMicError(''); })
            .catch(() => setMicError(
                'Микрофон недоступен: браузер не дал разрешение. Разговор можно провести '
                + 'без голоса — кнопки панели звонка работают.',
            ));
    }, [callMode, voice, callState]);

    /* Микрофон слушает комнату только со снятой трубкой. */
    useEffect(() => {
        if (!voice) return undefined;
        if (callState === 'talking' || callState === 'held') {
            try { voice.start?.(); } catch { /* голос не мешает уроку */ }
            return () => { try { voice.stop?.(); } catch { /* голос не мешает уроку */ } };
        }
        return undefined;
    }, [voice, callState]);

    const doHint = useCallback(() => setRun((prev) => takeHint(prev)), []);
    const doRestart = useCallback(() => {
        runLog.restart();
        // «Заново» — та же попытка с тем же делом, а не с запасным.
        setRun((prev) => restart(prev, { caseData }));
    }, [runLog, caseData]);

    /* Фокус переезжает на кнопку, которую ждут. Это и подсказка для мыши
       (кнопка подсвечена), и единственный способ пройти тренажёр с клавиатуры:
       иначе после каждого шага пришлось бы «дотабиваться» до нужной кнопки.
     *
     * Две ситуации, когда фокус трогать НЕЛЬЗЯ:
     *
     *   свободная среда — «ожидаемой кнопки» там нет вовсе: цель одна на весь
     *   стенд («Сохранить»), и перевод фокуса на неё случался бы постоянно;
     *
     *   человек печатает — мир меняется на КАЖДЫЙ набранный символ, и эффект
     *   выдёргивал курсор из поля после первой же буквы. Дальше пробел уходил
     *   уже в кнопку и «нажимал» её: обращение сохранялось само, с телефоном
     *   из одной цифры. */
    useEffect(() => {
        if (sandbox) return undefined;
        const focused = typeof document !== 'undefined' ? document.activeElement : null;
        if (focused && (focused.tagName === 'INPUT' || focused.tagName === 'TEXTAREA')) {
            return undefined;
        }
        const node = stageRef.current?.querySelector('.is-target:not([disabled])');
        if (node) {
            const frame = requestAnimationFrame(() => node.focus({ preventScroll: true }));
            return () => cancelAnimationFrame(frame);
        }
        return undefined;
    }, [sandbox, run.index, run.world]);

    /* Докуда дошли — в учёт, на каждом шаге. Это присваивание в ref, а не
       запрос: отправка одна, при закрытии урока. Эффект, а не вызов в doTap,
       потому что счётчики меняет ещё и «Подсказка», и restart. */
    useEffect(() => {
        runLog.track({
            done: Math.max(0, Number(step.stage) || 0),
            total: stages,
            errors: run.errors,
            hints: run.hints,
            /* ЧТО человек сделал — итог урока. Собирает его сценарий: только он
               знает, что в его мире является результатом. У прогулки по
               инструкции результата нет, и здесь будет null.

               Сверху докладываем ленту действий: без неё разбор видит, ЧТО
               человек завёл, но не видит, куда он смотрел по дороге. */
            result: withEvents(
                scenario.result ? scenario.result(run.world) : null,
                logRef.current,
            ),
        });
    }, [runLog, scenario, step.stage, stages, run.errors, run.hints, run.world]);

    /* Дошёл до финального шага — попытка засчитана сразу, не дожидаясь, пока
       человек закроет окно. Иначе прошедший и закрывший вкладку не отличался бы
       от бросившего на середине. */
    useEffect(() => {
        if (finished) {
            // Лента обязана уйти на завершении: дальше попытки уже нет.
            logRef.current.flush();
            runLog.close('finished');
        }
    }, [finished, runLog]);

    // Закрытие проигрывателя: то, что не досчиталось, уходит как брошенное.
    useEffect(() => () => runLog.close('abandoned'), [runLog]);

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
            className={`wt-root${settled && !leaving ? '' : ' wt-root--locked'}`
                + `${worldMode ? ' wt-root--world' : ''}`
                + `${deskMode ? ' wt-root--desk' : ''}`
                + `${noLesson ? ' wt-root--sandbox' : ''}`}
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
                    if (!leaving) setPhase((p) => (p === 'cards' ? (skipRunner ? 'done' : 'run') : p));
                }}
            >
                {/* Место барса в карточке. Пока он бежит, слот пуст — иначе
                    зверь окажется в двух местах сразу. */}
                <div className="wt-helper__leo" ref={slotRef}
                    style={{ opacity: settled || leaving ? 1 : 0 }}>
                    {/* Перекрёстный переход настроения. Слой абсолютный, размер
                        держит сам слот — иначе на время перехода в колонке стояли
                        бы два барса друг под другом, а замер пробежки (slotRef)
                        уехал бы вместе с высотой. */}
                    <AnimatePresence initial={false}>
                        <motion.span
                            key={mood}
                            className="wt-helper__leo-layer"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: SWAP, ease: 'easeInOut' }}
                        >
                            <SnowLeopard state={mood} />
                        </motion.span>
                    </AnimatePresence>
                </div>
                <div className={`wt-bubble wt-bubble--${said.tone}`}>
                    <span className="wt-bubble__who">Барс</span>
                    {/* aria-live остаётся на постоянном узле: перевесить его на
                        сменяемый абзац — значит потерять объявление, потому что
                        скринридер следит за ИЗМЕНЕНИЯМИ внутри области, а не за
                        её заменой. */}
                    <p aria-live="polite">
                        {/* key по тексту: новая реплика проявляется на месте.
                            Уход не анимируем — старый абзац исчезает мгновенно,
                            а новый уже занимает его место прозрачным, поэтому
                            карточка меняет высоту один раз, а не дважды. */}
                        <motion.span
                            key={said.text}
                            className="wt-bubble__text"
                            initial={{ opacity: 0, y: 5 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ duration: SWAP, ease: SWAP_EASE }}
                        >
                            {withCodes(said.text, run.world.codes)}
                        </motion.span>
                    </p>
                </div>

                {!finished && !noLesson && (
                    <div className="wt-goal">
                        <span>Сейчас</span>
                        <motion.b
                            key={goal}
                            initial={{ opacity: 0, y: 5 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ duration: SWAP, ease: SWAP_EASE, delay: SWAP * 0.25 }}
                        >
                            {goal}
                        </motion.b>
                    </div>
                )}

                {/* Подсказка — часть урока. В свободной среде подсказывать
                    нечего: правильного следующего действия там нет. */}
                {noLesson ? null : (
                    <button type="button" className="wt-hint-btn" onClick={doHint} disabled={finished}>
                        <HelpCircle size={15} /> Подсказка
                    </button>
                )}
            </motion.aside>

            {/* Лента действий. Сервера у неё пока нет — она копится в памяти и
                видна здесь, чтобы разбор можно было писать уже сейчас. Только
                в dev-сборке: пользователю она не нужна. */}
            {devMode && logTick >= 0 && noLesson ? (
                <aside className="wt-devlog" aria-label="Лента событий (отладка)">
                    <header>
                        Лента событий · {logRef.current.count()}
                        <button type="button" onClick={() => setLogTick((n) => n + 1)}>обновить</button>
                    </header>
                    <ol>
                        {logRef.current.all().slice(-40).reverse().map((item, index) => (
                            <li key={`${item.at}-${index}`}>
                                <code>{item.code}</code>
                                <small>{EVENT_TITLES[item.code] || ''}</small>
                            </li>
                        ))}
                    </ol>
                </aside>
            ) : null}

            {/* ── ЦЕНТР: учебный телефон ───────────────────────────────────
                Высота считается от окна, ширина — от пропорций корпуса, поэтому
                на большом экране телефон крупный, а на маленьком не вылезает. */}
            <div className={`wt-stage${worldMode ? ' wt-stage--world' : ''}`
                + `${deskMode ? ' wt-stage--desk' : ''}`} ref={stageRef}>
                {worldMode || deskMode ? (
                    /* Мир вокруг человека и рабочее место оператора. Вступление
                       и финал в обоих режимах — обычные карточки: показывать их
                       «в телефоне» незачем, телефон здесь часть сцены (или его
                       нет вовсе), а не рамка для любого текста. */
                    step.screen === 'pc_camera' ? (
                        <Suspense fallback={<div className="wt-world__load">Готовим машину…</div>}>
                            <CarStage
                                world={run.world}
                                tap={doTap}
                                toggle={doToggle}
                                browse={doBrowse}
                                target={target}
                                plate={run.world.car?.plate}
                            />
                        </Suspense>
                    ) : deskMode && step.screen === 'desk' ? (
                        /* Рабочий стол оператора: софтфон с карточкой звонка и
                           окно браузера с CRM. Карточка звонка обязана стоять
                           РЯДОМ с формой, а не в реплике помощника: на смене
                           оператор смотрит на неё, пока заполняет поля, и
                           тренажёр повторяет это движение глаз. */
                        <div className="wt-desk">
                            <div className="wt-desk__window">
                                {Screen && (
                                    <Screen
                                        key={step.key}
                                        scenario={scenario}
                                        world={run.world}
                                        tap={doTap}
                                        toggle={doToggle}
                                        browse={doBrowse}
                                        emit={doEmit}
                                        act={doAct}
                                        onSave={doSave}
                                        target={target}
                                        onRestart={doRestart}
                                        onCall={callMode ? handleCall : null}
                                        voice={voice}
                                        aiSpeaking={aiSpeaking}
                                        micLevel={micLevel}
                                        micError={micError}
                                        devMode={devMode}
                                        onRing={callMode && devMode
                                            ? () => callApiRef.current.ring({
                                                phone: run.world.case.call.phone_pretty
                                                    || run.world.case.call.phone,
                                                queue: run.world.case.call.queue,
                                                waitedSec: run.world.case.call.waited_sec,
                                            })
                                            : null}
                                    />
                                )}
                            </div>
                        </div>
                    ) : (
                        <div className="wt-card">
                            {Screen && (
                                <Screen
                                    key={step.key}
                                    scenario={scenario}
                                    world={run.world}
                                    tap={doTap}
                                    toggle={doToggle}
                                    browse={doBrowse}
                                    target={target}
                                    onRestart={doRestart}
                                />
                            )}
                        </div>
                    )
                ) : (
                <div className="wt-phone-wrap" ref={phoneWrapRef}>
                    {/* ГОЛОВА барса из-за корпуса. Окно с обрезкой, а не весь
                        зверь целиком: выглядывать половиной туловища — это не
                        «подглядывает», это «стоит рядом». Высота окна выбрана
                        так, чтобы голова оказалась там же, где барс потом сидит
                        в карточке, — тогда пробежка читается как продолжение
                        одного движения, а не как телепорт. */}
                    {!skipRunner && (phase === 'peek' || phase === 'cards') && (
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
                                    browse={doBrowse}
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
                )}
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

                {noLesson ? (
                    /* В свободной среде на месте прогресса — что где лежит.
                       Это не шаги: порядок не обязателен, отметок «пройдено» нет. */
                    <ul className="wt-side__map">
                        {(scenario.checklist || []).map((item) => {
                            const [where, what] = String(item).split(' — ');
                            return (
                                <li key={item}>
                                    <b>{where}</b>
                                    {what ? <span>{what}</span> : null}
                                </li>
                            );
                        })}
                    </ul>
                ) : (
                    <>
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
                    </>
                )}

                <div className="wt-side__foot">
                    {/* Промахи и подсказки — счётчики урока. В свободной среде
                        промахнуться не по чему, и нули там только сбивают. */}
                    {callMode ? (
                        <span className="wt-counters">
                            {`Звонок: ${CALL_TEXT[run.world.call?.state] || '—'}`}
                            {run.world.saved ? ' · обращение оформлено' : ''}
                        </span>
                    ) : sandbox ? (
                        <span className="wt-counters">
                            {run.world.saved ? 'Обращение сохранено' : 'Обращение ещё не сохранено'}
                        </span>
                    ) : (
                        <span className="wt-counters">
                            Промахов: {run.errors} · подсказок: {run.hints}
                        </span>
                    )}
                    <div className="wt-side__buttons">
                        {/* Попытку закрывает стажёр, а не «Сохранить»: после
                            разговора остаётся постобработка, и обрывать её
                            автоматически значит не дать её сделать. */}
                        {callMode && !finished ? (
                            <button
                                type="button"
                                className="wt-side__btn wt-side__btn--finish"
                                disabled={!CAN_FINISH.has(run.world.call?.state)}
                                onClick={() => { applyCall('finish'); doTap('finish_attempt'); }}
                            >
                                Завершить попытку
                            </button>
                        ) : null}
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
export default function TrainerModal({
    scenario, onClose, record = null,
    caseData = null, voice = null, onCallApi = null, onCall = null,
}) {
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
                record={record}
                caseData={caseData}
                voice={voice}
                onCallApi={onCallApi}
                onCall={onCall}
            />
        </div>,
        document.body,
    );
}
