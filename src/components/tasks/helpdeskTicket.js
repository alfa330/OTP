/*
 * Заявка в IT из раздела «Задачи» (владелец 27.11.2026): «В разделе тех причины
 * есть возможность создавать тикеты для айти, его надо полностью перенести в
 * раздел задачи при отметке it helpdesk… с таким же функционалом что бы тикеты
 * создавались там же и попадали в их же доску».
 *
 * Выбрали в форме задачи доску IT Helpdesk — форма становится заявкой: человек
 * описывает проблему своими словами, ИИ (те же ручки /api/it_tickets, что были в
 * «Тех причинах») подбирает тему из каталога, срочность по массовости, поля
 * «что уточнить» с выбором РМ на схеме и советы «что попробовать до заявки», а
 * потом собирает короткий текст. Уходит заявка обычной задачей на доску ITH:
 * заголовок — заголовок заявки, описание — тема и текст, срочность — срочность.
 *
 * Здесь только правила, без React: их гоняет node --test
 * (tests/helpdesk_ticket.test.mjs). Вид — HelpdeskTicketForm.jsx.
 */
import { stripTelegramTags } from '../technical/telegramText.js';
import { cabinetLabel, cabinetSeatNumbers } from '../technical/workplaceCabinets.js';

/* Доску узнаём по ключу. Тот же ключ — в task_projects.HELPDESK_PROJECT_CODE на
   сервере (тест сверяет оба): по нему сервер пускает людей доски к ИИ заявок и
   пишет заявку в журнал it_tickets. Флага в ответе сервера нарочно нет — фронт
   выкладывается при пуше, а бэкенд позже, кнопкой, и в этом окне форма должна
   узнавать доску и у старого сервера. Сменят ключ доски в «Проектах» — сменить
   и здесь, и на сервере. */
export const HELPDESK_PROJECT_CODE = 'ITH';

export const isHelpdeskProject = (project) => (
  String(project?.code || '').trim().toUpperCase() === HELPDESK_PROJECT_CODE
);

/* Профили каталога — по отделу заявителя (как в «Тех причинах»): у ОП и СЗоВ
   свои темы и свои инструкции к ИИ. Подпись в сегменте короткая: «Отдел продаж
   (ОП)» из каталога в сегмент не влезает. */
export const HELPDESK_PROFILES = ['op', 'szov'];
export const HELPDESK_PROFILE_LABELS = Object.freeze({ op: 'ОП', szov: 'СЗоВ' });
export const helpdeskProfileLabel = (profile) => HELPDESK_PROFILE_LABELS[profile] || String(profile || '');

/* Срочность. У ИИ четыре уровня (low/medium/high/critical — правило массовости в
   промпте), у задачи — три. Низкий и средний — обычная задача: на доске их и
   раньше не различали бы. В журнал заявок уходит уровень ИИ, а если срочность
   поменяли руками — ближайший ей уровень. */
export const TICKET_TASK_PRIORITY = Object.freeze({
  low: 'normal',
  medium: 'normal',
  high: 'urgent',
  critical: 'critical',
});
export const TASK_TICKET_PRIORITY = Object.freeze({
  normal: 'medium',
  urgent: 'high',
  critical: 'critical',
});

export const HELPDESK_FIELD_TYPES = ['text', 'textarea', 'select', 'date', 'time', 'number', 'workplace'];

export const emptyHelpdeskTicket = () => ({
  profile: '',
  category: '',
  subcategory: '',
  // Пояснение ИИ, если он поправил тему, выбранную человеком.
  note: '',
  description: '',
  // Поля, которые подобрал ИИ, и их значения (ключ поля → строка).
  fields: [],
  values: {},
  // Советы «что попробовать до заявки» и номера отмеченных.
  checks: [],
  done: [],
  priority: 'normal',
  aiPriority: '',
  title: '',
  titleEdited: false,
  // Текст заявки в разметке Telegram (<b>, <i>, <code>) — так его пишет ИИ.
  text: '',
  composed: false,
  // Отпечаток того, из чего текст собран: поменяли данные — текст устарел.
  composedSig: '',
  // Описание, из которого собран текст: дописали после сборки — дописанное не потеряется.
  composedDescription: '',
});

