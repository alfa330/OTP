/* Движок тренажёра: шаг → нажатие → следующий шаг.
 *
 * Чистые функции без React и без сети. Причина не в аккуратности: тренажёр —
 * это правила («сначала закрой рекламу, потом подписывай», «после подписи
 * обязательно вернись на портал»), и проверять их надо тестами, а не глазами по
 * четырнадцати экранам. Всё, что знает про DOM, живёт в TrainerPlayer.
 *
 * Модель одна на все сценарии: упорядоченный список шагов, у каждого — ОДНО
 * правильное нажатие и список ловушек с объяснением, почему нажали не туда.
 * Исключение — шаг-развилка (choices): у него несколько верных кнопок, и каждая
 * выбирает СПОСОБ (вариант) прохождения. Шаги, помеченные variant, идут только
 * в своём способе — так один тренажёр учит двум путям к одной цели (подпись в
 * Такси.Про «Через ЭЦП» или «Через eGov»), а кнопка в статье остаётся одна.
 * Баллов и порога нет намеренно: это учебная прогулка по инструкции, а не
 * экзамен. Неверное нажатие ничего не отнимает — оно объясняет, и это главное,
 * что тренажёр даёт сверх скриншотов в статье.
 *
 * Состояние («мир») тоже здесь: даты периода АВР, учебные коды, флаги «подписал
 * / вернулся / сохранил / обновил». Экраны его только читают.
 */

/* Шаги нумеруются ДВАЖДЫ, и это не дубль:
 *   index — позиция в массиве шагов движка (их больше);
 *   stage — номер шага ИНСТРУКЦИИ, который человек видит сверху («шаг 4 из 8»).
 * Одному пункту инструкции почти всегда соответствует несколько экранов: «войти
 * через eGov Mobile» — это код, подпись и возврат. Показывать «шаг 11 из 18»
 * там, где в инструкции шесть пунктов, значит рассинхронить тренажёр с текстом,
 * рядом с которым он стоит. */

const MONTHS_NOM = [
    'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
];

export const MONTHS_GEN = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

/* Дата в бизнес-таймзоне Asia/Almaty (UTC+5, без перехода на лето).
 *
 * Не new Date().toISOString(): у оператора в Алматы 01:30, а UTC-дата — ещё
 * вчерашняя, и тренажёр показывал бы период АВР на месяц назад в первую ночь
 * месяца. Смещение фиксированное, потому что в Казахстане DST отменён. */
export const almatyToday = (now = new Date()) => {
    const shifted = new Date(now.getTime() + now.getTimezoneOffset() * 60000 + 5 * 3600 * 1000);
    return {
        year: shifted.getFullYear(),
        month: shifted.getMonth() + 1,
        day: shifted.getDate(),
    };
};

const lastDayOf = (year, month) => new Date(Date.UTC(year, month, 0)).getUTCDate();

const pad = (value) => String(value).padStart(2, '0');

/** Период закрывающих документов — ВСЕГДА предыдущий календарный месяц. */
export const previousPeriod = (today) => {
    const year = today.month === 1 ? today.year - 1 : today.year;
    const month = today.month === 1 ? 12 : today.month - 1;
    const day = lastDayOf(year, month);
    return {
        year,
        month,
        iso: `${year}-${pad(month)}-${pad(day)}`,
        // «31 июля 2026» — так дата создания акта выглядит в приложении.
        human: `${day} ${MONTHS_GEN[month - 1]} ${year}`,
        // «за Июль 2026» — так период подписан в кабинете Сапар.
        label: `за ${MONTHS_NOM[month - 1][0].toUpperCase()}${MONTHS_NOM[month - 1].slice(1)} ${year}`,
        short: `${MONTHS_NOM[month - 1]} ${year}`,
    };
};

/** Месяц ДО периода: его акт в тренажёре уже подписан — как на скриншотах. */
export const periodBefore = (period) => previousPeriod({ year: period.year, month: period.month });

