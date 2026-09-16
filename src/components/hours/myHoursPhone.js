/*
 * «Мои часы» на телефоне — правила без React: месяц, форматы чисел, ячейки
 * календаря и содержимое экрана дня.
 *
 * Настольный календарь (WorkHoursCalendar) объявлен прямо в теле App и
 * считает то же самое внутри себя. Такой компонент пересоздаётся на каждом
 * рендере App, а с ним и его состояние — для окна на компьютере это терпимо,
 * для экрана дня с набранным запросом на телефоне нет. Поэтому телефонный
 * календарь — отдельный модуль со своим состоянием, а общие помощники App
 * (зачтённые часы тренингов, модель расчёта дня) приходят сюда аргументами,
 * а не копией.
 *
 * Пороги и подписи повторяют настольный вид: цвет ячейки — getCellColor,
 * статус нормы — progressStatus, показатели дня — окно дня WorkHoursCalendar.
 */

import { PHONE_MONTHS_BACK, formatPhoneMonth, shiftPhoneMonth } from '../../utils/phoneMonth.js';

export const MY_HOURS_REQUEST_MAX_LENGTH = 500;

/* Ответ сервера на запрос по дню показываем, только если он написан по-русски:
   иначе на экране оказывалось бы «Failed to send request: Unauthorized». */
export const myHoursRequestErrorText = (error) => {
  const data = error?.response?.data;
  const text = String(data?.error || data?.message || '').trim();
  return /[А-Яа-яЁё]/.test(text) ? text : 'Не удалось отправить запрос. Попробуйте ещё раз.';
};

// «Выбор месяца» на компьютере предлагает текущий месяц и одиннадцать прошлых.
export const MY_HOURS_MONTHS_BACK = PHONE_MONTHS_BACK;

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const WEEKDAYS = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
export const MY_HOURS_WEEKDAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

export const MY_HOURS_MARKERS = {
  training: { label: 'Тренинг', className: 'bg-yellow-400' },
  technical: { label: 'Тех. сбой', className: 'bg-violet-500' },
  offline: { label: 'Офлайн', className: 'bg-emerald-500' },
  fines: { label: 'Штраф', className: 'bg-red-400' },
};
const MARKER_ORDER = ['training', 'technical', 'offline', 'fines'];

export const MY_HOURS_SCALE = [
  { key: '1-3', label: '1–3', className: 'bg-green-100' },
  { key: '4-5', label: '4–5', className: 'bg-green-300' },
  { key: '6-7', label: '6–7', className: 'bg-green-500' },
  { key: '8+', label: '8+', className: 'bg-green-700' },
];

const pad2 = (value) => String(value).padStart(2, '0');
const isMap = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const toNumber = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};
const finite = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
};

const RU_2 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const RU_1 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const RU_0 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

// `|| 0` снимает минус у нуля: -0,001 после округления печатался бы «-0».
export const formatHoursNumber = (value) => RU_2.format(Math.round(finite(value) * 100) / 100 || 0);
export const formatHours = (value) => `${formatHoursNumber(value)} ч`;
export const formatPercent = (value) => `${RU_1.format(Math.round(finite(value) * 10) / 10 || 0)} %`;
export const formatMoney = (value) => `${RU_0.format(Math.round(finite(value)) || 0)} ₸`;

const parseMonth = (value) => {
  const match = /^(\d{4})-(\d{2})$/.exec(String(value || ''));
  if (!match) return null;
  const monthIndex = Number(match[2]) - 1;
  if (monthIndex < 0 || monthIndex > 11) return null;
  return { year: Number(match[1]), monthIndex };
};

export const formatMyHoursMonth = (value) => formatPhoneMonth(value);

/* Соседний месяц — общим правилом шапки (utils/phoneMonth.js): список месяцев
   у «Моих часов» и «Моих оценок» один и тот же. */
export const shiftMyHoursMonth = (value, delta, today = new Date()) => shiftPhoneMonth(value, delta, today);

// Те же пороги и подписи, что у progressStatus в «Моих часах» на компьютере.
export const myHoursNormStatus = (percent, norm) => {
  if (!(finite(norm) > 0)) return { label: 'Норма не задана', tone: 'slate' };
  if (percent >= 100) return { label: 'Норма выполнена', tone: 'green' };
  if (percent >= 85) return { label: 'Близко к норме', tone: 'blue' };
  return { label: 'Нужно добрать', tone: 'amber' };
};

/* Цвет ячейки — шкала getCellColor. День без часов на телефоне не заливается
   серым, если он ещё не наступил: иначе месяц вперёд читался бы как пропуски. */
