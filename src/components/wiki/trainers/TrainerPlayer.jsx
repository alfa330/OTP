import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import lazyWithRetry from '../../../utils/lazyWithRetry';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { HelpCircle, RotateCcw, X } from 'lucide-react';

import { APPLE_FONT } from '../../ui/ios';
import SnowLeopard from './SnowLeopard';
import useTrainerRun from './useTrainerRun';
import { StatusIcons, StatusTime } from './PhoneChrome';
import {
    blockOfStage, browse, checklistFor, currentBlock, currentStep, expectedTap, isFinished,
    progressPercent, restart, restartBlock, speech, stageCount, stagesOf, startRun, stepGoal, takeHint,
    tap, toggle, uiAction, uiMisses,
} from './runner';
import { callNext, emptyCall, syncReady, talkMs } from './callMachine';
import { EVENT_TITLES, createEventLog } from './trainerEvents';
import { SCREENS } from './screenMap';
import { chooseCorner, measureFloat } from './floatCorner';
import WinBuddy, { tipForCall } from './WinBuddy';
import './trainer.css';

/* Сцена с машиной приезжает отдельным чанком вместе с three.js.
 *
 * Обычным импортом библиотека попала бы в общий чанк тренажёров, и человек,
 * открывший урок про подписание документов, скачивал бы трёхмерный движок,
 * который там не нужен ни на одном шаге. */