/* Учебный код eGov. Случайный на каждую попытку, а не константа: с константой
   человек на второй раз вводит код по памяти, не глядя на подсказку, и шаг
   перестаёт учить тому, что код всегда приходит заново. */
export const trainingCode = (random = Math.random) => String(Math.floor(random() * 10000)).padStart(4, '0');

/** Шаги, по которым идёт попытка. У сценария со способами (variants) шаг с
 *  пометкой variant берётся только в своём способе; до выбора — в способе по
 *  умолчанию, чтобы прогресс и подсказки до развилки были осмысленными. У
 *  сценариев без способов это просто весь список. */
export const activeSteps = (scenario, world) => {
    if (!scenario.variants) return scenario.steps;
    const variant = (world && world.variant) || scenario.defaultVariant;
    return scenario.steps.filter((step) => !step.variant || step.variant === variant);
};

const stepsOf = (run) => activeSteps(run.scenario, run.world);

const stepAt = (steps, index) => steps[Math.max(0, Math.min(index, steps.length - 1))];

const maxStage = (steps) => steps.reduce((max, step) => Math.max(max, Number(step.stage) || 0), 0);

/** Сколько пунктов инструкции в сценарии — по ВСЕМ шагам, то есть по самому
 *  длинному способу. Это число уходит в учёт прохождений (stages_total) и на
 *  витрину, и оно обязано быть постоянным: поменяйся оно посреди урока, учёт
 *  завёл бы вторую строку попытки. */
export const stageCount = (scenario) => maxStage(scenario.steps);

/** Сколько пунктов в способе, которым идёт эта попытка, — для «шаг N из M». */
export const stagesOf = (run) => maxStage(stepsOf(run));

/** Чек-лист рядом с телефоном: свой у каждого способа, до выбора — способа по
 *  умолчанию. */
export const checklistFor = (scenario, world) => {
    const variant = (world && world.variant) || scenario.defaultVariant;
    const own = scenario.variants && variant && scenario.variants[variant];
    return (own && own.checklist) || scenario.checklist || [];
};

/** Новая попытка.
 *
 * options.random — только для тестов (детерминированные коды).
 * options.caseData — слепок дела для сред, которые читают данные снаружи
 * (рабочее место оператора). Не приехал — сценарий берёт свой запасной.
 */
export const startRun = (scenario, { now, random, caseData } = {}) => {
    const today = almatyToday(now);
    const world = scenario.createWorld({ today, code: () => trainingCode(random), caseData });
    return {
        scenario,
        index: 0,
        errors: 0,
        hints: 0,
        // Что барс говорит прямо сейчас. tone нужен интерфейсу: реплика-ошибка
        // и реплика-объяснение выглядят по-разному, иначе неверное нажатие
        // проходит незамеченным.
        speech: { text: stepAt(activeSteps(scenario, world), 0).msg, tone: 'idle' },
        world,
    };
};

export const currentStep = (run) => stepAt(stepsOf(run), run.index);

/** Идентификатор кнопки, которую сейчас ждут. Подсветка цели берёт его же.
 *  У шага-развилки — список: верна любая из кнопок, подсвечены все. */
export const expectedTap = (run) => {
    const step = currentStep(run);
    if (step.choices) return Object.keys(step.choices);
    return typeof step.action === 'function' ? step.action(run.world) : step.action;
};

export const isFinished = (run) => run.index >= stepsOf(run).length - 1;

/** Прогресс в процентах — по шагам движка, а не по пунктам инструкции.
 *  По пунктам полоса стояла бы на месте три экрана подряд и выглядела зависшей. */
export const progressPercent = (run) => Math.round(
    (run.index / Math.max(1, stepsOf(run).length - 1)) * 100,
);

/* Подстановка в реплики: {period}, {code}, {month}. Значения приходят из мира,
   потому что зависят от даты запуска и от попытки. */
