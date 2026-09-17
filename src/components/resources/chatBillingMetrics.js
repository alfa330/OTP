// «Биллинг чатов»: средние показатели и их вид на экране (задача #343). Правила
// вынесены из ResourceFteView.jsx, чтобы их проверял node-тест. Те же правила повторяет
// выгрузка в bot_schedule2.py (_chat_billing_round_half_up, _chat_billing_minutes,
// _chat_billing_export_metrics): файл обязан сходиться с экраном до десятой.

const ratio = (numerator, denominator) => (
  Number(denominator) > 0 ? Number(numerator || 0) / Number(denominator) : null
);

// «Половина вверх», а не банковское округление: так же округляет выгрузка, иначе
// 135 сек (2,25 мин) на экране было бы 2,3, а в Excel 2,2.
const roundHalfUp = (value, digits) => {
  const scale = 10 ** digits;
  return Math.floor(Number(value) * scale + 0.5) / scale;
};

// Средние всегда «сумма / сколько»: за период — по обращениям всех дней, а не
// среднее средних. Знаменатель у каждого свой: первая реакция — у отвеченных,
// время ответа — у обращений, где оператор отвечал, оценка — у оценённых.
export const chatBillingAverages = (item = {}) => ({
  firstReplySeconds: ratio(item?.first_reply_seconds, item?.answered),
  innerReplySeconds: ratio(item?.inner_reply_seconds, item?.inner_replied),
  rating: ratio(item?.rating_sum, item?.rated),
  sl: ratio(item?.answered_sl, item?.chats),
});

// Время — только в минутах с одним знаком: «2,5», «7», «12» (требование постановки).
export const chatBillingMinutes = (seconds) => {
  if (seconds === null || seconds === undefined || seconds === '') return null;
  const number = Number(seconds);
  return Number.isFinite(number) ? roundHalfUp(number / 60, 1) : null;
};

const MINUTES_FORMAT = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
// Оценка — с одним знаком, как в ежедневном отчёте СЗоВ («4,7»).
const RATING_FORMAT = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const formatChatBillingMinutes = (seconds) => {
  const minutes = chatBillingMinutes(seconds);
  return minutes === null ? '—' : MINUTES_FORMAT.format(minutes);
};

// С единицей — для карточек, где подписи колонки над числом нет.
export const formatChatBillingMinutesUnit = (seconds) => {
  const label = formatChatBillingMinutes(seconds);
  return label === '—' ? label : `${label} мин`;
};

export const formatChatBillingRating = (value) => (
  value === null || value === undefined || !Number.isFinite(Number(value))
    ? '—'
    : RATING_FORMAT.format(roundHalfUp(value, 1))
);

// Час «Группировки» — промежуток, а не точка: в строку 09:00–10:00 идут обращения,
// начавшиеся с 09:00:00 до 09:59:59.
export const chatBillingHourLabel = (hour) => {
  const start = Math.max(0, Math.min(23, Math.trunc(Number(hour) || 0)));
  const pad = (value) => String(value).padStart(2, '0');
  return `${pad(start)}:00–${pad((start + 1) % 24)}:00`;
};
