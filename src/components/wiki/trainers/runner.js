/* Движок тренажёра: шаг → нажатие → следующий шаг.
 *
 * Чистые функции без React и без сети. Причина не в аккуратности: тренажёр —
 * это правила («сначала закрой рекламу, потом подписывай», «после подписи
 * обязательно вернись на портал»), и проверять их надо тестами, а не глазами по
 * четырнадцати экранам. Всё, что знает про DOM, живёт в TrainerPlayer.
 *
 * Модель одна на все сценарии: упорядоченный список шагов, у каждого — ОДНО
 * правильное нажатие и список ловушек с объяснением, почему нажали не туда.
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

const MONTHS_GEN = [
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

const stepAt = (scenario, index) => scenario.steps[Math.max(0, Math.min(index, scenario.steps.length - 1))];

/** Сколько пунктов инструкции в сценарии — знаменатель прогресса. */
export const stageCount = (scenario) => scenario.steps
    .reduce((max, step) => Math.max(max, Number(step.stage) || 0), 0);

/** Новая попытка. options.random — только для тестов (детерминированные коды). */
export const startRun = (scenario, { now, random } = {}) => {
    const today = almatyToday(now);
    return {
        scenario,
        index: 0,
        errors: 0,
        hints: 0,
        // Что барс говорит прямо сейчас. tone нужен интерфейсу: реплика-ошибка
        // и реплика-объяснение выглядят по-разному, иначе неверное нажатие
        // проходит незамеченным.
        speech: { text: stepAt(scenario, 0).msg, tone: 'idle' },
        world: scenario.createWorld({ today, code: () => trainingCode(random) }),
    };
};

export const currentStep = (run) => stepAt(run.scenario, run.index);

/** Идентификатор кнопки, которую сейчас ждут. Подсветка цели берёт его же. */
export const expectedTap = (run) => {
    const step = currentStep(run);
    return typeof step.action === 'function' ? step.action(run.world) : step.action;
};

export const isFinished = (run) => run.index >= run.scenario.steps.length - 1;

/** Прогресс в процентах — по шагам движка, а не по пунктам инструкции.
 *  По пунктам полоса стояла бы на месте три экрана подряд и выглядела зависшей. */
export const progressPercent = (run) => Math.round(
    (run.index / Math.max(1, run.scenario.steps.length - 1)) * 100,
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
export const tap = (run, id, payload = {}) => {
    const step = currentStep(run);
    const expected = expectedTap(run);

    if (id !== expected) {
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
    if (step.check) {
        const problem = step.check(run.world, payload);
        if (problem) {
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

    const world = step.apply ? { ...run.world, ...step.apply(run.world, payload) } : run.world;
    const nextIndex = Math.min(run.index + 1, run.scenario.steps.length - 1);
    const next = { ...run, index: nextIndex, world };
    return {
        ok: true,
        run: { ...next, speech: { text: stepAt(run.scenario, nextIndex).msg, tone: 'idle' } },
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
 * это НЕ ход: в тренажёре «Обращение в CRM» соседняя вкладка — Диспетчерская,
 * куда оператор идёт СМОТРЕТЬ (например, чью комиссию удержали). Считать такой
 * поход ошибкой значило бы учить не открывать справочник.
 */
export const browse = (run, patch) => ({
    ...run,
    world: { ...run.world, ...patch },
});

/* Подсказка. Считается — счётчик виден человеку, как в исходных тренажёрах.
   Имя НЕ useHint, хотя по смыслу подошло бы: функция с приставкой use в React
   читается как хук, и вызов её из обработчика ловит правило линтера. */
export const takeHint = (run) => ({
    ...run,
    hints: run.hints + 1,
    speech: { text: currentStep(run).hint, tone: 'hint' },
});

/** Начать заново. Коды и период пересчитываются — попытка честно новая. */
export const restart = (run, options) => startRun(run.scenario, options);
