import test from 'node:test';
import assert from 'node:assert/strict';

import {
  EXPORT_MAX_DAYS,
  KIND_LABELS,
  MAX_BLOCKS,
  SETTINGS_LIMITS,
  TARIFF_LABELS,
  THRESHOLD_ORDER_ISSUE,
  accessChanges,
  accessCloseStep,
  accessDraftOf,
  accessPeopleGroups,
  accessSegments,
  accessSession,
  accessSummary,
  blocksWord,
  conditionGroups,
  daysWord,
  defaultOfficeId,
  exportFileName,
  fmtAvg,
  forecastLabel,
  isCountDraft,
  issueOffices,
  nextIssueLine,
  nextOfficeId,
  officePlace,
  parseCount,
  plural,
  settingsDirty,
  settingsIssues,
  stepCount,
  stockStatus,
  rebaseAccessDraft,
  tariffLabel,
  toggleAccess,
  verdictBasis,
  verdictHeadline,
  visibleIssues,
} from '../src/components/water/waterMeta.js';

test('plural follows Russian rules', () => {
  assert.equal(blocksWord(1), '1 блок');
  assert.equal(blocksWord(3), '3 блока');
  assert.equal(blocksWord(11), '11 блоков');
  assert.equal(blocksWord(21), '21 блок');
  assert.equal(daysWord(14), '14 дней');
  assert.equal(plural(102, 'a', 'b', 'c'), 'b');
});

test('tariff codes are Fleet codes, Business and Ultima are labelled', () => {
  assert.equal(tariffLabel('business'), 'Business');
  assert.equal(tariffLabel('ultimate'), 'Premier (Ultima)');
  assert.equal(tariffLabel('comfort'), 'Комфорт');
  assert.equal(tariffLabel('sdd_int'), 'sdd_int');
  assert.ok(Object.keys(TARIFF_LABELS).includes('comfort_plus'));
});

test('kind labels are the TZ words', () => {
  assert.deepEqual(KIND_LABELS, { welcome: 'Приветственная', activity: 'За активность' });
});

test('export file name mirrors report_filename', () => {
  assert.equal(exportFileName('2026-09-01', '2026-09-30'), 'Выдачи воды 01.09.2026 — 30.09.2026.xlsx');
  assert.equal(exportFileName('2026-09-30', '2026-09-30'), 'Выдачи воды 30.09.2026.xlsx');
  assert.equal(exportFileName('', ''), 'Выдачи воды.xlsx');
  assert.equal(EXPORT_MAX_DAYS, 366);
});

test('enough stock is neutral, low and buy are coloured', () => {
  assert.match(stockStatus('enough').pill, /slate/);
  assert.match(stockStatus('low').pill, /amber/);
  assert.match(stockStatus('buy').pill, /rose/);
  assert.equal(stockStatus('unknown').label, 'Достаточно');
});

test('verdict wording', () => {
  assert.equal(verdictHeadline({ allowed: true, kind: 'welcome' }), 'Можно выдать приветственный блок');
  assert.equal(verdictHeadline({ allowed: true, kind: 'activity' }), 'Можно выдать воду');
  assert.equal(verdictHeadline({ allowed: false }), 'Воду выдать нельзя');
  assert.equal(verdictBasis({ allowed: true, kind: 'activity', trips: 34, trips_basis: 'since_last' }),
    '34 поездки с прошлой выдачи');
  assert.equal(verdictBasis({ allowed: true, kind: 'activity', trips: 25, trips_basis: 'week' }),
    '25 поездок за последние 7 дней');
  assert.equal(verdictBasis({ allowed: false }), '');
  assert.equal(verdictBasis({ allowed: true, kind: 'welcome', fk: 'passed' }),
    'Новый водитель — ещё ни одного выполненного заказа · ФК пройден');
  assert.equal(verdictBasis({ allowed: true, kind: 'welcome', fk: 'unknown' }),
    'Новый водитель — ещё ни одного выполненного заказа');
  assert.equal(nextIssueLine({ next_date: '2026-10-02', last_issue: { id: 1 } }),
    'Следующая выдача за активность — с 02.10.2026');
  assert.equal(nextIssueLine({ next_date: null }), '');
});