const fill = (text, run) => {
    const vars = run.scenario.vars ? run.scenario.vars(run.world, run) : {};
    return String(text || '').replace(/\{(\w+)\}/g, (match, key) => (
        key in vars ? String(vars[key]) : match
    ));
};

/** Текущая реплика барса. Ошибка вытесняет объяснение шага до следующего хода. */
export const speech = (run) => ({
    text: fill(run.speech.text, run),
    tone: run.speech.tone,
});

export const stepGoal = (run) => fill(currentStep(run).goal, run);

const trapMessage = (step, id, world) => {
    const trap = (step.traps || {})[id];
    if (!trap) return null;
    return typeof trap === 'function' ? trap(world) : trap;
};

/* Ловушки, общие для всего сценария (нижняя навигация приложения, «выход» в
   кабинете) — чтобы не переписывать их в каждом шаге и не забыть в одном. */
const commonTrap = (scenario, id, world) => {
    const trap = (scenario.traps || {})[id];
    if (!trap) return null;
    return typeof trap === 'function' ? trap(world) : trap;
};

/** Нажатие по учебному телефону.
 *
 * Возвращает НОВЫЙ run (состояние не мутируется — на нём стоит React) и флаг
 * ok. Три исхода:
 *   ok:true  — шаг зачтён, движок перешёл к следующему;
 *   ok:false — не туда, барс объясняет; счётчик ошибок +1;
 *   ok:false + finished — попытка уже завершена, нажимать нечего.
 */
export const tap = (run, id, payload = {}, { soft = false } = {}) => {
    const step = currentStep(run);
    const expected = expectedTap(run);
    const accepted = step.choices
        ? Object.prototype.hasOwnProperty.call(step.choices, id)
        : id === expected;

    /* Мягкое нажатие — набор в поле, выбор в списке, раскрытие вкладки. Оно
       двигает урок, только если это и есть ожидаемое действие и проверка шага
       прошла; иначе не считается ни ходом, ни промахом. Без этого каждая буква
       номера телефона засчитывалась бы ошибкой, пока номер не набран целиком. */
    if (soft && (!accepted || (step.check && step.check(run.world, payload)))) {
        return { ok: false, run };
    }

    if (!accepted) {
        const message = trapMessage(step, id, run.world)
            || commonTrap(run.scenario, id, run.world)
            // Общий ответ обязателен: без него неверное нажатие по кнопке, о
            // которой автор сценария не подумал, не давало бы никакой реакции,
            // и тренажёр выглядел бы сломанным.
            || 'Сейчас нужно другое действие — посмотри на подсвеченную кнопку.';
        return {
            ok: false,
            run: {
                ...run,
                errors: run.errors + 1,
                speech: { text: message, tone: 'error' },
            },
        };
    }

    // Проверка ввода (учебный код eGov). Ошибка ввода — не «не туда нажал»:
    // кнопка правильная, поэтому шаг остаётся, а объяснение другое.
    //
    // Шаг аттестации (step.exam) — иначе: попытка одна, как на смене. Ответ
    // записывается (верно или с причиной ошибки), и экзамен идёт дальше;
    // разбор ошибок человек видит в конце, а не по ходу.
    let examProblem = null;
    if (step.check) {
        const problem = step.check(run.world, payload);
        if (problem && step.exam) {
            examProblem = problem;
        } else if (problem) {
            return {
                ok: false,
                run: {
                    ...run,
                    errors: run.errors + 1,
                    speech: { text: problem, tone: 'error' },
                },
            };
        }
    }

    let world = step.apply ? { ...run.world, ...step.apply(run.world, payload) } : run.world;
    if (step.exam) {
        world = {
            ...world,
            exam: { ...(world.exam || {}), [step.key]: examProblem ? { ok: false, problem: examProblem } : { ok: true } },
        };
    }
    // Развилка: нажатая кнопка выбирает способ, и дальше список шагов уже его.
    // Шаги до развилки общие для всех способов, поэтому номер следующего шага
    // в новом списке — тот же index + 1.
    if (step.choices) world = { ...world, variant: step.choices[id] };
    const steps = activeSteps(run.scenario, world);
    const nextIndex = Math.min(run.index + 1, steps.length - 1);
    const next = { ...run, index: nextIndex, world, errors: run.errors + (examProblem ? 1 : 0) };
    return {
        ok: true,
        run: { ...next, speech: { text: stepAt(steps, nextIndex).msg, tone: 'idle' } },
    };
};