const CarStage = lazyWithRetry(() => import('./CarStage'));

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
    /* Урок дошёл до финального шага. Нужен новости с обязательным тренажёром
       (задача #342): окно отмечает прохождение и отпускает кнопку «Прочитал».
       Держится в ref — новая функция из родителя на каждом рендере не должна
       перезапускать эффект завершения. */
    onFinished = null,
    /* Среда рабочего места. 'windows' — рабочий стол Windows на весь экран:
       браузер с CRM, iCORE Phone, панель задач (раздел «Тренажёр», звонок
       ИИ-водителя; владелец, 25.11.2026). Без Барса и плашки прогресса: это
       смена на настоящем компьютере, а не урок. */
    environment = null,
    /* Меню «Учебная смена» на панели задач: [{ label, onClick, danger }]. */
    desktopMenu = null,
}) {
    const [run, setRun] = useState(() => startRun(scenario, { caseData }));
    const onFinishedRef = useRef(onFinished);
    onFinishedRef.current = onFinished;
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
    const windowsMode = deskMode && environment === 'windows';
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
    /* Рабочее место (владелец, 23.11.2026: «более плавные анимации открытия
       и закрытия»): окно проявляется с лёгким приближением, как окно macOS, и
       так же уходит; за ним — плашка прогресса и Барс, с небольшой лесенкой.
       Длительность ухода близка к приходу: короткий уход читался как рывок. */
    const SOFT = [0.22, 1, 0.36, 1];
    const SOFT_OUT = [0.4, 0, 0.6, 1];
    const deskIn = (delay = 0) => ({ duration: reduceMotion ? 0 : 0.5, ease: SOFT, delay: reduceMotion ? 0 : delay });
    const deskOut = { duration: reduceMotion ? 0 : 0.32, ease: SOFT_OUT };

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
    /* Практика по ТЗ курса: человек сам ищет, где это делается, поэтому цель
       не подсвечена, пока он не спросил помощника (step.hideTarget). Движок
       при этом ждёт то же действие — прячется только подсветка. */
    const target = step.hideTarget && run.hintedIndex !== run.index ? null : expectedTap(run);
    const finished = isFinished(run);

    /* Барс на рабочем месте парит поверх окна (владелец, 23.11.2026) и не
       закрывает цель шага: если она под облачком, он перелетает в другой угол
       (floatCorner.js). Облачко сворачивается щелчком по Барсу и снова
       раскрывается на новом шаге — новая реплика должна быть видна. */
    const [corner, setCorner] = useState('br');
    /* Верхний угол — под панелью браузера (и плашкой звонка, если она есть),
       левый — правее меню CRM: меню Барс не закрывает никогда. */
    const [floatInset, setFloatInset] = useState({ top: 96, left: 0 });
    const [cloudOpen, setCloudOpen] = useState(true);
    useEffect(() => { setCloudOpen(true); }, [step.key]);
    const targetKey = Array.isArray(target) ? target.join(',') : String(target || '');
    useEffect(() => {
        if (!deskMode) return undefined;
        let frame = 0;
        const check = () => {
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                /* Помощника ищем в сцене, а не по ref: он прямой потомок
                   AnimatePresence, и ref на нём framer-motion 12 читает через
                   props.ref — React 18 ругается в консоль. */
                const stage = stageRef.current;
                const measured = measureFloat(stage, stage && stage.querySelector('.wt-helper--float'));
                if (!measured) return;
                const inset = { top: Math.round(measured.topInset), left: Math.round(measured.leftInset) };
                setFloatInset((prev) => (prev.top === inset.top && prev.left === inset.left ? prev : inset));
                setCorner((current) => chooseCorner({ ...measured, current }));
            });
        };
        check();
        // Прокрутка CRM к цели плавная: проверяем и когда она доедет.
        const later = setTimeout(check, 560);
        window.addEventListener('resize', check);
        const frameDoc = stageRef.current?.querySelector('.wt-crmframe iframe')?.contentDocument || null;
        frameDoc?.addEventListener('scroll', check, true);
        return () => {
            cancelAnimationFrame(frame);
            clearTimeout(later);
            window.removeEventListener('resize', check);
            frameDoc?.removeEventListener('scroll', check, true);
        };
    }, [deskMode, step.key, targetKey, cloudOpen, run.world.crmView, run.world.drvTab]);
    /* Курс из блоков (scenario.blocks): прогресс показываем по блокам, а не
       общим списком из двух десятков пунктов. */
    const blocks = scenario.blocks || null;
    const blockNow = blocks ? currentBlock(run) : -1;
    /* «Шаг N из M» и чек-лист — по способу, которым идёт попытка: у подписи
       «Через ЭЦП» пунктов меньше, чем «Через eGov». В учёт прохождений уходит
       stages — по всему сценарию, оно постоянно (см. runner.stageCount). */
    const shownStages = stagesOf(run);
    const checklist = checklistFor(scenario, run.world);
    /* Способ ещё не выбран: «шаг 1 из 8» и список одного способа здесь врали
       бы — человек ещё не решил, каким путём идёт. До выбора показываем шаги
       обоих способов. */
    const choosing = !!scenario.variants && !run.world.variant;
    const said = speech(run);
    const percent = progressPercent(run);
    /* Аттестация: итог — вердикт по ситуациям. Радоваться провалу барсу
       незачем, и список ситуаций показывает «верно / ошибка», а не «пройдено». */
    const exam = scenario.examSummary && finished ? scenario.examSummary(run.world) : null;
    const examVerdict = (index) => {
        const item = exam ? exam.items[index] : null;
        return item ? (item.ok ? 'is-done' : 'is-fail') : null;
    };
    const mood = finished ? (exam && exam.correct < exam.total ? 'speak' : 'success') : (MOOD[said.tone] || 'speak');
    const goal = stepGoal(run);

    /* Функциональные обновления, а не tap(run, …): между нажатием и отрисовкой
       может прийти второе нажатие (двойной тап по кнопке телефона), и на
       замкнутом run второе применилось бы к устаревшему состоянию. */
    const doTap = useCallback((id, payload) => {
        setRun((prev) => tap(prev, id, payload).run);
    }, []);
    /* Мягкое нажатие: набор в поле, выбор в списке. Двигает урок, только если
       это ожидаемое действие и его проверка прошла, — иначе ничего. */
    const doSoftTap = useCallback((id, payload) => {
        setRun((prev) => tap(prev, id, payload, { soft: true }).run);
    }, []);

    /* Действие в учебной CRM: кнопка — ход урока, поле и вкладка — без
       штрафа, выбор из списка — с объяснением, если выбрали не то (см.
       runner.uiAction). Функциональное обновление — по той же причине, что
       у doTap: набор в поле идёт быстрее перерисовки. */
    const doUi = useCallback((action) => setRun((prev) => uiAction(prev, action)), []);

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

    /* «Завершить постобработку» в iCORE Phone — то же, что «Завершить
       попытку» на плашке прогресса: звонок закрыт, попытка закончена. */
    const doFinishAttempt = useCallback(() => {
        handleCall('finish');
        doTap('finish_attempt');
    }, [handleCall, doTap]);

    /* Линия: звонки приходят только тому, кто на линии (world.onLine). В
       режиме звонка стажёр на линии с начала смены — рабочее место одна CRM. */
    useEffect(() => {
        if (!callMode) return;
        setRun((prev) => {
            const next = syncReady(prev.world.call || emptyCall(), prev.world);
            return next === prev.world.call ? prev : browse(prev, { call: next });
        });
    }, [callMode, run.world.onLine]);

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
    /* «Будет ли нажатие промахом» — по свежей попытке, в момент нажатия: окно
       подтверждения CRM («Код») не должно открываться перед объяснением. */
    const doMisses = useCallback((id) => uiMisses(runRef.current, id), []);

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
                'Микрофон недоступен: браузер не дал разрешение. Разрешите микрофон значком '
                + 'в адресной строке — без него водитель вас не услышит.',
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
    /* «Повторить блок» — задание блока заново с его начала (ТЗ: «возможность
       повторного выполнения заданий»). Это та же попытка: учёт не сбрасывается. */
    const doRestartBlock = useCallback(() => {
        setRun((prev) => restartBlock(prev, { caseData }));
    }, [caseData]);

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
            onFinishedRef.current?.();
        }
    }, [finished, runLog]);

    // Закрытие проигрывателя: то, что не досчиталось, уходит как брошенное.
    useEffect(() => () => runLog.close('abandoned'), [runLog]);

    const Screen = SCREENS[scenario.key]?.[step.screen];
    const purpose = scenario.egovPurpose ? scenario.egovPurpose(step.key) : 'docs';

    /* ── Части помощника ────────────────────────────────────────────────────
       Одни и те же у колонки (телефонные тренажёры) и у парящего Барса
       (рабочее место): меняется раскладка, а не содержание. */

    /* Перекрёстный переход настроения. Слой абсолютный, размер держит сам
       слот — иначе на время перехода стояли бы два барса друг под другом, а
       замер пробежки (slotRef) уехал бы вместе с высотой. */
    const leoLayers = (
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
    );

    /* aria-live остаётся на постоянном узле: перевесить его на сменяемый
       абзац — значит потерять объявление, потому что скринридер следит за
       ИЗМЕНЕНИЯМИ внутри области, а не за её заменой. key по тексту: новая
       реплика проявляется на месте, старая исчезает мгновенно — высота
       меняется один раз, а не дважды. */
    const sayText = (
        <p aria-live="polite">
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
    );

    const goalBlock = !finished && !noLesson ? (
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
    ) : null;

    /* Подсказка — часть урока. В свободной среде подсказывать нечего:
       правильного следующего действия там нет; на аттестации подсказок не
       бывает (scenario.noHints). */
    const hintButton = noLesson || scenario.noHints ? null : (
        <button type="button" className="wt-hint-btn" onClick={doHint} disabled={finished}>
            <HelpCircle size={15} /> {scenario.hintLabel || 'Подсказка'}
        </button>
    );

    /* Парящий Барс рабочего места: сам Барс и облачко с репликой, «Сейчас»
       и «Спросить Барса». Стоит в углу окна (corner), щелчок по Барсу
       сворачивает облачко.

       Анимации (владелец, 23.11.2026: «более плавные анимации открытия и
       закрытия»): Барс появляется вслед за окном и плашкой; в другой угол не
       прыгает и не едет через всё окно, а гаснет и проявляется на новом месте
       (AnimatePresence по углу); облачко раскрывается ИЗ Барса и сворачивается
       в него — точка роста у того края, где стоит Барс. Сдвиг раскладкой
       (layout) здесь нельзя: при сворачивании облачка коробка помощника
       сужается, и Барс дёргался бы вслед за ней. */
    const origin = {
        originX: corner === 'bl' ? 0 : 1,
        originY: corner === 'tr' ? 0 : 1,
    };
    const lift = corner === 'tr' ? -10 : 12;
    const helperFloat = (
        <AnimatePresence mode="wait" initial={animateEntrance && !reduceMotion}>
            {(
                <motion.aside
                    key={corner}
                    className={`wt-helper wt-helper--float is-${corner}${cloudOpen ? '' : ' is-folded'}`}
                    style={{
                        ...origin,
                        '--wt-float-top': `${floatInset.top}px`,
                        '--wt-float-left': `${floatInset.left}px`,
                    }}
                    initial={{ opacity: 0, scale: 0.92, y: lift }}
                    /* Закрытие тренажёра — уход вместе с окном, в его темпе;
                       смена угла (exit) — быстрее: новая реплика не ждёт. */
                    animate={leaving
                        ? { opacity: 0, scale: 0.94, y: lift * 0.6 }
                        : { opacity: 1, scale: 1, y: 0 }}
                    exit={{
                        opacity: 0,
                        scale: 0.94,
                        y: lift * 0.6,
                        transition: { duration: reduceMotion ? 0 : 0.2, ease: SOFT_OUT },
                    }}
                    transition={leaving ? deskOut
                        : (phase === 'done' ? { duration: reduceMotion ? 0 : 0.36, ease: SOFT } : deskIn(0.18))}
                    onAnimationComplete={() => {
                        if (!leaving) setPhase((p) => (p === 'cards' ? (skipRunner ? 'done' : 'run') : p));
                    }}
                >
                    <button
                        type="button"
                        className="wt-helper__leo"
                        ref={slotRef}
                        aria-expanded={cloudOpen}
                        aria-label={cloudOpen ? 'Свернуть подсказку Барса' : 'Показать подсказку Барса'}
                        title={cloudOpen ? 'Свернуть' : 'Что сейчас делать?'}
                        onClick={() => setCloudOpen((open) => !open)}
                    >
                        {leoLayers}
                    </button>
                    <AnimatePresence initial={false}>
                        {cloudOpen ? (
                            <motion.div
                                key="cloud"
                                className={`wt-cloud wt-cloud--${said.tone}`}
                                style={origin}
                                initial={{ opacity: 0, scale: 0.86, y: lift * 0.5 }}
                                animate={{ opacity: 1, scale: 1, y: 0 }}
                                exit={{
                                    opacity: 0,
                                    scale: 0.9,
                                    y: lift * 0.4,
                                    transition: { duration: reduceMotion ? 0 : 0.22, ease: SOFT_OUT },
                                }}
                                transition={{ duration: reduceMotion ? 0 : 0.38, ease: SOFT }}
                            >
                                <div className="wt-cloud__body">
                                    <span className="wt-bubble__who">Барс</span>
                                    {sayText}
                                </div>
                                {goalBlock}
                                {hintButton}
                            </motion.div>
                        ) : null}
                    </AnimatePresence>
                </motion.aside>
            )}
        </AnimatePresence>
    );

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
                + `${windowsMode ? ' wt-root--windows' : ''}`
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
                /* Телефона на рабочем месте нет, и сигнал «ушли» подаёт
                   подложка: она гаснет последней. */
                onAnimationComplete={() => { if (leaving && deskMode) onExited?.(); }}
            />

            {/* ── СЛЕВА: помощник ────────────────────────────────────────────
                Барс и его реплика занимают целую колонку, и текст здесь крупнее,
                чем в остальном портале: это не подпись к картинке, а то, ради
                чего экран открыт, — человек читает объяснение и идёт делать.
                Выезжает из-за телефона: карточка стартует сдвинутой ВПРАВО, к
                телефону, и уходит на своё место влево. На рабочем месте колонки
                нет — Барс парит поверх окна (helperFloat, внутри сцены). */}
            {deskMode ? null : (
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
                        {leoLayers}
                    </div>
                    <div className={`wt-bubble wt-bubble--${said.tone}`}>
                        <span className="wt-bubble__who">Барс</span>
                        {sayText}
                    </div>
                    {goalBlock}
                    {hintButton}
                </motion.aside>
            )}

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
            <motion.div className={`wt-stage${worldMode ? ' wt-stage--world' : ''}`
                + `${deskMode ? ' wt-stage--desk' : ''}`
                + `${deskMode && step.screen !== 'desk' ? ' wt-stage--card' : ''}`} ref={stageRef}
                /* Окно рабочего места проявляется с лёгким приближением и так
                   же уходит. Масштаб, а не сдвиг: окно на компьютере не
                   выезжает из-за края, оно открывается на месте. */
                initial={deskMode && animateEntrance && !reduceMotion
                    ? { opacity: 0, scale: 0.97, y: 10 } : false}
                animate={deskMode
                    ? (leaving ? { opacity: 0, scale: 0.975, y: 8 } : { opacity: 1, scale: 1, y: 0 })
                    : undefined}
                transition={leaving ? deskOut : deskIn(0)}>
                {worldMode || deskMode ? (
                    /* Мир вокруг человека и рабочее место оператора. Вступление
                       и финал в обоих режимах — обычные карточки: показывать их
                       «в телефоне» незачем, телефон здесь часть сцены (или его
                       нет вовсе), а не рамка для любого текста. */
                    step.screen === 'pc_camera' || step.screen === 'pc_list' ? (
                        /* Оба экрана фотоконтроля рисует одна и та же сцена:
                           человек стоит у машины и там, где выбирает кадр в
                           списке, и там, где его снимает. Пересоздавать сцену
                           между ними нельзя — модель грузится секундами. */
                        <Suspense fallback={<div className="wt-world__load">Готовим машину…</div>}>
                            <CarStage
                                world={run.world}
                                tap={doTap}
                                toggle={doToggle}
                                browse={doBrowse}
                                target={target}
                                screen={step.screen}
                                car={run.world.car?.model}
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
                                    /* Ключ постоянный: рабочее место — одно окно на весь
                                       урок. С ключом шага оно пересоздавалось бы на
                                       каждом шаге — фрейм CRM перезагружался бы, а
                                       набранное в полях терялось. */
                                    <Screen
                                        key="desk"
                                        scenario={scenario}
                                        world={run.world}
                                        tap={doTap}
                                        toggle={doToggle}
                                        browse={doBrowse}
                                        softTap={doSoftTap}
                                        onUi={doUi}
                                        misses={doMisses}
                                        free={!!step.free}
                                        emit={doEmit}
                                        onSave={doSave}
                                        target={target}
                                        onRestart={doRestart}
                                        onCall={callMode ? handleCall : null}
                                        voice={voice}
                                        aiSpeaking={aiSpeaking}
                                        micLevel={micLevel}
                                        micError={micError}
                                        devMode={devMode}
                                        environment={environment}
                                        desktopMenu={desktopMenu}
                                        onFinishAttempt={callMode ? doFinishAttempt : null}
                                        /* Барс в среде Windows — помощник, которого можно
                                           переставить (WinBuddy); говорит по ходу звонка. */
                                        buddy={windowsMode
                                            ? <WinBuddy leo={leoLayers} tip={tipForCall(run.world.call)} />
                                            : null}
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
                {deskMode && !windowsMode ? helperFloat : null}
            </motion.div>

            {/* ── СПРАВА: прогресс ─────────────────────────────────────────
                Полоса, доля и список шагов инструкции вместе: полоса отвечает
                «сколько осталось», список — «что именно осталось». В отдельной
                колонке список помещается целиком, поэтому прятать его больше
                не нужно. Выезжает из-за телефона зеркально помощнику. */}
            {windowsMode ? null : (
            <motion.aside
                className="wt-side"
                initial={animateEntrance && !reduceMotion
                    ? (deskMode ? { opacity: 0, x: 24, scale: 0.98 } : { opacity: 0, x: -120, scale: 0.94 })
                    : false}
                animate={leaving
                    ? (deskMode ? { opacity: 0, x: 16, scale: 0.98 } : { opacity: 0, x: -120, scale: 0.94 })
                    : (cardsOut ? { opacity: 1, x: 0, scale: 1 } : {})}
                transition={deskMode
                    ? (leaving ? deskOut : deskIn(0.08))
                    : { duration: leaving ? 0.24 : 0.46, ease: leaving ? EASE_LEAVE : EASE }}
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
                            <span>
                                {exam ? `Верно ${exam.correct} из ${exam.total}`
                                    : finished ? 'Урок пройден'
                                    : choosing ? 'Выберите способ'
                                        : blocks ? `Блок ${blockNow + 1} из ${blocks.length}`
                                            : `Шаг ${Math.max(1, step.stage)} из ${shownStages}`}
                            </span>
                        </div>

                        {blocks ? (
                            /* Курс: три блока подряд. У пройденного — галочка, у
                               текущего раскрыты его пункты, будущие свёрнуты. */
                            <ol className="wt-blocks">
                                {blocks.map((block, index) => {
                                    const state = finished || index < blockNow ? 'is-done'
                                        : (index === blockNow ? 'is-current' : '');
                                    const first = blocks.slice(0, index)
                                        .reduce((sum, item) => sum + item.checklist.length, 0);
                                    return (
                                        <li key={block.title} className={state}>
                                            <div className="wt-blocks__head">
                                                <i aria-hidden="true">{state === 'is-done' ? '✓' : index + 1}</i>
                                                <b>{block.title}</b>
                                            </div>
                                            {state === 'is-current' ? (
                                                <ol className="wt-steps">
                                                    {block.checklist.map((item, n) => {
                                                        const number = first + n + 1;
                                                        const itemState = step.stage > number ? 'is-done'
                                                            : (step.stage === number ? 'is-current' : '');
                                                        return (
                                                            <li key={`${n}-${item}`} className={itemState}>
                                                                <i aria-hidden="true">{step.stage > number ? '✓' : n + 1}</i>
                                                                {item}
                                                            </li>
                                                        );
                                                    })}
                                                </ol>
                                            ) : null}
                                        </li>
                                    );
                                })}
                            </ol>
                        ) : choosing ? (
                            <div className="wt-steps-choice">
                                {Object.entries(scenario.variants).map(([key, variant]) => (
                                    <div key={key}>
                                        <b>{variant.label}</b>
                                        <ol className="wt-steps">
                                            {(variant.checklist || []).map((item, index) => (
                                                <li key={`${index}-${item}`}>
                                                    <i aria-hidden="true">{index + 1}</i>
                                                    {item}
                                                </li>
                                            ))}
                                        </ol>
                                    </div>
                                ))}
                            </div>
                        ) : (
                        <ol className="wt-steps">
                            {checklist.map((item, index) => {
                                const number = index + 1;
                                const verdict = examVerdict(index);
                                const state = verdict || (finished || step.stage > number ? 'is-done'
                                    : (step.stage === number ? 'is-current' : ''));
                                return (
                                    <li key={`${index}-${item}`} className={state}>
                                        <i aria-hidden="true">
                                            {verdict === 'is-fail' ? '✗' : (finished || step.stage > number ? '✓' : number)}
                                        </i>
                                        {item}
                                    </li>
                                );
                            })}
                        </ol>
                        )}
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
                        {blocks && !finished && blockOfStage(scenario, step.stage) >= 0 ? (
                            <button type="button" className="wt-side__btn" onClick={doRestartBlock}>
                                <RotateCcw size={14} /> Повторить блок
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
            )}
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
    environment = null, desktopMenu = null,
    /* layer="top" — поверх окна «Новость дня» (z-index 120): оттуда тренажёр
       открывают, пока окно остаётся на месте (задача #342). */
    onFinished = null, layer = null,
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
    // стоит на кнопке внутри учебного телефона. В среде Windows — нет: там Esc
    // закрывает списки CRM, и смена со звонком обрывалась бы от одного нажатия;
    // выход — меню «Учебная смена».
    useEffect(() => {
        if (environment === 'windows') return undefined;
        const onKey = (event) => { if (event.key === 'Escape') requestClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [requestClose, environment]);

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
        <div className={`wt-overlay${layer === 'top' ? ' wt-overlay--top' : ''}`}
             role="dialog" aria-modal="true" aria-label={scenario.title}>
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
                environment={environment}
                desktopMenu={desktopMenu}
                onFinished={onFinished}
            />
        </div>,
        document.body,
    );
}
