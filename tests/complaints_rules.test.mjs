import test from 'node:test';
import assert from 'node:assert/strict';

import {
  activeFilterCount, analyticsQuery, bucketLabel, driverAnswers,
  employeeDepartmentId, eventText, heldDraft, isPickedShift, isRecorded, needsReview, pickShift,
  planText, shiftLabel,
  formPayload, formProblems, isRepeated, officeOptions, openQuestion, percent, rowBadges,
  rowSubtitle, statusView, submitLabel, whereabouts, willProcess, wizardTargets, workButtons,
  workPayload, workProblems, workSteps,
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
      processing: 'always', reasons: [{ code: 'commission' }] },
    { code: 'yandex', unit: null, unit_required: false, employee: false,
      processing: 'review', reasons: [{ code: 'tariffs' }] },
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

test('Яндекс идёт на проверку супервайзеру — и кнопка говорит об этом до нажатия', () => {
  assert.equal(willProcess(target('yandex')), false);
  assert.equal(needsReview(target('yandex')), true);
  assert.equal(submitLabel(target('yandex')), 'Отправить на проверку');
  assert.equal(submitLabel(target('call_center')), 'Отправить в группу');
  // Выбора у оператора нет ни у одной цели (владелец, 29.09.2026): парк —
  // тоже в группу.
  assert.equal(submitLabel(target('taxi_park')), 'Отправить в группу');
  assert.equal(willProcess(target('car_rental')), true);
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
  // «Отправлять ли в группу» решает сервер по цели — клиент его не шлёт.
  assert.equal('requires_processing' in payload, false);
  const call = formPayload(target('call_center'), FILLED);
  assert.equal(call.unit_id, 367);
  assert.equal(call.employee_id, 40);
  // Ссылка на аккаунт — только помощник заполнения: в жалобу она не уходит.
  const withLink = formPayload(target('call_center'), { ...FILLED, driver_link: 'https://fleet.yandex.kz/contractors/x' });
  assert.equal(JSON.stringify(withLink).includes('fleet'), false);
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
  // «В работе» — штатное состояние: цвета у него нет.
  assert.equal(statusView({ status: 'open' }).tone, 'slate');
});

test('жалоба на проверке: «На проверке», решённая — «Отработана», не «Зафиксирована»', () => {
  const pending = { status: 'open', requires_processing: false, review_state: 'pending' };
  assert.equal(statusView(pending).label, 'На проверке');
  assert.equal(isRecorded(pending), false);
  const resolved = { status: 'closed', requires_processing: false, review_state: 'resolved',
    result_code: 'solved' };
  assert.equal(statusView(resolved).label, 'Отработана');
  assert.equal(isRecorded({ requires_processing: false }), true, 'старые жалобы на Яндекс');
});

test('где жалоба — одной фразой для автора', () => {
  assert.equal(whereabouts({ review_state: 'pending', requires_processing: false }),
    'на проверке у супервайзера');
  assert.equal(whereabouts({ review_state: 'resolved', requires_processing: false, result_code: 'solved' }),
    'решена супервайзером, в группу не отправлялась');
  assert.equal(whereabouts({ requires_processing: false }),
    'зафиксирована для аналитики, в группу не отправлялась');
  assert.equal(whereabouts({ requires_processing: true, delivery_status: 'sent', tg_chat_title: 'Жалобы' }),
    'в группе «Жалобы»');
  // Не ушла — об этом говорит красная плашка с повтором, а не эта строка.
  assert.equal(whereabouts({ requires_processing: true, delivery_status: 'failed', tg_chat_title: 'Жалобы' }), '');
});