test('forecast and averages', () => {
  // «Требуется закупка» уже сказано плашкой в той же строке — прогноз молчит.
  assert.equal(forecastLabel({ status: 'buy', days_to_buy: 0 }), '—');
  assert.equal(forecastLabel({ status: 'low', days_to_buy: 3 }), 'через ~3 дня');
  assert.equal(forecastLabel({ status: 'enough', days_to_buy: null }), '—');
  assert.equal(fmtAvg(0), '0');
  assert.equal(fmtAvg(1.25), '1,3');
  assert.equal(fmtAvg(12.4), '12');
});

test('parseCount accepts only whole non-negative numbers', () => {
  assert.equal(parseCount(''), null);
  assert.equal(parseCount(' 12 '), 12);
  assert.equal(parseCount(0), 0);
  assert.ok(Number.isNaN(parseCount('-1')));
  assert.ok(Number.isNaN(parseCount('1.5')));
  assert.ok(Number.isNaN(parseCount('два')));
});

test('default office: remembered, then the only one in own city, then the only one', () => {
  const offices = [
    { id: 1, city: 'Алматы', is_active: true },
    { id: 2, city: 'Алматы', is_active: true },
    { id: 3, city: 'Шымкент', is_active: true },
    { id: 4, city: 'Шымкент', is_active: false },
  ];
  assert.equal(defaultOfficeId(offices, 'Алматы', 2), 2);
  assert.equal(defaultOfficeId(offices, 'Шымкент', 4), 3);
  assert.equal(defaultOfficeId(offices, 'Алматы', null), null);
  assert.equal(defaultOfficeId([offices[2]], null, null), 3);
  // Своего города в учёте нет — чужой офис сам не подставляется.
  assert.equal(defaultOfficeId([offices[0]], 'Астана', null), null);
});

test('default office: a remembered office of another city is not picked by itself', () => {
  const offices = [
    { id: 1, city: 'Алматы', is_active: true },
    { id: 2, city: 'Астана', is_active: true },
  ];
  // 23.10.2026: астанинскому сотруднику неделю подставлялся раз выбранный алматинский офис.
  assert.equal(defaultOfficeId(offices, 'Астана', 1), null);
  assert.equal(defaultOfficeId(offices, 'Астана', 2), 2);
  assert.equal(defaultOfficeId(offices, 'Астана', null), 2);
  // Города нет или своего офиса в учёте нет — сверять не с чем, запомненный остаётся.
  assert.equal(defaultOfficeId(offices, null, 1), 1);
  assert.equal(defaultOfficeId(offices, 'Шымкент', 1), 1);
});

test('conditions spell out the program from the settings', () => {
  const groups = conditionGroups({
    tariffs: ['business', 'ultimate'], min_trips: 20, cooldown_days: 7,
    welcome_blocks: 1, activity_blocks: 2,
  });
  assert.deepEqual(groups.map((group) => group.title), ['Приветственный блок', 'За активность']);
  const welcome = Object.fromEntries(groups[0].rows);
  assert.equal(welcome['Тариф машины'], 'Business, Premier (Ultima)');
  assert.equal(welcome['ФК'], 'Должен быть пройден');
  assert.equal(welcome['Сколько'], '1 блок, один раз');
  const activity = Object.fromEntries(groups[1].rows);
  assert.equal(activity['Поездки'], '20 поездок с прошлой выдачи, первый раз — за последние 7 дней');
  assert.equal(activity['Как часто'], 'Не чаще раза в 7 дней');
  assert.equal(activity['Сколько'], '2 блока за выдачу');
});

test('conditions without limits say so instead of showing zeros', () => {
  const activity = Object.fromEntries(conditionGroups({ tariffs: [], min_trips: 0, cooldown_days: 0 })[1].rows);
  assert.equal(activity['Поездки'], 'Не требуются');
  assert.equal(activity['Как часто'], 'Без ограничения по дням');
  assert.equal(activity['Тариф машины'], 'не заданы');
  assert.equal(activity['Сколько'], '1 блок за выдачу');
});

test('count fields take digits only, up to six', () => {
  for (const good of ['', '0', '120', '000123', '999999']) {
    assert.equal(isCountDraft(good), true, good);
  }
  // «Только числа, а не буквы» (01.10.2026): буквы, пробелы, минус, дробь и
  // нелатинские цифры поле отбрасывает целиком, а не вычищает.
  for (const bad of ['12a', 'а', '-3', '1,5', '1.5', ' 12', '12 ', '1 000', '1234567', '١٢', '１２']) {
    assert.equal(isCountDraft(bad), false, bad);
  }
  assert.equal(parseCount('120'), 120);
  assert.ok(Number.isNaN(parseCount('12a')));
});

