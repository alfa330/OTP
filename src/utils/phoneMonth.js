/*
 * Месяц в шапке раздела на телефоне: подпись и шаг стрелками.
 *
 * Разделы «Мои часы» и «Мои оценки» выбирают месяц из ОДНОГО списка —
 * getMonthOptions в App.jsx отдаёт двенадцать месяцев, считая от текущего
 * назад. Стрелки обязаны ходить ровно по нему: за краем списка раздел данных
 * не запрашивает, поэтому соседний месяц там — null, и стрелка гаснет.
 *
 * Правила живут здесь, а не в модуле раздела: вторая копия шага по месяцам
 * разъехалась бы со списком на первой же его правке.
 */

export const PHONE_MONTHS_BACK = 11;

const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

const pad2 = (value) => String(value).padStart(2, '0');

const parseMonth = (value) => {
  const match = /^(\d{4})-(\d{2})$/.exec(String(value || ''));
  if (!match) return null;
  const monthIndex = Number(match[2]) - 1;
  if (monthIndex < 0 || monthIndex > 11) return null;
  return { year: Number(match[1]), monthIndex };
};

export const formatPhoneMonth = (value) => {
  const parsed = parseMonth(value);
  return parsed ? `${MONTHS[parsed.monthIndex]} ${parsed.year}` : '';
};

export const shiftPhoneMonth = (value, delta, today = new Date()) => {
  const parsed = parseMonth(value);
  if (!parsed) return null;
  const index = parsed.year * 12 + parsed.monthIndex + delta;
  const newest = today.getFullYear() * 12 + today.getMonth();
  if (index > newest || index < newest - PHONE_MONTHS_BACK) return null;
  return `${Math.floor(index / 12)}-${pad2((index % 12) + 1)}`;
};