/* Раскрыть список, сменить вкладку внутри экрана — не шаг и не ошибка.
 *
 * Отдельная функция, а не шаг сценария: в eGov список документов раскрывается
 * «посмотреть, что подписываю», и наказывать за любопытство нельзя, но и
 * пропускать вперёд — тоже. */
export const toggle = (run, key) => ({
    ...run,
    world: { ...run.world, [key]: !run.world[key] },
});

/* Свободное перемещение по учебной среде: сменить вкладку браузера, уйти в
 * другой раздел соседнего кабинета, полистать его вкладки.
 *
 * Отдельно от toggle, потому что там один флаг, а здесь состояние: «какая
 * вкладка открыта» и «какой экран внутри неё». И отдельно от tap, потому что
 * это НЕ ход: в рабочем месте оператора это вкладки карточки водителя и
 * разделы CRM, куда оператор идёт СМОТРЕТЬ (например, чью комиссию удержали).
 * Считать такой поход ошибкой значило бы учить не открывать справочник.
 */
export const browse = (run, patch) => ({
    ...run,
    world: { ...run.world, ...patch },
});

/* ── Действия в учебной системе со своим интерфейсом ─────────────────────
 * Учебная CRM — не набор экранов «одна кнопка на шаг», а живая система: в ней
 * печатают, листают вкладки, раскрывают меню. Поэтому у каждого её действия
 * есть ВИД, и он решает, что считать ходом, а что — нет:
 *
 *   act   — кнопка или ссылка. Ожидаемая — ход урока (проверка шага, затем
 *           изменение экрана); чужая — промах с объяснением ловушки, экран не
 *           меняется. На шаге-практике (step.free) чужая кнопка без ловушки —
 *           не промах: человек сам ищет дорогу, и это и есть задание.
 *   touch — набор в поле, вкладка, раскрытие окна. Экран меняется всегда;
 *           урок двигается, только если это ожидаемое действие и проверка
 *           прошла. Каждая буква номера телефона — не ошибка.
 *   pick  — выбор из списка. Как touch, но неверный выбор там, где его ждут,
 *           объясняется: «это не та категория — вот почему».
 *
 * patch — что действие меняет на экране (мир попытки), payload — что видит
 * проверка шага. Чистая функция: правила урока проверяются тестами. */
const acceptedBy = (run, id) => {
    const expected = expectedTap(run);
    return Array.isArray(expected) ? expected.includes(id) : id === expected;
};

const hasTrap = (run, id) => Boolean(
    trapMessage(currentStep(run), id, run.world) || commonTrap(run.scenario, id, run.world),
);

/** Будет ли нажатие кнопки промахом — чтобы не показывать перед ним окно
 *  подтверждения («Код»): сначала объяснение, потом уже окна. */
export const uiMisses = (run, id) => {
    if (acceptedBy(run, id)) return false;
    return !currentStep(run).free || hasTrap(run, id);
};