const trimmed = (value) => String(value ?? '').trim();
const norm = (value) => trimmed(value).toLowerCase();

export const catalogCategories = (catalog, profile) => {
  const list = catalog?.[profile]?.categories;
  return Array.isArray(list) ? list.filter((category) => trimmed(category?.name)) : [];
};

export const catalogItems = (catalog, profile, categoryName) => {
  const category = catalogCategories(catalog, profile).find((item) => item.name === categoryName);
  return Array.isArray(category?.items) ? category.items.filter((item) => trimmed(item)) : [];
};

/* То, что человек написал до выбора доски («Что нужно сделать?» и описание), —
   уже описание проблемы: при переходе в заявку оно не пропадает. */
export const seedTicketDescription = (values) => [values?.subject, values?.description]
  .map(trimmed)
  .filter(Boolean)
  .join('\n');

/* ─── Поля, которые подобрал ИИ ─── */

const FIELD_KEY_RE = /^[A-Za-z0-9_\-.]{1,64}$/;

export function sanitizeTicketFields(fields) {
  const seen = new Set();
  const out = [];
  (Array.isArray(fields) ? fields : []).forEach((field) => {
    const key = trimmed(field?.key);
    if (!key || !FIELD_KEY_RE.test(key) || seen.has(key)) return;
    seen.add(key);
    const type = norm(field?.type);
    // ИИ пишет подписи как попало («рабочее место / № РМ») — подпись поля с заглавной.
    const label = trimmed(field?.label) || key;
    out.push({
      key,
      label: label.charAt(0).toUpperCase() + label.slice(1),
      type: HELPDESK_FIELD_TYPES.includes(type) ? type : 'text',
      required: Boolean(field?.required),
      placeholder: trimmed(field?.placeholder),
      hint: trimmed(field?.hint),
      options: Array.isArray(field?.options) ? field.options.map(trimmed).filter(Boolean) : [],
      value: field?.value === undefined || field?.value === null ? '' : String(field.value),
    });
  });
  return out;
}

export const missingTicketFields = (fields, values) => (Array.isArray(fields) ? fields : [])
  .filter((field) => field?.required && !trimmed(values?.[field.key]));

/* Куда писать отмеченные советы: поле «что уже пробовали», если ИИ его подобрал,
   иначе ключ tried — поля всё равно уходят в ИИ при сборке, и он впишет их в текст. */
const TRIED_RE = /пробов|уже\s*дел|tried|attempt/i;
export const triedFieldKey = (fields) => (
  (Array.isArray(fields) ? fields : []).find((field) => TRIED_RE.test(`${field?.key || ''} ${field?.label || ''}`))?.key
  || 'tried'
);

const CHECKS_MARK = 'Пробовали, не помогло:';

export const checksAnswer = (checks, done) => {
  const marked = new Set(Array.isArray(done) ? done : []);
  const list = (Array.isArray(checks) ? checks : []).filter((_, index) => marked.has(index));
  return list.length ? `${CHECKS_MARK} ${list.join('; ')}` : '';
};

/* Отметить совет: отмеченное — и есть ответ на «что уже пробовали», он
   собирается сам, пересказывать руками не нужно. То, что человек написал в
   этом поле сам («гарнитуру переподключали»), остаётся впереди: дописываем
   и переписываем только свою часть после «Пробовали, не помогло:». */
export function toggleTicketCheck(ticket, index) {
  const done = new Set(Array.isArray(ticket?.done) ? ticket.done : []);
  if (done.has(index)) done.delete(index); else done.add(index);
  const nextDone = [...done].sort((a, b) => a - b);
  const key = triedFieldKey(ticket?.fields);
  const current = String(ticket?.values?.[key] ?? '');
  const markAt = current.indexOf(CHECKS_MARK);
  const own = (markAt >= 0 ? current.slice(0, markAt) : current).replace(/[\s;,.]+$/, '').trim();
  const answer = checksAnswer(ticket?.checks, nextDone);
  return {
    done: nextDone,
    values: { ...(ticket?.values || {}), [key]: [own, answer].filter(Boolean).join('; ') },
  };
}

