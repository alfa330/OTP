/*
 * «Активация водителей» для СЗоВ (26.11.2026) — чистая логика раздела, без React. На неё написан
 * node-тест (tests/dial_list_driver_activation.test.mjs).
 *
 * Раздел — движок обзвона (dial_list) с профилем отдела 'activation' (решения владельца 26.11.2026):
 *   • база — новички, не добравшие N поездок (N — «Цель по поездкам» в настройках, 50); у водителя — поездки за
 *     всё время (CRM) и провайдер ЭДО (карточка Fleet);
 *   • две цели раздельно: добрал N поездок — водитель уходит из обзвона, успешка оператору; перешёл на провайдера
 *     Sapar — отдельная успешка оператору, водитель остаётся, пока не доберёт поездки;
 *   • в остальном — автообзвон, как у Тез: в «Активном» телефон звонит сам, итоги с датой, постобработка.
 * Сервер присылает подписи готовыми (activation.trips_label, provider_label); здесь — запасные правила и то,
 * в чём сайту нельзя разойтись с сервером: пределы цели, этапы журнала, текст итога загрузки.
 */

import { TEZ_STAGES, TEZ_STAGE_ROW_LABEL } from './tezAutodial.js';

export const TRIPS_GOAL_DEFAULT = 50;
export const TRIPS_GOAL_MIN = 1;
export const TRIPS_GOAL_MAX = 1000;

/* Этапы журнала — те же, что у Тез (сроки, перезвоны, дожим); цель — «Добрали поездки». */
export const ACTIVATION_STAGES = TEZ_STAGES.map((s) => {
    if (s.value === 'signed') return { ...s, label: 'Добрали поездки' };
    if (s.value === 'followup') return { ...s, label: 'Обещали ездить' };
    return s;
});

export const ACTIVATION_STAGE_ROW_LABEL = {
    ...TEZ_STAGE_ROW_LABEL,
    followup: 'Обещал ездить',
    signed: 'Добрал поездки',
};

export const activationStageLabel = (lead) => ACTIVATION_STAGE_ROW_LABEL[lead?.stage] || lead?.stage_label || '';

/** «поездка / поездки / поездок» для числа n. */
export const tripsWord = (n) => {
    const v = Math.abs(Math.trunc(Number(n) || 0));
    if (v % 10 === 1 && v % 100 !== 11) return 'поездка';
    if (v % 10 >= 2 && v % 10 <= 4 && !(v % 100 >= 12 && v % 100 <= 14)) return 'поездки';
    return 'поездок';
};

/* «добрал 51 поездку / 52 поездки / 50 поездок»: после глагола — винительный падеж. */
const tripsWordAcc = (n) => (tripsWord(n) === 'поездка' ? 'поездку' : tripsWord(n));

const ofGoalWord = (goal) => {
    const v = Math.abs(Math.trunc(Number(goal) || 0));
    return v % 10 === 1 && v % 100 !== 11 ? 'поездки' : 'поездок';
};

/** Поездки водителя одной фразой — как у сервера (activation.trips_label): «34 из 50 поездок». */
export const tripsLabel = (block) => {
    if (!block) return '';
    if (typeof block.trips_label === 'string' && block.trips_label.trim()) return block.trips_label.trim();
    const goal = Number(block.trips_goal) || TRIPS_GOAL_DEFAULT;
    const total = block.trips_total;
    if (block.reached_at || (total !== null && total !== undefined && Number(total) >= goal)) {
        const n = total !== null && total !== undefined ? Number(total) : goal;
        return `Добрал ${n} ${tripsWordAcc(n)}`;
    }
    if (total === null || total === undefined) return 'Поездки не проверены';
    return `${Number(total)} из ${goal} ${ofGoalWord(goal)}`;
};

/** Сколько поездок не хватает до цели; добрал или не знаем — null. */
export const tripsLeft = (block) => {
    if (!block || block.reached_at) return null;
    const total = block.trips_total;
    if (total === null || total === undefined) return null;
    const left = (Number(block.trips_goal) || TRIPS_GOAL_DEFAULT) - Number(total);
    return left > 0 ? left : null;
};

/** Доля пути к цели, 0..100 (полоса в журнале); не знаем — null. */
export const tripsProgress = (block) => {
    if (!block) return null;
    if (block.reached_at) return 100;
    const total = block.trips_total;
    if (total === null || total === undefined) return null;
    const goal = Number(block.trips_goal) || TRIPS_GOAL_DEFAULT;
    return Math.max(0, Math.min(100, Math.round((Number(total) / goal) * 100)));
};

const PAPER = ['бумажный документооборот', 'бумажный', 'paperdo'];