export const myHoursCellTone = ({ hours, isPast }) => {
  if (hours > 7) return 'bg-green-700 text-white';
  if (hours > 5) return 'bg-green-500 text-white';
  if (hours > 3) return 'bg-green-300 text-green-900';
  if (hours > 0) return 'bg-green-100 text-green-800';
  return isPast ? 'bg-slate-100 text-slate-400' : 'text-slate-400';
};

/* «Из чего сложились часы»: база и только те добавки, что есть. Если добавок
   нет, строк нет вовсе — одна «База» повторила бы число из карточки нормы. */
export const buildMyHoursBreakdown = ({ base = 0, training = 0, technical = 0, offline = 0 }) => {
  const extras = [
    { key: 'training', label: 'Тренинги', hours: finite(training), dotClassName: MY_HOURS_MARKERS.training.className },
    { key: 'technical', label: 'Тех. сбои', hours: finite(technical), dotClassName: MY_HOURS_MARKERS.technical.className },
    { key: 'offline', label: 'Офлайн активность', hours: finite(offline), dotClassName: MY_HOURS_MARKERS.offline.className },
  ].filter((row) => row.hours > 0);
  if (!extras.length) return [];
  return [{ key: 'base', label: 'База', hours: finite(base), dotClassName: 'bg-slate-400' }, ...extras];
};

const parseClock = (value) => {
  const match = /^(\d{1,2}):(\d{2})/.exec(String(value || ''));
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
};
const clockLabel = (value) => {
  const match = /^(\d{1,2}):(\d{2})/.exec(String(value || ''));
  return match ? `${pad2(match[1])}:${match[2]}` : '—';
};
const timeRange = (start, end) => `${clockLabel(start)} — ${clockLabel(end)}`;
const spanHours = (start, end) => {
  const from = parseClock(start);
  const to = parseClock(end);
  if (from === null || to === null) return null;
  let diff = to - from;
  if (diff < 0) diff += 24 * 60;
  return diff / 60;
};

// Записи дня — по времени начала: сервер отдаёт их в порядке добавления.
const byStartTime = (a, b) => (parseClock(a?.start_time ?? a?.startTime) ?? 0) - (parseClock(b?.start_time ?? b?.startTime) ?? 0);

// Длительность тех. сбоя или офлайн-активности — порядок полей как в настольном окне.
export const itemDurationHours = (item) => {
  if (!item) return 0;
  const minutes = toNumber(item.duration_minutes ?? item.durationMinutes);
  if (minutes !== null) return Math.max(0, minutes) / 60;
  const hours = toNumber(item.duration_hours ?? item.durationHours);
  if (hours !== null) return Math.max(0, hours);
  return spanHours(item.start_time ?? item.startTime, item.end_time ?? item.endTime) ?? 0;
};

const countedTrainingHours = (list, countTrainingHours) => (
  typeof countTrainingHours === 'function'
    ? finite(countTrainingHours(list, (training) => training && training.count_in_hours !== false))
    : 0
);

/*
 * Месяц ячейками: пустые места до первого числа, затем день за днём. Часы дня —
 * база + зачтённые тренинги + тех. сбои + офлайн, как в настольной ячейке.
 * Формат дней — operator.daily из /api/sv/daily_hours (карта «номер дня → день»).
 */