/* ─── Рабочие места на схеме ───
   Значение поля — читаемая строка «РМ 3, 5 (Тех поддержка); РМ 70 (ОП · 17 этаж)»:
   так её читает и ИИ, и сисадмин в задаче. Выбор на схеме восстанавливается из
   строки (ИИ мог распознать «РМ 16» в описании): каждое число привязываем к
   первому кабинету, где такой номер есть. */

export const seatKey = (cabinetId, number) => `${cabinetId}:${number}`;

const numbersIn = (text) => (String(text || '').match(/\d+/g) || []).map(Number).filter(Number.isFinite);

export function selectionFromValue(value, cabinets) {
  const selected = new Set();
  const list = Array.isArray(cabinets) ? cabinets : [];
  const text = String(value ?? '');
  if (!text.trim() || !list.length) return selected;
  const index = list.map((cabinet) => ({ cabinet, label: `(${cabinetLabel(cabinet)})`, seats: new Set(cabinetSeatNumbers(cabinet)) }));
  /* Номера 28–30 есть и в «Тех поддержке», и в «ОП 3»: часть строки с названием
     кабинета («РМ 28 (ОП 3 (Честный) · 18 этаж)») читаем в этом кабинете —
     иначе выбранное в ОП 3 место загоралось бы в тех-поддержке. Цифры самого
     названия («18 этаж») номерами РМ не считаем. */
  text.split(';').forEach((part) => {
    const owner = index
      .filter((item) => part.includes(item.label))
      .sort((a, b) => b.label.length - a.label.length)[0];
    if (owner) {
      numbersIn(part.replace(owner.label, ' ')).forEach((number) => {
        if (owner.seats.has(number)) selected.add(seatKey(owner.cabinet.id, number));
      });
      return;
    }
    numbersIn(part).forEach((number) => {
      const hit = index.find((item) => item.seats.has(number));
      if (hit) selected.add(seatKey(hit.cabinet.id, number));
    });
  });
  return selected;
}

/* Схема — кабинеты отдела заявки: у ОП свои три кабинета, у СЗоВ тех-поддержка.
   В «Тех причинах» схема шла по отделу самого супервайзера, и СВ, выбравший
   профиль другого отдела, видел не те места. */
export const cabinetsForProfile = (profile, cabinets) => {
  const list = Array.isArray(cabinets) ? cabinets : [];
  if (profile === 'op') return list.filter((cabinet) => cabinet.group === 'sales');
  if (profile === 'szov') return list.filter((cabinet) => cabinet.group === 'support');
  return list;
};

export function workplaceValue(selected, cabinets) {
  const keys = [...(selected || [])];
  return (Array.isArray(cabinets) ? cabinets : []).map((cabinet) => {
    const numbers = keys
      .map((key) => String(key).split(':'))
      .filter(([cabinetId]) => cabinetId === cabinet.id)
      .map(([, number]) => Number(number))
      .filter(Number.isFinite)
      .sort((a, b) => a - b);
    return numbers.length ? `РМ ${numbers.join(', ')} (${cabinetLabel(cabinet)})` : '';
  }).filter(Boolean).join('; ');
}

/* Масштаб для оценки срочности: сколько РМ затронуто относительно кабинета и
   всей зоны заявителя. Текст уходит в ИИ (MASS_CONTEXT), правило массовости — в
   промпте: ≥50% кабинета — критично. */
