import test from 'node:test';
import assert from 'node:assert/strict';

import {
  activeFilterCount, analyticsQuery, bucketLabel, driverAnswers, employeeDepartmentId,
  formPayload, formProblems, isRepeated, officeOptions, openQuestion, percent, rowBadges,
  rowSubtitle, statusView, submitLabel, willProcess, workPayload, workProblems, workSteps,
} from '../src/components/complaints/complaintRules.js';

/* Правила раздела «Жалобы» (ТЗ задачи #297). Обязательность формы и правило
 * «уходит ли жалоба в группу» продублированы на сервере — здесь сверяется, что
 * клиент говорит то же самое, иначе кнопка активна, а сервер отказывает. */

const META = {
  targets: [
    { code: 'call_center', unit: 'department', unit_required: true, employee: true,
      processing: 'always', reasons: [{ code: 'rude' }, { code: 'other' }] },
    { code: 'car_rental', unit: null, unit_required: false, employee: false,
      processing: 'always', reasons: [{ code: 'terms' }] },
    { code: 'front_office', unit: 'office', unit_required: false, employee: true,
      processing: 'always', reasons: [{ code: 'rude' }] },
    { code: 'taxi_park', unit: 'park', unit_required: false, employee: false,
      processing: 'optional', reasons: [{ code: 'commission' }] },
    { code: 'yandex', unit: null, unit_required: false, employee: false,
      processing: 'never', reasons: [{ code: 'tariffs' }] },
  ],
  departments: [
    { id: 1, code: 'szov', call_center: true }, { id: 367, code: 'op', call_center: true },
    { id: 909, code: 'front_office', call_center: false },
  ],
  offices: [
    { id: 47, name: 'Офис Алматы №1', city: 'Алматы' },
    { id: 50, name: 'Офис Актау', city: 'Актау' },
  ],
};
const target = (code) => META.targets.find((item) => item.code === code);

const FILLED = {
  reason: 'rude', unit_id: '367', employee_id: '40', driver_name: 'Сериков Ерлан',
  driver_phone: '+7 701', city: 'Алматы', description: 'Нагрубил',
};

test('Яндекс только фиксируется — и кнопка говорит об этом до нажатия', () => {
  assert.equal(willProcess(target('yandex'), true), false);
  assert.equal(submitLabel(target('yandex')), 'Зафиксировать');
  assert.equal(submitLabel(target('call_center')), 'Отправить в группу');
  // У парка решает оператор.
  assert.equal(submitLabel(target('taxi_park'), false), 'Зафиксировать');
  assert.equal(submitLabel(target('taxi_park'), true), 'Отправить в группу');
  // «Всегда» выключателем не отменяется.
  assert.equal(willProcess(target('car_rental'), false), true);
});

test('обязательные поля — как у сервера', () => {
  assert.deepEqual(Object.keys(formProblems(target('call_center'), {})).sort(),
    ['city', 'description', 'driver_name', 'driver_phone', 'reason', 'unit_id']);
  assert.deepEqual(formProblems(target('call_center'), FILLED), {});
  // Сотрудник не обязателен: «если оператор не смог определить сотрудника,
  // обращение всё равно должно быть создано».
  assert.deepEqual(formProblems(target('call_center'), { ...FILLED, employee_id: '' }), {});
  // Причина чужой цели — не причина.
  assert.ok(formProblems(target('yandex'), FILLED).reason);
});

test('в запрос не уезжает то, чего у цели нет', () => {
  const payload = formPayload(target('car_rental'), { ...FILLED, reason: 'terms' });
  assert.equal(payload.unit_id, undefined);
  assert.equal(payload.employee_id, undefined);
  assert.equal(payload.requires_processing, true);
  const call = formPayload(target('call_center'), FILLED);
  assert.equal(call.unit_id, 367);
  assert.equal(call.employee_id, 40);
  assert.equal(formPayload(target('yandex'), { ...FILLED, reason: 'tariffs' }).requires_processing, false);
  assert.equal(formPayload(target('taxi_park'), { ...FILLED, reason: 'commission',
    requires_processing: false }).requires_processing, false);
});

test('сотрудников предлагаем из отдела цели', () => {
  assert.equal(employeeDepartmentId(target('call_center'), { unit_id: '367' }, META), 367);
  assert.equal(employeeDepartmentId(target('call_center'), {}, META), null);
  assert.equal(employeeDepartmentId(target('front_office'), {}, META), 909);
  assert.equal(employeeDepartmentId(target('taxi_park'), {}, META), null);
});

test('офисы выбранного города — первыми, чужие не пропадают', () => {
  const options = officeOptions(META, 'Актау');
  assert.deepEqual(options.map((item) => item.value), ['50', '47']);
  assert.equal(options[0].groupLabel, 'Актау');
});