export const buildMyHoursMonth = ({
  op = null,
  month,
  trainings = [],
  technicalByDay = null,
  offlineByDay = null,
  monthModelCode = 'operator',
  resolveDayModel = null,
  countTrainingHours = null,
  today = new Date(),
}) => {
  const parsed = parseMonth(month);
  if (!parsed) return { leading: 0, cells: [], markers: [] };
  const { year, monthIndex } = parsed;
  const daysInMonth = new Date(year, monthIndex + 1, 0).getDate();
  const leading = (new Date(year, monthIndex, 1).getDay() + 6) % 7;
  const daily = isMap(op?.daily) ? op.daily : {};
  const technicalMap = isMap(technicalByDay) ? technicalByDay : (isMap(op?.technical_issues_by_day) ? op.technical_issues_by_day : {});
  const offlineMap = isMap(offlineByDay) ? offlineByDay : (isMap(op?.offline_activities_by_day) ? op.offline_activities_by_day : {});
  const chatByDay = isMap(op?.chat_metrics_by_day) ? op.chat_metrics_by_day : {};
  const trainingList = Array.isArray(trainings) ? trainings : [];
  const todayTime = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const present = new Set();
  const cells = [];

  for (let day = 1; day <= daysInMonth; day += 1) {
    const key = String(day);
    const date = `${year}-${pad2(monthIndex + 1)}-${pad2(day)}`;
    const entry = daily[key];
    let baseHours = 0;
    let dayData = {};
    if (typeof entry === 'number') {
      baseHours = entry;
      dayData = { work_time: entry };
    } else if (isMap(entry)) {
      baseHours = finite(entry.work_time ?? entry.hours ?? entry.time ?? 0);
      dayData = entry;
    }
    const modelCode = typeof resolveDayModel === 'function' ? resolveDayModel(op, day, monthModelCode) : monthModelCode;
    const isChat = modelCode === 'chat_manager';
    const chatMetrics = isChat ? (dayData.chat_metrics || chatByDay[key] || null) : null;
    if (chatMetrics) {
      dayData = { ...dayData, chat_metrics: chatMetrics, calls: finite(chatMetrics.chats_count ?? dayData.calls ?? 0) };
    }
    const dayTrainings = trainingList.filter((training) => training?.date === date);
    const technical = Array.isArray(technicalMap[key]) ? technicalMap[key] : [];
    const offline = Array.isArray(offlineMap[key]) ? offlineMap[key] : [];
    const trainingHours = countedTrainingHours(dayTrainings, countTrainingHours);
    const technicalHours = technical.reduce((sum, item) => sum + itemDurationHours(item), 0);
    const offlineHours = offline.reduce((sum, item) => sum + itemDurationHours(item), 0);
    const fines = Array.isArray(dayData.fines) ? dayData.fines : [];
    const markers = [
      dayTrainings.length ? 'training' : null,
      technical.length ? 'technical' : null,
      offline.length ? 'offline' : null,
      fines.length ? 'fines' : null,
    ].filter(Boolean);
    markers.forEach((marker) => present.add(marker));
    const time = new Date(year, monthIndex, day).getTime();
    cells.push({
      day,
      date,
      weekday: new Date(year, monthIndex, day).getDay(),
      hours: baseHours + trainingHours + technicalHours + offlineHours,
      baseHours,
      trainingHours,
      technicalHours,
      offlineHours,
      dayData,
      modelCode,
      isChat,
      isToday: time === todayTime,
      isPast: time < todayTime,
      markers,
      trainings: dayTrainings,
      technical,
      offline,
    });
  }

  return { leading, cells, markers: MARKER_ORDER.filter((marker) => present.has(marker)) };
};

export const formatMyHoursDay = (date) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(date || ''));
  if (!match) return { title: '', subtitle: '' };
  const value = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return { title: `${value.getDate()} ${MONTHS_GENITIVE[value.getMonth()]}`, subtitle: WEEKDAYS[value.getDay()] };
};

// «Перерыв», «Разговор»: строка «Ч:ММ» как есть, число больше 12 — минуты, иначе часы.
const formatMetricTime = (value) => {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'string' && /^\d{1,2}:\d{2}$/.test(value)) return value;
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (n > 12) {
    const total = Math.round(n);
    return `${Math.floor(total / 60)}:${pad2(total % 60)}`;
  }
  return formatHours(n);
};
const formatMetricNumber = (value, digits) => {
  const n = toNumber(value);
  if (n === null) return value === null || value === undefined || value === '' ? '—' : String(value);
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(n);
};
const formatSeconds = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n < 60) return `${Math.round(n)} сек`;
  return `${RU_2.format(n / 60)} мин`;
};
const EMPTY_METRIC = /^(—|0|0 ч)$/;
const BONUS_WITH_QUANTITY = new Set(['Приведи друга', 'Съемки']);

/*
 * Экран дня: итог и из чего он сложился, показатели, тренинги, тех. сбои,
 * офлайн, штрафы и бонусы. Пустой день показателей не рисует: у настольного
 * окна там сетка прочерков, на телефоне это полэкрана «—».
 */