export function massContext(cabinets, fields, values) {
  const list = Array.isArray(cabinets) ? cabinets : [];
  if (!list.length) return '';
  const index = list.map((cabinet) => ({ cabinet, seats: new Set(cabinetSeatNumbers(cabinet)) }));
  const total = index.reduce((sum, item) => sum + item.seats.size, 0);
  const numbers = new Set();
  (Array.isArray(fields) ? fields : [])
    .filter((field) => norm(field?.type) === 'workplace')
    .forEach((field) => {
      (String(values?.[field.key] ?? '').match(/\d+/g) || []).map(Number).filter(Number.isFinite)
        .forEach((number) => numbers.add(number));
    });
  const zone = `В зоне ответственности супервайзера ~${total} РМ.`;
  if (!numbers.size) {
    return total > 0 ? `${zone} Конкретные РМ на схеме не выбраны — оцени массовость по описанию.` : '';
  }
  const perCabinet = new Map();
  let affected = 0;
  numbers.forEach((number) => {
    const hit = index.find((item) => item.seats.has(number));
    if (!hit) return;
    perCabinet.set(hit.cabinet.id, (perCabinet.get(hit.cabinet.id) || 0) + 1);
    affected += 1;
  });
  if (!affected) return total > 0 ? zone : '';
  const share = total > 0 ? Math.round((affected / total) * 100) : 0;
  const parts = index
    .filter((item) => perCabinet.get(item.cabinet.id))
    .map((item) => {
      const count = perCabinet.get(item.cabinet.id);
      const capacity = item.seats.size;
      return `${cabinetLabel(item.cabinet)} — ${count}/${capacity} РМ (${capacity ? Math.round((count / capacity) * 100) : 0}%)`;
    });
  return `${zone} Затронуто ${affected} РМ (~${share}% отдела). По кабинетам: ${parts.join('; ')}.`;
}

/* ─── ИИ ─── */

export const AI_ERROR_MESSAGES = Object.freeze({
  ai_unavailable: 'ИИ перегружен — попробуйте ещё раз',
  ai_timeout: 'ИИ долго отвечает — попробуйте ещё раз',
  ai_blocked: 'ИИ не понял описание — уточните его',
  json_parse_error: 'ИИ ответил непонятно — попробуйте ещё раз',
  ai_failed: 'ИИ временно недоступен',
});
export const aiErrorText = (code) => AI_ERROR_MESSAGES[code] || 'ИИ не смог разобрать заявку';

export const ticketAiRequest = (ticket, mode, cabinets) => ({
  mode,
  profile: ticket?.profile || '',
  category: ticket?.category || '',
  subcategory: ticket?.subcategory || '',
  description: ticket?.description || '',
  fields: ticket?.values || {},
  mass_context: massContext(cabinets, ticket?.fields, ticket?.values),
});

/* Отпечаток данных, из которых собран текст: описание, тема и поля. Срочность
   и заголовок в него не входят — текст заявки от них не зависит. */
export const ticketSignature = (ticket) => JSON.stringify([
  trimmed(ticket?.description),
  trimmed(ticket?.category),
  trimmed(ticket?.subcategory),
  Object.entries(ticket?.values || {})
    .map(([key, value]) => [key, trimmed(value)])
    .filter(([, value]) => value)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
]);

/* Текст собран и с тех пор ничего не меняли. */
export const isTicketTextFresh = (ticket) => Boolean(ticket?.composed)
  && Boolean(trimmed(ticket?.text))
  && ticket?.composedSig === ticketSignature(ticket);

/* Перед отправкой текст надо собрать (или пересобрать): ИИ уже подбирал поля, а
   текст не собран или устарел — иначе в задачу ушло бы описание без того, что
   человек дозаполнил (номер РМ, отмеченные проверки). */
export const ticketNeedsCompose = (ticket) => (Array.isArray(ticket?.fields) && ticket.fields.length > 0)
  && !isTicketTextFresh(ticket);

/**
 * Ответ ИИ → изменения заявки. Тему из ответа берём, только если она есть в
 * действующем каталоге (с точной строкой оттуда): иначе список показал бы
 * пустоту. Тему, выбранную человеком, ИИ меняет, только когда сам говорит, что
 * поправил её (category_adjusted), и тогда же объясняет — note.
 * @returns {{patch: object, status: string}}
 */
