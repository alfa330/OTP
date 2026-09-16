/*
 * Правила телефонного вида «Моих оценок» — без React и без разметки.
 *
 * Здесь то, что отвечает на вопросы «какая это оценка», «какого цвета балл»,
 * «что показать по критериям» — ровно те же ответы, что даёт настольная
 * разметка раздела в App.jsx. Пороги балла и условие «можно запросить
 * переоценку» повторяют её дословно; расхождение ловит
 * tests/test_my_evaluations_mobile.py, который читает обе стороны текстом.
 *
 * Правил самого качества (как считается балл, кто и когда оценивает) здесь
 * нет вовсе: их знает сервер, а средний балл считает App.
 */

/* Вердикт по критерию. Подписи и тона — те же, что у проверяющих в «Журнале
   оценок» (call_qa/CallReviewCard.jsx): одно и то же слово по обе стороны. */
export const EVALUATION_VERDICTS = {
  Correct: { label: 'Верно', tone: 'green' },
  Deficiency: { label: 'Недочёт', tone: 'amber' },
  Incorrect: { label: 'Неверно', tone: 'red' },
  'N/A': { label: 'N/A', tone: 'slate' },
  Error: { label: 'Критич. ошибка', tone: 'red' },
  Pending: { label: 'Ожидает', tone: 'amber' },
};

export const evaluationVerdict = (status) => {
  const key = String(status ?? '').trim();
  if (EVALUATION_VERDICTS[key]) return EVALUATION_VERDICTS[key];
  return { label: key || '—', tone: 'slate' };
};

/* Тон балла. Пороги — из getScoreColor в App.jsx (90 и 60), включая его
   особенность: пустой балл и ноль остаются серыми, а не красными. */
export const evaluationScoreTone = (score) => {
  const value = Number(score);
  if (!Number.isFinite(value) || !value) return 'slate';
  if (value >= 90) return 'green';
  if (value >= 60) return 'amber';
  return 'red';
};

export const formatEvaluationScore = (score) => (
  score === null || score === undefined || score === '' || !Number.isFinite(Number(score))
    ? '—'
    : String(Math.round(Number(score)))
);

/* Балл с десятой — так подписан результат теста в настольной разметке. */
export const formatEvaluationPercent = (score) => (
  score === null || score === undefined || score === '' || !Number.isFinite(Number(score))
    ? '—'
    : `${Number(score).toFixed(1).replace(/\.0$/, '')}%`
);

const MONTHS_SHORT = ['янв.', 'февр.', 'мар.', 'апр.', 'мая', 'июн.', 'июл.', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'];

/* Срок повторной проверки — это ДЕНЬ, а не момент: общий formatDate раздела
   дописывает к нему «05:00», и на телефоне это читается как время проверки. */
export const formatEvaluationDay = (value) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
  if (!match) return String(value || '');
  const monthIndex = Number(match[2]) - 1;
  return `${match[3]} ${MONTHS_SHORT[monthIndex] || ''} ${match[1]}`.trim();
};

export const evaluationIsImported = (evaluation) => (
  evaluation?.call?.is_imported === true || evaluation?.is_imported === true
);

/* Что оценено: пройденный тест, чат Chat2Desk или звонок. */
export const describeEvaluationSubject = (evaluation) => {
  if (evaluation?.knowledge_test) {
    return { kind: 'test', title: evaluation.knowledge_test.title || 'Тест', typeLabel: 'Тестирование знаний' };
  }
  if (evaluation?.c2d_snapshot_id) {
    return { kind: 'chat', title: evaluation.phone_number || 'Чат', typeLabel: 'Чат' };
  }
  return { kind: 'call', title: evaluation?.phone_number || '—', typeLabel: 'Звонок' };
};

/* Условие «можно запросить переоценку» — дословно из настольной разметки:
   импортированный звонок не оценён вовсе, сотню оспаривать нечего, а пока
   запрос на рассмотрении или одобрен — второй не нужен. */
export const canRequestReevaluation = (evaluation, requestStatus) => (
  !evaluationIsImported(evaluation)
  && Number(evaluation?.score) < 100
  && !['pending', 'approved'].includes(String(requestStatus || 'none'))
);

/* Критерии оценки: сперва шкала направления, иначе — голые статусы (у старых
   оценок направления в ответе нет), иначе пусто. */
export const buildEvaluationCriteria = (evaluation) => {
  const statuses = Array.isArray(evaluation?.scores) ? evaluation.scores : [];
  const comments = Array.isArray(evaluation?.criterion_comments) ? evaluation.criterion_comments : [];
  const names = Array.isArray(evaluation?.criterion_names) ? evaluation.criterion_names : [];
  const criteria = Array.isArray(evaluation?.direction?.criteria) ? evaluation.direction.criteria : [];
  const comment = (index) => String(comments[index] || '').trim();

  if (criteria.length > 0) {
    return criteria.map((crit, index) => ({
      key: `${crit?.id ?? crit?.name ?? 'criterion'}-${index}`,
      number: index + 1,
      name: String(crit?.name || `Критерий ${index + 1}`),
      status: statuses[index] ?? null,
      weight: crit?.weight === null || crit?.weight === undefined || crit?.weight === '' ? null : Number(crit.weight),
      isCritical: !!crit?.isCritical,
      comment: comment(index),
    }));
  }
  return statuses.map((status, index) => ({
    key: `score-${index}`,
    number: index + 1,
    name: String(names[index] || `Критерий ${index + 1}`),
    status,
    weight: null,
    isCritical: false,
    comment: comment(index),
  }));
};