test('бейджи ленты — только то, что ждёт зрителя', () => {
  const base = { created_by: 10, responsible_id: 50, work_state: 'pending' };
  assert.deepEqual(rowBadges({ ...base, question_open: true }, 10).map((b) => b.key), ['question']);
  assert.deepEqual(rowBadges({ ...base, unread: true, unread_kind: 'answer' }, 10).map((b) => b.key),
    ['answer']);
  assert.deepEqual(rowBadges(base, 50).map((b) => b.label), ['Ждёт работы']);
  assert.deepEqual(rowBadges({ ...base, training_required: true }, 50).map((b) => b.label),
    ['Нужен тренинг']);
  assert.deepEqual(rowBadges({ ...base, training_required: true,
    training_planned_at: '2026-10-02T14:00:00' }, 50).map((b) => b.label), ['Тренинг 02.10']);
  // Жалоба на проверке горит тому, чья это проверка (флаг сервера — то же
  // правило, что у счётчика и колокола), а не каждому, кому она видна.
  const review = { created_by: 10, review_state: 'pending' };
  assert.deepEqual(rowBadges({ ...review, review_mine: true }, 50).map((b) => b.key), ['review']);
  assert.deepEqual(rowBadges({ ...review, review_mine: false }, 50), []);
  assert.deepEqual(rowBadges({ ...review, review_state: 'resolved', review_mine: true }, 50), []);
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
  // Кнопки «ОС» больше нет — и серого шага «ОС не проведена» тоже.
  assert.deepEqual(workSteps(pending).map((s) => s.key), ['employee', 'done']);
  const planned = { ...pending, training_required: true, training_planned_at: '2026-10-02T14:00:00' };
  assert.deepEqual(workSteps(planned).map((s) => s.label),
    ['Сотрудник определён', 'Тренинг назначен на 02.10 в 14:00', 'Работа с сотрудником завершена']);
  assert.deepEqual(workSteps({ ...pending, training_required: true }).map((s) => s.label)[1],
    'Требуется тренинг');
  // Старая запись ОС остаётся видна фактом.
  const old = { ...pending, feedback_done: true, training_done: true, work_state: 'done' };
  assert.deepEqual(workSteps(old).map((s) => s.key), ['employee', 'feedback', 'training', 'done']);
  assert.ok(workSteps(old).every((s) => s.done));
});

const CLOCK = { today: '2026-09-30', minute: 12 * 60 };

test('«Назначить тренинг»: день и время — только вперёд', () => {
  assert.deepEqual(Object.keys(workProblems('training_assigned', {}, CLOCK)).sort(), ['date', 'time']);
  assert.equal(workProblems('training_assigned', { date: '2026-09-29', time: '10:00' }, CLOCK).date,
    'Этот день уже прошёл');
  assert.equal(workProblems('training_assigned', { date: '2026-09-30', time: '11:55' }, CLOCK).time,
    'Это время уже прошло');
  assert.deepEqual(workProblems('training_assigned', { date: '2026-09-30', time: '12:05' }, CLOCK), {});
  assert.deepEqual(workPayload('training_assigned', { date: '2026-10-02', time: '14:00' }),
    { action: 'training_assigned', plan: { date: '2026-10-02', time: '14:00' } });
});

test('«Проведён тренинг»: дата, начало и конец — в «Тренинги»', () => {
  assert.equal(workProblems('training', { date: '2026-09-28', start: '10:00', end: '09:00' }, CLOCK).time,
    'Окончание должно быть позже начала');
  assert.equal(workProblems('training', { date: '2026-10-05', start: '10:00', end: '11:00' }, CLOCK).date,
    'Занятие ещё не прошло');
  assert.deepEqual(workProblems('training', { date: '2026-09-28', start: '10:00', end: '10:30' }, CLOCK), {});
  assert.deepEqual(workPayload('training', { date: '2026-09-28', start: '10:00', end: '10:30' }),
    { action: 'training', training: { date: '2026-09-28', start: '10:00', end: '10:30' } });
  // Назначенный и наступивший тренинг подставляет свой день и начало.
  const planned = { training_required: true, training_planned_at: '2026-09-30T09:00:00' };
  assert.deepEqual(heldDraft(planned, CLOCK), { date: '2026-09-30', start: '09:00' });
  const future = { training_required: true, training_planned_at: '2026-10-03T09:00:00' };
  assert.deepEqual(heldDraft(future, CLOCK), { date: '2026-09-30' });
});

test('«Приняты другие меры»: нужен комментарий', () => {
  assert.equal(workProblems('other', { comment: '  ' }, CLOCK).comment, 'Опишите, что сделано');
  assert.deepEqual(workProblems('other', { comment: 'Беседа' }, CLOCK), {});
  assert.deepEqual(workPayload('other', { comment: ' Беседа ' }), { action: 'other', comment: 'Беседа' });
  assert.equal(workProblems('feedback', {}, CLOCK).action, 'Выберите, что сделано');
});

test('смена в окне назначения: идущая ночная — это «сегодня»', () => {
  const night = { date: '2026-09-29', start: '20:00', end: '08:00', ongoing: true };
  const clock = { today: '2026-09-30', minute: 2 * 60 };
  const picked = pickShift({}, night, clock);
  // День начала смены уже прошёл — подставляем сегодня, время не трогаем.
  assert.equal(picked.date, '2026-09-30');
  assert.equal(picked.time, undefined);
  assert.equal(workProblems('training_assigned', { ...picked, time: '04:00' }, clock).date, undefined);
  assert.ok(isPickedShift(picked, night, clock));
  const next = { date: '2026-10-01', start: '09:00', end: '18:00', ongoing: false };
  const second = pickShift(picked, next, clock);
  assert.deepEqual([second.date, second.time], ['2026-10-01', '09:00']);
  assert.ok(isPickedShift(second, next, clock));
  assert.ok(!isPickedShift(second, night, clock), 'галочка — у одной смены');
  assert.ok(!isPickedShift({ ...second, date: '2026-10-02' }, next, clock), 'день поменяли руками');
});