/** Короткое имя провайдера: «Бумажный документооборот» → «Бумажный». */
export const providerShort = (name) => {
    const text = String(name ?? '').trim().replace(/\s+/g, ' ');
    return PAPER.includes(text.toLowerCase()) ? 'Бумажный' : text;
};

/** «ЭДО: Sapar» / «ЭДО: Бумажный» / «ЭДО: нет» / «ЭДО: не знаем» — как у сервера (activation.provider_label). */
export const providerLabel = (block) => {
    if (!block) return '';
    if (typeof block.provider_label === 'string' && block.provider_label.trim()) return block.provider_label.trim();
    if (block.provider_source === 'none') return 'ЭДО: нет';
    const name = providerShort(block.provider);
    return name ? `ЭДО: ${name}` : 'ЭДО: не знаем';
};

/** Тон провайдера в журнале: Sapar — зелёный (цель), известный другой — янтарный (переводить), не знаем — серый. */
export const providerTone = (block) => {
    if (!block) return 'slate';
    if (block.on_sapar) return 'green';
    if (block.provider || block.provider_source === 'none') return 'amber';
    return 'slate';
};

/** Откуда известен провайдер: «по карточке Fleet», «из файла»; не знаем — ''. */
export const providerSourceText = (block) => {
    if (!block) return '';
    if (block.provider_source === 'card' || block.provider_source === 'none') return 'по карточке Fleet';
    if (block.provider_source === 'file') return 'из файла';
    return '';
};

/** Переход на Sapar одной фразой: «Перевод на Sapar · успешка Иванова», «Перешёл на Sapar сам», «ждём конца звонка». */
export const saparText = (block) => {
    const sapar = block?.sapar;
    if (!sapar) return '';
    if (sapar.success) return `Перевод на Sapar · успешка ${sapar.success.operator?.name || 'оператора'}`;
    if (!sapar.resolved) return 'Перешёл на Sapar · ждём конца звонка';
    return 'Перешёл на Sapar сам, без разговора';
};

/** Цель из поля настроек: '' — годится, иначе текст ошибки (пределы — как у сервера). */
export const validateTripsGoal = (value) => {
    const text = String(value ?? '').trim();
    if (!/^\d+$/.test(text)) return `Цель по поездкам — целое число от ${TRIPS_GOAL_MIN} до ${TRIPS_GOAL_MAX}`;
    const n = Number(text);
    if (n < TRIPS_GOAL_MIN || n > TRIPS_GOAL_MAX) return `Цель по поездкам — от ${TRIPS_GOAL_MIN} до ${TRIPS_GOAL_MAX}`;
    return '';
};

const rowsText = (rows = [], total = 0) => {
    if (!Array.isArray(rows) || !rows.length) return '';
    const more = total > rows.length ? '…' : '';
    return `${rows.length === 1 && total <= 1 ? 'строка' : 'строки'} ${rows.join(', ')}${more}`;
};

/** Итог загрузки базы активации одной фразой: новых, повторов, уже добрали, без ID, без номера. */
export const activationUploadSummary = (data, periodText = '') => {
    const parts = [`новых ${data?.rows_new ?? 0}`];
    if (data?.rows_duplicate) parts.push(`повторов ${data.rows_duplicate}`);
    if (data?.rows_goal_reached) {
        parts.push(`уже ${data.trips_goal ?? TRIPS_GOAL_DEFAULT}+ поездок — не загружены: ${data.rows_goal_reached}`);
    }
    if (data?.rows_bad_id) {
        const where = rowsText(data.bad_id_rows, data.rows_bad_id);
        parts.push(`без ID водителя ${data.rows_bad_id}${where ? ` (${where})` : ''}`);
    }
    if (data?.rows_invalid) {
        const where = rowsText(data.invalid_rows, data.rows_invalid);
        parts.push(`без номера ${data.rows_invalid}${where ? ` (${where})` : ''}`);
    }
    return `${periodText ? `${periodText}: ` : ''}${parts.join(', ')}`;
};

/** Что сказать о проверках в сводке базы: CRM не настроена / кабинет Fleet не подключён / '' — всё есть. */
export const checkWarning = (summary) => {
    const check = summary?.activation_check;
    if (!check) return '';
    if (!check.configured) return 'Проверка поездок не настроена на сервере (CRM) — видны только числа из файла.';
    if (!check.fleet_configured) {
        return 'Кабинет Fleet не подключён — провайдер ЭДО виден только из файла. Сессию кладут в разделе «Провайдер ЭДО».';
    }
    if (check.fleet_error) return `Кабинет Fleet: ${check.fleet_error}`;
    return '';
};