test('статус: «зафиксирована» — не «отработана»', () => {
  assert.equal(statusView({ status: 'closed', requires_processing: false }).label, 'Зафиксирована');
  assert.equal(statusView({ status: 'closed', requires_processing: true, result_code: 'confirmed' }).label,
    'Отработана');
  assert.equal(statusView({ status: 'open' }).label, 'В работе');
});

test('бейджи ленты — только то, что ждёт зрителя', () => {
  const base = { created_by: 10, responsible_id: 50, work_state: 'pending' };
  assert.deepEqual(rowBadges({ ...base, question_open: true }, 10).map((b) => b.key), ['question']);
  assert.deepEqual(rowBadges({ ...base, unread: true, unread_kind: 'answer' }, 10).map((b) => b.key),
    ['answer']);
  assert.deepEqual(rowBadges(base, 50).map((b) => b.label), ['Нужна ОС']);
  assert.deepEqual(rowBadges({ ...base, training_required: true }, 50).map((b) => b.label),
    ['Нужен тренинг']);
  // Штатное «в работе» не рисуется никак.
  assert.deepEqual(rowBadges(base, 99), []);
  assert.deepEqual(rowBadges({ ...base, delivery_status: 'failed' }, 99).map((b) => b.key), ['failed']);
});

test('вторая строка ленты: разбирающему — сотрудник', () => {
  const item = { employee_name: 'Иванова', driver_name: 'Сериков', city: 'Алматы', unit_name: 'ОП' };
  assert.equal(rowSubtitle(item), 'Сериков · Алматы');
  assert.equal(rowSubtitle(item, { handler: true }), 'Иванова · Сериков · Алматы');
});

test('лесенка работы с сотрудником', () => {
  assert.deepEqual(workSteps({}).map((s) => s.label), ['Сотрудник не определён']);
  const pending = { employee_id: 40, employee_name: 'Иванова', work_state: 'pending' };
  assert.deepEqual(workSteps(pending).map((s) => s.key), ['employee', 'feedback', 'done']);
  const training = { ...pending, feedback_done: true, training_required: true };
  assert.deepEqual(workSteps(training).map((s) => s.label),
    ['Сотрудник определён', 'Обратная связь проведена', 'Требуется тренинг',
      'Работа с сотрудником завершена']);
  const done = { ...pending, feedback_done: true, training_done: true, work_state: 'done' };
  assert.ok(workSteps(done).every((s) => s.done));
});

test('запись о работе: время занятия и комментарий', () => {
  const feedback = { code: 'feedback', training: true, ask_training: true, default_reason: 'Обратная связь' };
  const other = { code: 'other', training: false, ask_training: false };
  assert.deepEqual(Object.keys(workProblems(feedback, {})).sort(), ['comment', 'date', 'time']);
  assert.equal(workProblems(feedback, { comment: 'x', date: '2026-09-28', start: '10:00', end: '09:00' }).time,
    'Окончание должно быть позже начала');
  assert.equal(workProblems(feedback, { comment: 'x', date: '2026-10-05', start: '10:00', end: '11:00' },
    { today: '2026-09-29' }).date, 'Занятие ещё не прошло');
  assert.deepEqual(workProblems(other, { comment: 'x' }), {});
  const payload = workPayload(feedback, { comment: ' ОС ', date: '2026-09-28', start: '10:00',
    end: '10:30', need_training: true });
  assert.deepEqual(payload.training, { date: '2026-09-28', start: '10:00', end: '10:30',
    reason: 'Обратная связь' });
  assert.equal(payload.need_training, true);
  assert.equal(workPayload(other, { comment: 'x', need_training: true }).need_training, false);
});

test('ответы для водителя — свежий первым; вопрос — последний', () => {
  const messages = [
    { id: 1, kind: 'answer' }, { id: 2, kind: 'internal' }, { id: 3, kind: 'question' },
    { id: 4, kind: 'answer' }, { id: 5, kind: 'question' },
  ];
  assert.deepEqual(driverAnswers(messages).map((m) => m.id), [4, 1]);
  assert.equal(openQuestion({ question_open: true }, messages).id, 5);
  assert.equal(openQuestion({ question_open: false }, messages), null);
});

test('аналитика: подписи, проценты, фильтры', () => {
  assert.equal(percent(1, 3), 33);
  assert.equal(percent(1, 0), 0);
  assert.equal(bucketLabel('2026-09-07', 'day'), '7 сен');
  assert.equal(bucketLabel('2026-09-07', 'week'), 'с 7 сен');
  assert.equal(bucketLabel('2026-09-01', 'month'), 'Сентябрь 2026');
  assert.equal(activeFilterCount({ date_from: 'x', target: 'yandex', city: 'Алматы', feedback: 'yes' }), 2);
  assert.equal(analyticsQuery({ date_from: '2026-09-01', city: '', target: 'yandex' }),
    'date_from=2026-09-01&target=yandex');
  assert.equal(isRepeated({ total: 2 }), true);
  assert.equal(isRepeated({ total: 1 }), false);
});