export const uiAction = (run, { kind = 'act', id, patch = null, payload = {} }) => {
    const withPatch = (base) => (patch ? browse(base, patch) : base);
    const step = currentStep(run);
    const accepted = acceptedBy(run, id);

    if (kind === 'act') {
        if (!accepted && step.free && !hasTrap(run, id)) return withPatch(run);
        const result = tap(run, id, payload);
        return result.ok ? withPatch(result.run) : result.run;
    }

    // touch и pick: экран меняется всегда, проверка смотрит уже на новый мир.
    const next = withPatch(run);
    if (!accepted) {
        /* Ловушка ШАГА у мягкого действия — «открыл не ту вкладку»: экран
           показывает, что открыл, а помощник объясняет, почему не то. Общие
           ловушки сценария сюда не идут: они про кнопки, а не про поля. */
        const trap = trapMessage(step, id, run.world);
        return trap ? { ...next, errors: next.errors + 1, speech: { text: trap, tone: 'error' } } : next;
    }
    if (step.check) {
        const problem = step.check(next.world, payload);
        if (problem) {
            return kind === 'pick'
                ? { ...next, errors: next.errors + 1, speech: { text: problem, tone: 'error' } }
                : next;
        }
    }
    return tap(next, id, payload).run;
};

/* Подсказка. Считается — счётчик виден человеку, как в исходных тренажёрах.
   Имя НЕ useHint, хотя по смыслу подошло бы: функция с приставкой use в React
   читается как хук, и вызов её из обработчика ловит правило линтера. */
export const takeHint = (run) => ({
    ...run,
    hints: run.hints + 1,
    // На практике цель не подсвечена, пока не спросили помощника (hideTarget).
    hintedIndex: run.index,
    speech: { text: currentStep(run).hint, tone: 'hint' },
});

/* ── Блоки курса ─────────────────────────────────────────────────────────
   Курс «Рабочее место оператора» — три последовательных блока (ТЗ SZOV-8).
   Блок — это отрезок пунктов инструкции: blocks[i].checklist идут подряд, и
   номер пункта (stage) у шагов сквозной. Блок вычисляется из stage, поэтому
   отдельной разметки шагов по блокам не нужно и разойтись ей не с чем. */

/** Номер блока (с нуля) для пункта инструкции; вне блоков (вступление) — -1. */
export const blockOfStage = (scenario, stage) => {
    if (!scenario.blocks || !stage) return -1;
    let last = 0;
    for (let i = 0; i < scenario.blocks.length; i += 1) {
        last += scenario.blocks[i].checklist.length;
        if (stage <= last) return i;
    }
    return scenario.blocks.length - 1;
};

/** Первый пункт блока (сквозной номер). */
export const firstStageOf = (scenario, block) => scenario.blocks
    .slice(0, block)
    .reduce((sum, item) => sum + item.checklist.length, 0) + 1;

/** Блок, в котором идёт попытка; до первого блока — 0, после финала — последний. */
export const currentBlock = (run) => {
    const { scenario } = run;
    if (!scenario.blocks) return -1;
    if (isFinished(run)) return scenario.blocks.length - 1;
    const block = blockOfStage(scenario, currentStep(run).stage);
    return block < 0 ? 0 : block;
};

/**
 * Пройти блок заново — «возможность повторного выполнения заданий» из ТЗ.
 * Мир собирается с нуля и приводится к началу блока (scenario.blockStart):
 * повтор не должен зависеть от того, что человек натворил в прошлый раз.
 * Промахи и подсказки остаются — это та же попытка.
 */
export const restartBlock = (run, options = {}) => {
    const { scenario } = run;
    const block = currentBlock(run);
    if (block < 0) return restart(run, options);
    const fresh = startRun(scenario, options);
    const world = scenario.blockStart
        ? { ...fresh.world, ...scenario.blockStart(block, fresh.world) }
        : fresh.world;
    const steps = activeSteps(scenario, world);
    const first = firstStageOf(scenario, block);
    const index = Math.max(0, steps.findIndex((step) => Number(step.stage) >= first));
    return {
        ...run,
        index,
        world,
        hintedIndex: undefined,
        speech: { text: steps[index].msg, tone: 'idle' },
    };
};

/** Начать заново. Коды и период пересчитываются — попытка честно новая. */
export const restart = (run, options) => startRun(run.scenario, options);