test('office place reads city and address', () => {
  assert.equal(officePlace({ city: 'Алматы', address: 'ул. Байзакова 78А' }), 'Алматы · ул. Байзакова 78А');
  assert.equal(officePlace({ city: 'Астана', address: '  ' }), 'Астана');
  assert.equal(officePlace({ city: '', address: 'Сарыарка 31' }), 'Сарыарка 31');
  assert.equal(officePlace(null), '');
});

test('settings issues name what to fix at the field', () => {
  const ok = { min_trips: '10', cooldown_days: 7, welcome_blocks: 1, activity_blocks: '1',
    low_threshold: 20, buy_threshold: 10, tariffs: ['business'] };
  assert.deepEqual(settingsIssues(ok), {});
  assert.deepEqual(settingsIssues({ ...ok, min_trips: '' }), { min_trips: 'Укажите число' });
  assert.deepEqual(settingsIssues({ ...ok, activity_blocks: '0' }), { activity_blocks: 'От 1 до 50' });
  assert.deepEqual(settingsIssues({ ...ok, cooldown_days: '366' }), { cooldown_days: 'От 0 до 365' });
  assert.deepEqual(settingsIssues({ ...ok, buy_threshold: '25' }),
    { buy_threshold: 'Не больше порога «Низкий остаток»' });
  // Равные пороги — можно: у обоих «столько или меньше», как у сервера.
  assert.deepEqual(settingsIssues({ ...ok, buy_threshold: 20 }), {});
  assert.deepEqual(settingsIssues({ ...ok, tariffs: [] }), { tariffs: 'Отметьте хотя бы один тариф' });
  // Порог вне пределов — своя строка, а не «порядок порогов» в соседнем поле.
  const huge = settingsIssues({ ...ok, low_threshold: '200000' });
  assert.equal(huge.low_threshold, `От 0 до ${MAX_BLOCKS.toLocaleString('ru-RU')}`);
  assert.equal(huge.buy_threshold, undefined);
});

test('settings limits cover every number of the program', () => {
  assert.deepEqual(Object.keys(SETTINGS_LIMITS).sort(), [
    'activity_blocks', 'buy_threshold', 'cooldown_days', 'low_threshold', 'min_trips', 'welcome_blocks']);
  assert.deepEqual(SETTINGS_LIMITS.welcome_blocks, [1, 50]);
  assert.equal(SETTINGS_LIMITS.low_threshold[1], MAX_BLOCKS);
});

test('stepper steps within the server limits', () => {
  assert.equal(stepCount('10', 1, [0, 1000]), 11);
  assert.equal(stepCount(10, -1, [0, 1000]), 9);
  assert.equal(stepCount('50', 1, [1, 50]), 50);   // на пределе — стоит
  assert.equal(stepCount('1', -1, [1, 50]), 1);
  assert.equal(stepCount('', 1, [1, 50]), 1);      // от пустого — нижний предел
  assert.equal(stepCount('', -1, [0, 365]), 0);
  assert.equal(stepCount('60', -1, [1, 50]), 50);  // набранное вне пределов прижимается
});

test('an edit is a different value, not a different string', () => {
  const loaded = { min_trips: 10, cooldown_days: 7, welcome_blocks: 1, activity_blocks: 1,
    low_threshold: 20, buy_threshold: 10, tariffs: ['business', 'ultimate'], notify_user_ids: [11, 12] };
  assert.equal(settingsDirty({ ...loaded }, loaded), false);
  assert.equal(settingsDirty({ ...loaded, min_trips: '10' }, loaded), false);
  assert.equal(settingsDirty({ ...loaded, min_trips: '010' }, loaded), false);
  assert.equal(settingsDirty({ ...loaded, tariffs: ['ultimate', 'business'] }, loaded), false);
  assert.equal(settingsDirty({ ...loaded, notify_user_ids: ['12', '11'] }, loaded), false);
  assert.equal(settingsDirty({ ...loaded, min_trips: '11' }, loaded), true);
  assert.equal(settingsDirty({ ...loaded, min_trips: '' }, loaded), true);
  assert.equal(settingsDirty({ ...loaded, tariffs: ['business'] }, loaded), true);
  assert.equal(settingsDirty({ ...loaded, notify_user_ids: [] }, loaded), true);
  assert.equal(settingsDirty(null, loaded), false);
});

