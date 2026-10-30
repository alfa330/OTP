// «Биллинг Oktell → Группировка»: почасовая таблица дня по образцу таблицы владельца.
// Правила вынесены из ResourceFteView.jsx, чтобы их проверял node-тест. Те же правила
// повторяет выгрузка в bot_schedule2.py (_OKTELL_BILLING_GROUPING_COLUMNS,
// _oktell_billing_grouping_values) — совпадение подписей и порога сторожит pytest.

// Подписи и порядок колонок — как в таблице владельца: по ней раздел и сделан,
// и одни и те же слова на экране и в выгрузке не заставляют сверять, что есть что.
// hint — подсказка при наведении на шапку: как считается колонка.
export const BILLING_GROUPING_COLUMNS = [
  { key: 'hour', label: 'С' },
  { key: 'arrived', label: 'Получено' },
  { key: 'served', label: 'Принято' },
  { key: 'lost', label: 'Потеряно' },
  { key: 'ar', label: '% Неотв' },
  { key: 'talk', label: 'Средн. Прод.', hint: 'Средняя длина принятого звонка, как в Oktell: приветствие, очередь и разговор' },
  { key: 'wait', label: 'Средн. время ожидания', hint: 'Как в отчёте Oktell: среднее ожидание в очереди принятых звонков плюс среднее ожидание неотвеченных' },
  { key: 'forecast', label: 'Прогноз смен' },
  { key: 'planned', label: 'Запланировано смен' },
  { key: 'fact', label: 'Факт смен' },
  { key: 'delta', label: 'Разница факта от прогноза' },
  { key: 'comment', label: 'Комментарии' },
];

// Правая половина таблицы — про смены, левая — про звонки; смены идут на подложке.
export const BILLING_GROUPING_SHIFT_KEYS = new Set(['forecast', 'planned', 'fact', 'delta']);

// Доля неотвеченных, ВЫШЕ которой час заливается красным. В таблице владельца 4 %
// остаются без заливки, а 8 % уже залиты; граница совпадает с зелёной зоной AR раздела.
export const BILLING_GROUPING_AR_ALERT = 0.05;

// Число смен или null. Ноль смен и «данных нет» — разные вещи: первое — цифра,
// второе — прочерк (статусы за час ещё не приехали, у дня нет истории для прогноза).
const shiftValue = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.trunc(number) : null;
};

// Средние — целые секунды УСЕЧЕНИЕМ, как в отчёте Oktell, таблице владельца и почасовой
// отбивке табло: округление расходилось бы с ними на секунду в каждой второй строке.
// Суммы приходят с долями секунды: округлённые до целых давали ту же ошибку на секунду.
const truncatedAverage = (sum, count) => (
  Number(count) > 0 ? Math.trunc(Number(sum || 0) / Number(count)) : null
);

// Ожидание — как в таблице СЗоВ: среднее у принятых ПЛЮС среднее у неотвеченных, каждое
// усечено отдельно (это две колонки отчёта Oktell). Сверено на их таблице за 14.09: 24 часа
// из 24 (задача #380). Нет неотвеченных — остаётся ожидание принятых, и наоборот.
const groupingWait = (item, served, lost) => {
  const waitOk = truncatedAverage(item.wait_ok_seconds, served);
  const waitLost = truncatedAverage(item.wait_lost_seconds, lost);
  return waitOk === null && waitLost === null ? null : (waitOk || 0) + (waitLost || 0);
};

export const billingGroupingRow = (item = {}) => {
  const arrived = Math.max(0, Number(item.arrived || 0));
  const served = Math.max(0, Number(item.served || 0));
  const lost = Math.max(0, Number(item.lost || 0));
  const ar = arrived > 0 ? lost / arrived : null;
  const forecast = shiftValue(item.forecast);
  const fact = shiftValue(item.fact);
  return {
    hour: Number(item.hour),
    arrived,
    served,
    lost,
    ar,
    arAlert: ar !== null && ar > BILLING_GROUPING_AR_ALERT,
    // «Средн. Прод.» — длина принятого звонка целиком, как «Среднее время разговора» Oktell.
    talk: truncatedAverage(item.talk_seconds, served),
    wait: groupingWait(item, served, lost),
    forecast,
    planned: shiftValue(item.planned),
    fact,
    // Правило владельца: разница считается от ПРОГНОЗА, а не от плана.
    delta: forecast === null || fact === null ? null : fact - forecast,
    comment: String(item.comment || '').trim(),
    commentAuthor: String(item.comment_author || '').trim(),
  };
};

// Целый процент, как в таблице владельца: 11 из 29 — «38%», а не «37,9%».
export const billingGroupingPercent = (ratio) => (
  ratio === null || ratio === undefined ? '—' : `${Math.round(Number(ratio) * 100)}%`
);

// Соседние часы с одинаковым комментарием — одна объединённая ячейка. Комментарий
// хранится по часу, поэтому «блок» — это подряд идущие часы с одним текстом.
// Пустые часы не объединяются: у каждого своя ячейка, чтобы комментарий можно было
// начать с любого часа.
export const billingGroupingCommentBlocks = (rows = []) => {
  const blocks = [];
  rows.forEach((row, index) => {
    const hour = Number(row?.hour);
    const comment = String(row?.comment || '').trim();
    const previous = blocks[blocks.length - 1];
    if (comment && previous && previous.comment === comment && previous.hourTo === hour - 1) {
      previous.hourTo = hour;
      previous.span += 1;
      return;
    }
    blocks.push({ index, hourFrom: hour, hourTo: hour, span: 1, comment });
  });
  return blocks;
};