export const buildMyHoursDayDetails = ({ cell, op = null, countTrainingHours = null }) => {
  if (!cell) return null;
  const data = isMap(cell.dayData) ? cell.dayData : {};
  const pick = (key) => data[key] ?? null;
  const work = finite(pick('work_time'));
  const efficiency = Number(pick('efficiency') ?? 0);
  const isTezOp = cell.modelCode === 'tez_op';
  const chat = cell.isChat ? (data.chat_metrics || op?.chat_metrics_by_day?.[String(cell.day)] || {}) : {};

  const metrics = cell.isChat ? [
    { key: 'break_time', label: 'Перерыв', value: formatMetricTime(pick('break_time')) },
    { key: 'total_chats', label: 'Чаты', value: formatMetricNumber(chat.chats_count, 0) },
    { key: 'avg_score', label: 'Средняя оценка', value: formatMetricNumber(chat.avg_score, 2) },
    { key: 'response_time', label: 'Время ответа', value: formatSeconds(chat.avg_response_time_seconds) },
    { key: 'transfer_chat', label: 'Передачи чата', value: formatMetricNumber(chat.transfer_chat_count, 0) },
  ] : [
    { key: 'break_time', label: 'Перерыв', value: formatMetricTime(pick('break_time')) },
    { key: 'total_calls', label: 'Звонки', value: formatMetricNumber(pick('calls'), 0) },
    { key: 'efficiency', label: 'Эффективность', value: work > 0 && Number.isFinite(efficiency) ? formatPercent((efficiency / work) * 100) : '—' },
    { key: 'talk_time', label: 'Время в разговоре', value: formatMetricTime(pick('talk_time')) },
    ...(isTezOp ? [
      { key: 'dial_time', label: 'Время набора', value: formatMetricTime(pick('dial_time')) },
      { key: 'chats', label: 'Чаты', value: formatMetricNumber(pick('chats'), 0) },
    ] : []),
  ];

  const trainings = [...(cell.trainings || [])].sort(byStartTime).map((training, index) => {
    const byClock = spanHours(training.start_time, training.end_time);
    const byField = toNumber(training.hours ?? training.duration_hours ?? training.duration ?? training.count);
    return {
      key: training.id ?? `training-${index}`,
      time: timeRange(training.start_time, training.end_time),
      hours: byClock ?? (byField !== null && byField > 0 ? byField : null),
      reason: training.reason || '',
      comment: training.comment || '',
      author: training.created_by_name || '',
      counted: training.count_in_hours !== false,
    };
  });
  const technical = [...(cell.technical || [])].sort(byStartTime).map((item, index) => ({
    key: item.id ?? `technical-${index}`,
    time: timeRange(item.start_time ?? item.startTime, item.end_time ?? item.endTime),
    hours: itemDurationHours(item),
    reason: item.reason || '',
    comment: item.comment || '',
    author: item.created_by_name || '',
  }));
  const offline = [...(cell.offline || [])].sort(byStartTime).map((item, index) => ({
    key: item.id ?? `offline-${index}`,
    time: timeRange(item.start_time ?? item.startTime, item.end_time ?? item.endTime),
    hours: itemDurationHours(item),
    comment: item.comment || '',
    author: item.created_by_name || '',
  }));
  const fines = (Array.isArray(data.fines) ? data.fines : []).map((fine, index) => {
    const amount = finite(fine.amount || fine.fine_amount || 0);
    const reason = fine.reason || fine.fine_reason || '';
    return {
      key: fine.id ?? `fine-${index}`,
      reason,
      amount,
      // Опоздание стоит 50 ₸ за минуту — так минуты показывает и настольное окно.
      minutes: reason === 'Опоздание' ? Math.round(amount / 50) : null,
      comment: fine.comment || '',
    };
  });
  const bonuses = (Array.isArray(data.bonuses) ? data.bonuses : []).map((bonus, index) => {
    const type = String(bonus.type || bonus.bonus_type || '').trim();
    return {
      key: bonus.id ?? `bonus-${index}`,
      type,
      amount: finite(bonus.amount || 0),
      quantity: BONUS_WITH_QUANTITY.has(type) ? (finite(bonus.quantity || 1) || 1) : null,
      friendNames: bonus.friend_names || '',
      links: bonus.video_links || '',
      comment: bonus.comment || '',
    };
  });

  return {
    ...formatMyHoursDay(cell.date),
    total: cell.hours,
    breakdown: buildMyHoursBreakdown({
      base: cell.baseHours,
      training: cell.trainingHours,
      technical: cell.technicalHours,
      offline: cell.offlineHours,
    }),
    metrics: metrics.some((metric) => !EMPTY_METRIC.test(metric.value)) ? metrics : [],
    tezSuccesses: isTezOp ? finite(data.tez_successes ?? op?.tez_successes_by_day?.[String(cell.day)] ?? 0) : null,
    trainings,
    trainingCountedHours: countedTrainingHours(cell.trainings || [], countTrainingHours),
    technical,
    technicalHours: technical.reduce((sum, item) => sum + item.hours, 0),
    offline,
    offlineHours: offline.reduce((sum, item) => sum + item.hours, 0),
    fines,
    finesTotal: fines.reduce((sum, fine) => sum + fine.amount, 0),
    bonuses,
    bonusesTotal: bonuses.reduce((sum, bonus) => sum + bonus.amount, 0),
  };
};