test('while typing, only the cleared field and the threshold order keep quiet', () => {
  const draft = { min_trips: '', cooldown_days: 7, welcome_blocks: '60', activity_blocks: 1,
    low_threshold: '1', buy_threshold: 10, tariffs: ['business'] };
  const issues = settingsIssues(draft);
  assert.equal(issues.buy_threshold, THRESHOLD_ORDER_ISSUE);
  // Ничего не в работе — видно всё.
  assert.deepEqual(visibleIssues(issues, draft, null), issues);
  // Стёртое поле в работе молчит, чужие ошибки видны.
  const typingTrips = visibleIssues(issues, draft, 'min_trips');
  assert.equal(typingTrips.min_trips, undefined);
  assert.equal(typingTrips.welcome_blocks, 'От 1 до 50');
  assert.equal(typingTrips.buy_threshold, THRESHOLD_ORDER_ISSUE);
  // «60» в работе — ошибка видна сразу: поле не пустое.
  assert.equal(visibleIssues(issues, draft, 'welcome_blocks').welcome_blocks, 'От 1 до 50');
  // Набирают «низкий» — порядок порогов молчит, и так же у самой закупки.
  assert.equal(visibleIssues(issues, draft, 'low_threshold').buy_threshold, undefined);
  assert.equal(visibleIssues(issues, draft, 'buy_threshold').buy_threshold, undefined);
  // Предел у закупки — не порядок: виден и в работе.
  const big = { ...draft, buy_threshold: '200000' };
  assert.match(visibleIssues(settingsIssues(big), big, 'buy_threshold').buy_threshold, /^От 0 до /);
});

/* ── Доступ к выдаче по офисам (23.10.2026) ─────────────────────────────── */

const ALMATY = { id: 1, city: 'Алматы', name: 'Офис для подключения тарифа «Бизнес»', address: 'Байзакова 78А', is_active: true };
const ASTANA = { id: 2, city: 'Астана', name: 'Офис Астана', address: 'Сарыарка 31', is_active: true };
const ACCESS = {
  offices: [{ ...ALMATY, user_ids: [11, 12] }, { ...ASTANA, user_ids: [] }],
  people: [
    { id: 12, name: 'Яковлев', city: 'Алматы' },
    { id: 21, name: 'Борисов', city: 'Астана' },
    { id: 11, name: 'Алиев', city: 'Алматы' },
    { id: 31, name: 'Ветров', city: null },
    { id: 41, name: 'Громов', city: 'Шымкент' },
  ],
};

test('issue screen offers the issuer only the offices granted to them', () => {
  const inactive = { id: 3, city: 'Алматы', name: 'Старый', is_active: false };
  assert.deepEqual(issueOffices([ALMATY, ASTANA, inactive], [2]).map((o) => o.id), [2]);
  assert.deepEqual(issueOffices([ALMATY, ASTANA], []), []);
  // Строки «2» с сервера не бывает, но сравнение — по числу.
  assert.deepEqual(issueOffices([ALMATY, ASTANA], ['1']).map((o) => o.id), [1]);
  // null — руководитель: все офисы учёта; поля нет (сервер старее фронта) — как было.
  assert.deepEqual(issueOffices([ALMATY, ASTANA, inactive], null).map((o) => o.id), [1, 2]);
  assert.deepEqual(issueOffices([ALMATY, ASTANA], undefined).map((o) => o.id), [1, 2]);
});

test('the only granted office is chosen by itself, whatever its city', () => {
  // Астанинцу открыли офис Алматы на время отпуска коллеги — он и подставляется.
  assert.equal(defaultOfficeId([ALMATY], 'Астана', null, true), 1);
  // Без доступа по офисам — прежнее правило: чужой город сам не подставляется.
  assert.equal(defaultOfficeId([ALMATY], 'Астана', null), null);
  // Двух открытых — свой город. Запомненный офис чужого города и здесь сам не
  // подставляется (правило того же дня: шесть выдач на чужую полку).
  assert.equal(defaultOfficeId([ALMATY, ASTANA], 'Астана', null, true), 2);
  assert.equal(defaultOfficeId([ALMATY, ASTANA], 'Астана', 1, true), null);
  assert.equal(defaultOfficeId([ALMATY, ASTANA], 'Астана', 2, true), 2);
  // Запомненный единственный открытый — даже чужого города: своего в списке нет.
  assert.equal(defaultOfficeId([ALMATY], 'Астана', 1, true), 1);
  assert.equal(defaultOfficeId([ALMATY, ASTANA], null, null, true), null);
});