export function applyTicketAiResult(ticket, result, catalog) {
  const current = ticket || emptyHelpdeskTicket();
  const categories = catalogCategories(catalog, current.profile);
  const findCategory = (name) => (trimmed(name) ? categories.find((item) => norm(item.name) === norm(name)) : null);
  const adjusted = Boolean(result?.category_adjusted);
  const patch = {};

  let category = current.category;
  const categoryMatch = findCategory(result?.category);
  if (categoryMatch && (adjusted || !current.category)) {
    category = categoryMatch.name;
    patch.category = categoryMatch.name;
  }
  let subApplied = false;
  if (trimmed(result?.subcategory)) {
    const owner = findCategory(category);
    const items = Array.isArray(owner?.items) ? owner.items : [];
    const subMatch = items.find((item) => norm(item) === norm(result.subcategory));
    if (subMatch && (adjusted || !current.subcategory || category !== current.category)) {
      patch.subcategory = subMatch;
      subApplied = true;
    }
  }
  // Тема сменилась, а тип из ответа к ней не подошёл — прежний тип чужой теме не оставляем.
  if (category !== current.category && !subApplied) patch.subcategory = '';
  patch.note = adjusted && (categoryMatch || subApplied) && trimmed(result?.category_adjustment_note)
    ? trimmed(result.category_adjustment_note)
    : '';

  const aiPriority = norm(result?.priority);
  if (TICKET_TASK_PRIORITY[aiPriority]) {
    patch.priority = TICKET_TASK_PRIORITY[aiPriority];
    patch.aiPriority = aiPriority;
  }

  const fields = sanitizeTicketFields(result?.form?.fields);
  if (fields.length) {
    // Поля нового ответа и те прежние, что уже заполнены: заполненное не пропадает.
    const keys = new Set(fields.map((field) => field.key));
    const keptFilled = (current.fields || []).filter((field) => !keys.has(field.key) && trimmed(current.values?.[field.key]));
    patch.fields = [...fields, ...keptFilled];
    const values = { ...(current.values || {}) };
    fields.forEach((field) => {
      if (values[field.key] === undefined || (!trimmed(values[field.key]) && field.value)) values[field.key] = field.value;
    });
    patch.values = values;
  }

  if (Array.isArray(result?.checks)) {
    const checks = result.checks.map(trimmed).filter(Boolean);
    // Отметки переживают повторный ответ, если совет тот же — по тексту, не по номеру.
    const doneTexts = new Set((current.done || []).map((index) => current.checks?.[index]).filter(Boolean));
    patch.checks = checks;
    patch.done = checks.map((check, index) => (doneTexts.has(check) ? index : -1)).filter((index) => index >= 0);
  }

  const markdown = trimmed(result?.ticket?.markdown) || trimmed(result?.ticket?.summary);
  if (trimmed(result?.ticket?.title) && !current.titleEdited) patch.title = trimmed(result.ticket.title).slice(0, 255);

  const status = String(result?.status || 'draft');
  if (status === 'ready') {
    if (markdown) patch.text = markdown;
    patch.composed = Boolean(markdown || trimmed(current.text));
    patch.composedSig = ticketSignature({ ...current, ...patch });
    patch.composedDescription = trimmed(current.description);
  } else if (status === 'need_more_info') {
    patch.composed = false;
  } else if (markdown && !trimmed(current.text)) {
    patch.text = markdown;
    patch.composedDescription = trimmed(current.description);
  }
  return { patch, status };
}

/* Смена отдела заявки: у отдела свой каталог тем и своя схема кабинетов, поэтому
   разбор ИИ начинается заново — как в «Тех причинах». Иначе в задачу ушло бы
   «РМ 27 (Тех поддержка)» под отделом ОП, а схема ОП стояла бы пустой. Описание и
   срочность остаются: их писал и выбирал человек. */
export const ticketProfileChange = (profile) => ({
  profile,
  category: '',
  subcategory: '',
  note: '',
  fields: [],
  values: {},
  checks: [],
  done: [],
  aiPriority: '',
  title: '',
  titleEdited: false,
  text: '',
  composed: false,
  composedSig: '',
  composedDescription: '',
});