/* Свод по критериям одной строкой: «Верно 8 · Неверно 1». Порядок вердиктов
   постоянный — строка не должна переставляться от оценки к оценке. */
export const summarizeEvaluationCriteria = (criteria = []) => {
  const order = ['Correct', 'Deficiency', 'Incorrect', 'N/A', 'Error', 'Pending'];
  const counts = new Map();
  criteria.forEach((item) => {
    const key = String(item?.status ?? '').trim();
    if (!key) return;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  const known = order.filter((key) => counts.has(key));
  const rest = [...counts.keys()].filter((key) => !order.includes(key));
  return [...known, ...rest].map((key) => ({
    key,
    label: evaluationVerdict(key).label,
    tone: evaluationVerdict(key).tone,
    count: counts.get(key),
  }));
};

/* Карточка теста: баллы за вопросы, результат в процентах и отметка о том, что
   тест ушёл сам по истечении времени. */
export const buildEvaluationTest = (evaluation) => {
  const test = evaluation?.knowledge_test;
  if (!test) return null;
  const earned = test.earned_points === null || test.earned_points === undefined ? null : test.earned_points;
  const max = test.max_points === null || test.max_points === undefined ? null : test.max_points;
  return {
    title: String(test.title || 'Тест'),
    points: `${earned === null ? '—' : earned}${max === null ? '' : ` / ${max}`}`,
    result: formatEvaluationPercent(evaluation?.score),
    autoSubmitted: !!test.is_auto_submitted,
  };
};

export const evaluationChatQuotes = (evaluation) => (
  Array.isArray(evaluation?.chat_quotes) ? evaluation.chat_quotes.filter(Boolean) : []
);

/* Строки списка: то, что видно до открытия оценки.
 *
 * key, а не id: неоценённые звонки приходят из СВОЕЙ таблицы (imported_calls) и
 * нумеруются с единицы независимо от оценок — id «1» бывает и у оценки, и у
 * неоценённого звонка разом. По такому ключу список открывал не ту строку, по
 * которой нажали (поймано на стенде 16.09.2026). */
export const buildEvaluationRows = (evaluations = []) => (
  (Array.isArray(evaluations) ? evaluations : []).map((evaluation, index) => {
    const subject = describeEvaluationSubject(evaluation);
    const imported = evaluationIsImported(evaluation);
    return {
      key: `${imported ? 'imported' : 'call'}:${evaluation?.id ?? index}`,
      id: evaluation?.id ?? index,
      evaluation,
      number: index + 1,
      kind: subject.kind,
      title: subject.title,
      typeLabel: subject.typeLabel,
      score: evaluation?.score ?? null,
      tone: evaluationScoreTone(evaluation?.score),
      isImported: imported,
    };
  })
);

const KIND_LABELS = { call: 'Звонки', chat: 'Чаты', test: 'Тесты' };

/* Из чего сложился месяц: «Звонки 10 · Чаты 3 · Тесты 1». Виды, которых в
   месяце нет, не называются вовсе, а один-единственный вид не называется тоже —
   он уже сказан в каждой строке списка. */
export const summarizeEvaluationKinds = (evaluations = []) => {
  const counts = { call: 0, chat: 0, test: 0 };
  (Array.isArray(evaluations) ? evaluations : []).forEach((evaluation) => {
    counts[describeEvaluationSubject(evaluation).kind] += 1;
  });
  const parts = ['call', 'chat', 'test']
    .filter((kind) => counts[kind] > 0)
    .map((kind) => ({ key: kind, label: KIND_LABELS[kind], count: counts[kind] }));
  return parts.length > 1 ? parts : [];
};

/* «14 оценок» — счётный падеж. */
export const formatEvaluationsCount = (count) => {
  const value = Math.max(0, Math.round(Number(count) || 0));
  const tail = value % 100;
  const last = value % 10;
  if (tail >= 11 && tail <= 14) return `${value} оценок`;
  if (last === 1) return `${value} оценка`;
  if (last >= 2 && last <= 4) return `${value} оценки`;
  return `${value} оценок`;
};

/* Мониторинговая шкала: сводка по выбранному направлению. */
export const summarizeMonitoringScale = (criteria = []) => {
  const list = Array.isArray(criteria) ? criteria : [];
  const critical = list.filter((crit) => !!crit?.isCritical).length;
  const weight = list
    .filter((crit) => !crit?.isCritical)
    .reduce((sum, crit) => sum + (Number(crit?.weight) || 0), 0);
  return { total: list.length, critical, weight };
};