const SECOND = { id: 4, city: 'Алматы', name: 'Офис Алматы №1', address: 'Улица Жамбыла, 172В угол улицы Байзакова', is_active: true };

test('offices are segments only when two or three and each city is its own', () => {
  assert.equal(accessSegments([ALMATY, ASTANA]), true);
  assert.equal(accessSegments([ALMATY]), true);
  // Два офиса одного города сегментом не различить — список (разбор 23.10.2026).
  assert.equal(accessSegments([ALMATY, ASTANA, SECOND]), false);
  assert.equal(accessSegments([ALMATY, { ...ASTANA, city: '  алматы ' }]), false);
  assert.equal(accessSegments([ALMATY, ASTANA, { id: 6, city: 'Шымкент' }, { id: 7, city: 'Тараз' }]), false);
  assert.equal(accessSegments([ALMATY, { id: 8, city: '' }]), false);
  assert.equal(accessSegments([]), false);
});

test('a lost office is not replaced silently with one of another city', () => {
  // Первый приход списка: свой город или единственный открытый.
  assert.equal(nextOfficeId(null, [ALMATY, ASTANA], { city: 'Астана', granted: true }), 2);
  assert.equal(nextOfficeId(null, [ASTANA], { city: 'Алматы', granted: true }), 2);
  // Выбранный офис на месте — остаётся.
  assert.equal(nextOfficeId(1, [ALMATY, ASTANA], { picked: true, city: 'Астана' }), 1);
  // Его отняли — выбор пустой, даже если остался единственный офис.
  assert.equal(nextOfficeId(1, [ASTANA], { picked: true, city: 'Алматы', granted: true }), null);
  // Список пришёл заново после сброса — сам ничего не подставляет.
  assert.equal(nextOfficeId(null, [ASTANA], { picked: true, city: 'Астана', granted: true }), null);
});

test('only toggled pairs go to the server', () => {
  const draft = accessDraftOf(ACCESS);
  assert.deepEqual(draft, { 1: [11, 12], 2: [] });
  assert.deepEqual(accessChanges(draft, ACCESS), []);
  let next = toggleAccess(draft, 2, 21);
  next = toggleAccess(next, 1, 12);
  assert.deepEqual(next, { 1: [11], 2: [21] });
  assert.deepEqual(accessChanges(next, ACCESS), [
    { water_office_id: 1, user_id: 12, granted: false },
    { water_office_id: 2, user_id: 21, granted: true },
  ]);
  // Сняли и поставили снова — не правка.
  assert.deepEqual(accessChanges(toggleAccess(toggleAccess(draft, 1, 11), 1, 11), ACCESS), []);
  // Черновик не трогается — новый объект на каждое переключение.
  assert.deepEqual(draft, { 1: [11, 12], 2: [] });
});

test('settings row sums up who issues where', () => {
  assert.equal(accessSummary(ACCESS), 'Алматы: 2 · Астана: 0');
  assert.equal(accessSummary({ offices: [], people: [] }), 'Офисов в учёте пока нет');
  assert.equal(accessSummary(null), 'Офисов в учёте пока нет');
  // Два офиса одного города — итогом: перечень с адресами в строку не входит.
  const twins = { offices: [{ ...ALMATY, user_ids: [11, 12] }, { ...SECOND, user_ids: [12] },
    { ...ASTANA, user_ids: [21] }], people: ACCESS.people };
  assert.equal(accessSummary(twins), '3 офиса, доступ у 3 сотрудников');
  assert.equal(accessSummary({ offices: [{ ...ALMATY, user_ids: [11] }, { ...SECOND, user_ids: [] }] }),
    '2 офиса, доступ у 1 сотрудника');
  assert.equal(accessSummary({ offices: [{ ...ALMATY, user_ids: [] }, { ...SECOND, user_ids: [] }] }),
    '2 офиса, доступа пока ни у кого');
  assert.ok(accessSummary(twins).length < 60);
});