/* Исполнители при смене доски в окне «Новая задача». Заявку в IT берёт любой
   исполнитель доски — на доске IT Helpdesk сразу «Все». А уходя с неё, «Все»
   с собой не уносим: на другой доске это разослало бы обычную задачу всем её
   исполнителям — выбор там начинается с чистого поля. */
export const assigneeBaseForBoardChange = (values, currentProject, nextProject) => {
  if (isHelpdeskProject(nextProject)) return { ...values, assignAll: true };
  if (isHelpdeskProject(currentProject)) return { ...values, assignAll: false, assigneeIds: [] };
  return values;
};

/* «Весь кабинет»: отметить все места активного кабинета (массовый сбой) или,
   если уже отмечены все, снять их. Места других кабинетов не трогаем. */
export function toggleCabinetSeats(selected, cabinet) {
  const next = new Set(selected || []);
  const seats = cabinetSeatNumbers(cabinet);
  if (!cabinet || !seats.length) return next;
  const all = seats.every((number) => next.has(seatKey(cabinet.id, number)));
  seats.forEach((number) => {
    const key = seatKey(cabinet.id, number);
    if (all) next.delete(key); else next.add(key);
  });
  return next;
}

/* ─── Задача из заявки ─── */

export const ticketTopic = (category, subcategory) => [category, subcategory]
  .map(trimmed)
  .filter(Boolean)
  .join(' → ');

/* Что прочтёт сисадмин: собранный текст без разметки, а без ИИ — описание как есть. */
export const ticketBodyText = (ticket) => (
  trimmed(stripTelegramTags(ticket?.text || '').replace(/\r\n?/g, '\n')) || trimmed(ticket?.description)
);

const flat = (value) => trimmed(value).replace(/\s+/g, ' ').toLowerCase();

/* Дозаполненное после сборки текста. Текст собран и свежий — в нём уже всё,
   ИИ сам вписал поля. Текста нет или он устарел (дозаполнили номер РМ, а
   «Пересобрать» не нажали; ИИ недоступен) — поля, которых в тексте нет, идут
   строками «подпись: значение» под ним: ничего из заполненного не теряется. */
export function ticketExtraLines(ticket) {
  if (isTicketTextFresh(ticket)) return [];
  const body = flat(ticketBodyText(ticket));
  const fields = Array.isArray(ticket?.fields) ? ticket.fields : [];
  const triedKey = triedFieldKey(fields);
  return Object.entries(ticket?.values || {})
    .map(([key, value]) => [key, trimmed(value)])
    .filter(([, value]) => value && !body.includes(flat(value)))
    .map(([key, value]) => {
      // Ответ из отмеченных советов подписан сам («Пробовали, не помогло: …»).
      if (key === triedKey && /^пробовали/i.test(value)) return value;
      const label = fields.find((field) => field.key === key)?.label
        || (key === triedKey ? 'Уже пробовали' : key);
      return `${label}: ${value}`;
    });
}

/* Описание, дописанное после сборки текста ИИ («и микрофон на РМ 6 тоже»): в
   собранный текст оно уже не попало, а «Пересобрать» не нажали. Тогда описание
   идёт в задачу целиком, своим блоком, — какая часть новая, не угадать. */
export function ticketDescriptionAddendum(ticket) {
  const description = trimmed(ticket?.description);
  if (!description || !trimmed(ticket?.text) || isTicketTextFresh(ticket)) return '';
  return description === trimmed(ticket?.composedDescription) ? '' : description;
}

export function ticketTaskDescription(ticket) {
  const topic = ticketTopic(ticket?.category, ticket?.subcategory);
  const addendum = ticketDescriptionAddendum(ticket);
  const extra = ticketExtraLines(ticket);
  return [
    topic ? `Тема: ${topic}` : '',
    ticketBodyText(ticket),
    addendum ? `Со слов заявителя:\n${addendum}` : '',
    extra.length ? ['Детали:', ...extra].join('\n') : '',
  ].filter(Boolean).join('\n\n');
}