test('смена в окне назначения и время плана', () => {
  assert.equal(shiftLabel({ date: '2026-10-01', start: '09:00', end: '18:00', type: 'regular' }),
    'Чт, 01.10 · 09:00–18:00');
  assert.equal(shiftLabel({ date: '2026-10-03', start: '10:00', end: '14:00', type: 'office_practice' }),
    'Сб, 03.10 · 10:00–14:00 · практика в офисе');
  assert.equal(planText('2026-10-02T14:05:00'), '02.10 в 14:05');
  assert.equal(planText(null), '');
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


test('без сотрудника — тренинг недоступен, «другие меры» объясняют почему', () => {
  const meta = { work_buttons: ['training_assigned', 'training', 'other']
    .map((code) => ({ code, button: code })) };
  assert.deepEqual(workButtons(meta, {}).map((a) => [a.code, a.disabled]),
    [['training_assigned', true], ['training', true], ['other', false]]);
  assert.equal(workButtons(meta, {})[0].hint, 'Сначала определите сотрудника');
  assert.ok(workButtons(meta, { employee_id: 4 }).every((a) => !a.disabled));
  assert.deepEqual(workButtons(null, {}), []);
  // Лесенка у такой жалобы: сотрудник не определён, но работа закрыта объяснением.
  assert.deepEqual(workSteps({ work_state: 'done' }).map((s) => [s.key, s.done]),
    [['employee', false], ['done', true]]);
});

test('длинное описание — ошибка, а не молчаливая обрезка', () => {
  const problems = formProblems(target('car_rental'), { ...FILLED, reason: 'terms', description: 'я'.repeat(4001) });
  assert.ok(problems.description);
});

test('история говорит, ЧТО произошло', () => {
  const meta = { results: [{ code: 'confirmed', title: 'Жалоба подтверждена' }],
    work_actions: [{ code: 'feedback', title: 'Обратная связь проведена' }] };
  assert.equal(eventText({ kind: 'employee', payload: { from: 'Иванова', to: 'Петров' } }),
    'Сотрудник изменён: Иванова → Петров');
  assert.equal(eventText({ kind: 'employee', payload: { from: 'Иванова', to: null } }),
    'Сотрудник снят: Иванова');
  assert.equal(eventText({ kind: 'employee', payload: { to: 'Петров' } }), 'Сотрудник определён: Петров');
  assert.equal(eventText({ kind: 'result', payload: { result: 'confirmed', via: 'telegram' } }, meta),
    'Итог проверки: Жалоба подтверждена · в группе');
  assert.equal(eventText({ kind: 'work', payload: { action: 'feedback', closed: true } }, meta),
    'Обратная связь проведена · работа завершена');
  assert.equal(eventText({ kind: 'sent', payload: {} }), 'Отправлена в группу');
  assert.equal(eventText({ kind: 'work', payload: { action: 'feedback', planned_at: '2026-10-02T14:00:00' } },
    meta), 'Обратная связь проведена на 02.10 в 14:00');
  assert.equal(eventText({ kind: 'review_resolved', payload: {} }), 'Проверена супервайзером — решено');
  assert.equal(eventText({ kind: 'review_sent', payload: {} }),
    'Проверена супервайзером и отправлена в группу');
});

test('направления жалобы в мастере «Обращений»: без группы — как тематика без группы', () => {
  const ready = wizardTargets({ ...META, group: { ready: true } });
  assert.deepEqual(ready.map((t) => t.code),
    ['call_center', 'car_rental', 'front_office', 'taxi_park', 'yandex']);
  assert.ok(ready.every((t) => t.is_ready));
  // Группа не выбрана: отправить некуда никого, кроме Яндекса — он сначала
  // идёт на проверку супервайзеру.
  const noGroup = Object.fromEntries(wizardTargets({ ...META, group: { ready: false } })
    .map((t) => [t.code, t.is_ready]));
  assert.deepEqual(noGroup, { call_center: false, car_rental: false, front_office: false,
    taxi_park: false, yandex: true });
  assert.deepEqual(wizardTargets(null), []);
});