test('people are grouped by city, the office city first, order does not follow the ticks', () => {
  const titles = (office) => accessPeopleGroups(ACCESS.people, office).map((group) => [group.title, group.rows.map((p) => p.id)]);
  assert.deepEqual(titles(ALMATY), [
    ['Сотрудники · Алматы', [11, 12]],
    ['Сотрудники · Астана', [21]],
    ['Сотрудники · Шымкент', [41]],
    ['Сотрудники · город не указан', [31]],
  ]);
  assert.deepEqual(titles(ASTANA).map(([title]) => title),
    ['Сотрудники · Астана', 'Сотрудники · Алматы', 'Сотрудники · Шымкент', 'Сотрудники · город не указан']);
  // «  алматы » из карточки — тот же город, а не вторая группа.
  const messy = [...ACCESS.people, { id: 51, name: 'Аманов', city: '  алматы ' }];
  assert.deepEqual(accessPeopleGroups(messy, ALMATY)[0].rows.map((p) => p.id), [11, 51, 12]);
  // Исходный список не переставляется на месте.
  assert.deepEqual(ACCESS.people.map((person) => person.id), [12, 21, 11, 31, 41]);
});

test('a refusal keeps the edits that are still possible', () => {
  // Отметили 21 и 41 в Алматы и 12 в Астане, сняли 11 в Алматы; тем временем
  // 41 перевели из фронт-офисов (его нет среди людей), а Астану вывели из учёта.
  let draft = accessDraftOf(ACCESS);
  draft = toggleAccess(draft, 1, 21);
  draft = toggleAccess(draft, 1, 41);
  draft = toggleAccess(draft, 1, 11);
  draft = toggleAccess(draft, 2, 12);
  const fresh = { offices: [{ ...ALMATY, user_ids: [11, 12] }], people: ACCESS.people.filter((p) => p.id !== 41) };
  const next = rebaseAccessDraft(draft, ACCESS, fresh);
  assert.deepEqual(next, { 1: [12, 21] });
  // Снять можно и у того, кого больше нет в списке: снятие не требует кандидата.
  const gone = rebaseAccessDraft(toggleAccess(accessDraftOf(ACCESS), 1, 12), ACCESS, fresh);
  assert.deepEqual(gone, { 1: [11] });
});

test('window draft: open, toggle, reset, ask before closing', () => {
  let session = accessSession(null, { type: 'open', officeId: 2 }, ACCESS);
  assert.deepEqual(session, { draft: { 1: [11, 12], 2: [] }, officeId: 2, confirmClose: false });
  session = accessSession(session, { type: 'toggle', officeId: 2, userId: 21 }, ACCESS);
  assert.deepEqual(session.draft, { 1: [11, 12], 2: [21] });
  // «Отменить» — снова сохранённое.
  assert.deepEqual(accessSession(session, { type: 'reset' }, ACCESS).draft, { 1: [11, 12], 2: [] });
  // Закрывают с несохранённым: спросить; «Вернуться» снимает вопрос, отметка — тоже.
  session = accessSession(session, { type: 'ask-close' }, ACCESS);
  assert.equal(session.confirmClose, true);
  assert.equal(accessSession(session, { type: 'keep' }, ACCESS).confirmClose, false);
  assert.equal(accessSession(session, { type: 'toggle', officeId: 1, userId: 11 }, ACCESS).confirmClose, false);
  assert.equal(accessSession(session, { type: 'choose', officeId: 1 }, ACCESS).officeId, 1);
  // Отказ сервера переносит правку на свежий доступ.
  const fresh = { offices: [{ ...ASTANA, user_ids: [] }], people: ACCESS.people };
  assert.deepEqual(accessSession(session, { type: 'refused', fresh }, ACCESS).draft, { 2: [21] });
  assert.equal(accessSession(session, { type: 'unknown' }, ACCESS), session);
});

test('closing: stay while saving, ask once with unsaved ticks, then close', () => {
  assert.equal(accessCloseStep({ saving: true, dirty: true, confirmClose: false }), 'stay');
  assert.equal(accessCloseStep({ saving: true, dirty: false, confirmClose: false }), 'stay');
  assert.equal(accessCloseStep({ saving: false, dirty: true, confirmClose: false }), 'ask');
  assert.equal(accessCloseStep({ saving: false, dirty: true, confirmClose: true }), 'close');
  assert.equal(accessCloseStep({ saving: false, dirty: false, confirmClose: false }), 'close');
});