const SUBJECT_MAX = 120;

/* Заголовок задачи — заголовок заявки от ИИ; без ИИ — первая строка описания,
   по границе слова. */
export function ticketTaskSubject(ticket) {
  const title = trimmed(String(ticket?.title || '').replace(/\s+/g, ' '));
  if (title) return title.slice(0, 255);
  const source = trimmed(ticket?.description) || trimmed(stripTelegramTags(ticket?.text || ''));
  let line = source.split(/\r?\n/).map(trimmed).find(Boolean) || '';
  line = line.replace(/\s+/g, ' ');
  if (line.length > SUBJECT_MAX) {
    const cut = line.slice(0, SUBJECT_MAX);
    const space = cut.lastIndexOf(' ');
    line = `${(space > SUBJECT_MAX * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,.;:—-]+$/, '')}…`;
  }
  return line ? line.charAt(0).toUpperCase() + line.slice(1) : '';
}

/* Чего не хватает, чтобы отправить. Ключи — якоря полей (data-issue-anchor):
   по ним подвал формы листает к первому пустому. */
export function ticketIssues(ticket) {
  const issues = [];
  if (!ticketBodyText(ticket)) issues.push({ key: 'ticket-description', text: 'Опишите, что случилось' });
  missingTicketFields(ticket?.fields, ticket?.values).forEach((field) => {
    issues.push({ key: `ticket-field-${field.key}`, text: 'Без этого заявку не возьмут в работу' });
  });
  return issues;
}

/* Журнал заявок (it_tickets): тема, профиль, срочность в шкале ИИ и
   заполненные поля. Заголовок и текст сервер берёт из самой задачи. */
export function ticketLogPayload(ticket) {
  const fields = {};
  Object.entries(ticket?.values || {}).forEach(([key, value]) => {
    const text = trimmed(value);
    if (key && text) fields[key] = text.slice(0, 2000);
  });
  const aiPriority = norm(ticket?.aiPriority);
  const priority = aiPriority && TICKET_TASK_PRIORITY[aiPriority] === ticket?.priority
    ? aiPriority
    : (TASK_TICKET_PRIORITY[ticket?.priority] || 'medium');
  return {
    profile: HELPDESK_PROFILES.includes(ticket?.profile) ? ticket.profile : '',
    category: trimmed(ticket?.category),
    subcategory: trimmed(ticket?.subcategory),
    priority,
    fields,
  };
}

/* Скриншот из буфера обмена (Ctrl+V в описании) — вложение заявки. Имя
   у вставленной картинки браузер даёт одинаковое («image.png»), поэтому
   подписываем временем: два снимка подряд не схлопнутся в один. */
export function clipboardFiles(clipboardData, now = new Date()) {
  /* Скопировали ячейки Excel или абзац Word — Office кладёт рядом с текстом ещё и
     картинку выделения. Это вставка текста, а не скриншот: вложением её не берём. */
  const types = Array.from(clipboardData?.types || []);
  const text = typeof clipboardData?.getData === 'function' ? clipboardData.getData('text/plain') : '';
  if (text || types.includes('text/plain') || types.includes('text/html')) return [];
  const items = Array.from(clipboardData?.items || []);
  const stamp = `${String(now.getHours()).padStart(2, '0')}-${String(now.getMinutes()).padStart(2, '0')}-${String(now.getSeconds()).padStart(2, '0')}`;
  return items
    .filter((item) => item?.kind === 'file')
    .map((item) => item.getAsFile?.())
    .filter(Boolean)
    .map((file, index) => {
      const isImage = String(file.type || '').startsWith('image/');
      const generic = !file.name || /^image\.(png|jpe?g|gif|webp)$/i.test(file.name);
      if (!isImage || !generic || typeof File === 'undefined') return file;
      const ext = (String(file.type).split('/')[1] || 'png').replace('jpeg', 'jpg');
      return new File([file], `Снимок ${stamp}${index ? `-${index + 1}` : ''}.${ext}`, { type: file.type });
    });
}
