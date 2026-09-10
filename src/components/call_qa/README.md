# Раздел «ИИ-оценка» (UI)

Стиль — дизайн-кит `src/components/ui/ios.jsx` (SF Pro / slate / iOS), framer-motion, lucide-react.
Точка входа: `view === "ai_qa"` → `<CallQaView/>`. Мок-данных нет: всё приходит с `/api/ai-qa/*`,
при недоступности бэкенда рисуются состояния загрузки / ошибки / «пусто».

## Три отдела и селектор

Раздел оценивает **ОП**, **СЗоВ** и **Тез КЦ**. Список доступных отделов приходит из
`GET /api/ai-qa/departments`; селектор (`IosSegmented` в шапке) показывается только тем, кому
открыт больше одного отдела — у главы и СВ он был бы одной неактивной кнопкой. Выбранный код
уходит параметром `department` во ВСЕ запросы раздела; ручка, забывшая его передать, показала
бы главе СЗоВ данные всех отделов (сторожит `test_every_scoped_route_resolves_the_department`).
Смена отдела закрывает открытую карточку и сбрасывает очередь: они принадлежали прежнему отделу.

## Пять субъектов оценки

Субъект — это таблица-источник; знание о них лежит в одном месте — `subjects.js`. Раньше строка
`'wz_episode'` была вписана в четыре компонента, и с появлением переписки СЗоВ и Тез КЦ каждый
решал бы по-своему, чат перед ним или звонок.

| `subject_kind` | Что это | Отдел |
|---|---|---|
| `call` | звонок, оценённый человеком | все |
| `imported_call` | звонок из АТС, БЕЗ оценки в журнале | СЗоВ, Тез КЦ |
| `wz_episode` | эпизод Wazzup | ОП (Верификаторы) |
| `c2d_snapshot` | заявка Chat2Desk | СЗоВ |
| `ca_episode` | эпизод ChatApp | Тез КЦ |

Форма карточки одна: строки транскрипта + критерии + отпечатки прогона. Отличия у переписки:
нет аудио и языков ASR, есть метаданные чата, вложения в строках и гейт атрибуции (переписку,
которую вели несколько сотрудников, оценить нельзя — бэкенд отдаёт `409` с причиной, карточка
показывает её текстом). У Chat2Desk единица — ЗАЯВКА, и в подписях стоит это слово.

## Экраны
| Файл | Экран |
|---|---|
| `CallQaView.jsx` | контейнер + вкладки (Обзор / Очередь ревью / Чаты / Оценки / Критерии / База разборов), открытие карточки, отправка разбора |
| `ChatQueue.jsx` | вкладка «Чаты»: сводка пригодности + подбор случайной переписки источника отдела |
| `subjects.js` | виды субъектов, источник переписки отдела, подписи — одно место вместо литералов |
| `CallReviewCard.jsx` | карточка ревью: транскрипт/переписка, вложения, критерии с вердиктом ИИ, подтверждение/правка → разбор |
| `QaDashboard.jsx` | метрики доверия (согласие, точность тревог, RAG-observability) |
| `EvaluationsList.jsx` | уже оценённые ИИ субъекты + «Оценить случайный звонок» + «Из АТС» (СЗоВ, Тез КЦ) |
| `CriteriaClassification.jsx` | классификация критериев (transcript / system_api / manual) по направлениям выбранного отдела |
| `AdjudicationsRag.jsx` | каталог правил (база разборов), lifecycle, reindex, rollout |
| `QaFilters.jsx` | панель фильтров: период, направление, группа, сотрудник, балл ИИ, оценка человека, поиск |
| `filters.js` | правила отбора — одно место для панели, обоих списков и подбора |

## Фильтры

Панель стоит в `CallQaView` ПОД вкладками и накрывает три из них — «Очередь ревью»,
«Чаты» и «Звонки». Внутрь списка её класть нельзя: отбор сбрасывался бы при
переключении вкладки, а панель прыгала бы по экрану. У «Обзора», «Критериев» и «Базы
разборов» своей выборки по людям нет — там панели нет вовсе (у `stats()` фильтров тоже
нет, и панель рядом с его цифрами противоречила бы сама себе).

Форма — канон проекта («Касания» `cdr/TouchesView.jsx`, «Посылки» `parcels/ParcelsView.jsx`),
три яруса:

1. **Видимая полоса** — период (`IosDateRangePicker` с дефолтным чипом) и поиск. С них
   начинают работу; прятать их за кнопкой — лишний клик на каждый заход.
2. **Чипы отобранного** сразу под полосой, ДО панели: так они не прыгают вниз при
   раскрытии и видны, когда панель свёрнута. Сброс живёт в них — отдельной кнопки
   «Сбросить» в подвале панели не нужно.
3. **Карточка-сетка** `sm:grid-cols-2 lg:grid-cols-3` с подписями `iosGroupLabel`:
   направление, группа, сотрудник, оценка человека, балл ИИ. Три колонки, а не четыре:
   полей пять, и в четвёрке низ рвётся 4+1, а по три ряды складываются осмысленно
   («кто смотрим» / «какие оценки»); в очереди ревью последних двух нет — выходит ровно
   один полный ряд.

Две ловушки разметки:

* Подпись над `IosSegmented` — в `<div>`, НЕ в `<label>`. Сегмент-контрол это `tablist`
  из кнопок, а `<label>` без `htmlFor` делает своим управляемым элементом первую кнопку
  внутри: клик по подписи «Оценка человека» молча выбирал «Все», то есть снимал фильтр.
  Вокруг `CustomSelect` обёртка `<label>` уместна — там первый потомок это триггер, и
  клик по подписи просто раскрывает список.
* «Балл ИИ» — ОДИН орган (плитка с двумя прозрачными полями и кольцом на `focus-within`),
  а не два поля рядом: балл и считается одним фильтром, и снимается одним чипом «70—100».

Фильтрует **сервер** (`call_qa.api._list_filters_predicate`). Клиентская фильтрация
разошлась бы со счётчиком «Показано N из M»: `total` считается по всей выборке, а не по
загруженной странице, поэтому любой фильтр обязан попасть и в список, и в счётчик.

Правила живут в `filters.js` и продублированы на бэке (`normalise_list_filters`) —
node-тест `tests/ai_qa_filters.test.mjs` сверяет, что обе стороны знают ОДИН набор осей.

Отбор действует и на кнопки подбора: «Оценить случайный звонок»/«…чат» уходят с теми же
параметрами, а «Из АТС» берёт из отбора сотрудника и период (направление и группа сужают
ЛЮДЕЙ, а не звонки, поэтому в АТС не уходят). Кнопка, игнорирующая выставленный рядом
фильтр, читается как сломанная.

Две ловушки:

* **Группа — это дата.** Колонки `group_id` у сотрудника нет, есть история членств
  (`group_operator_memberships`). Списки берут членство, пересекающее МЕСЯЦ разговора
  (та же мерка, что у часов), а не «текущую группу»: иначе переезд одного человека
  переписывал бы прошлое. В справочнике фильтров рядом с фамилией, наоборот, стоит
  группа на сегодня — там это ответ на «кто где сейчас».
* **Корзина «Без группы» обязательна.** У части звонков из АТС учётной записи нет вовсе
  (осталось имя из телефонии); без неё такие строки молча исчезали бы из любого разреза
  по группам, и сумма по группам не сходилась бы с общим числом оценок.

Вкладки «Критерии» и «База разборов» скрыты у СВ ОП (`isScopedSupervisor`) — бэкенд их тоже
ограничивает. Правка/удаление правил — только `super_admin`.

## Подключение в App.jsx
```jsx
{view === "ai_qa" && canAccessAiQaSection && (
    <Suspense fallback={…}>
        <CallQaView user={user} showToast={showToast} apiBaseUrl={API_BASE_URL}
                    withAccessTokenHeader={withAccessTokenHeader} directions={directions} />
    </Suspense>
)}
```
Доступ — `canAccessAiQaForUser`: `super_admin`, глава ОП/СЗоВ, СВ отдела продаж, whitelist по id.
Ключ `'ai_qa'` также должен быть в `SALES_SUPERVISOR_VIEWS` (`src/utils/departmentViews.js`).

## API-контракт

Везде `headers: withAccessTokenHeader()`. `subject` — один из пяти видов (`call` по умолчанию).
`department` — код отдела из селектора; без него бэкенд подставляет единственный доступный, а
чужой отдел отвергает (`403`).

- `GET /api/ai-qa/departments` →
  `{ items:[{ code, name, chat_subject }], current, can_switch }` — что открыто этому человеку.

Списки и оба подбора дополнительно принимают ФИЛЬТРЫ (все необязательные, пустое =
«фильтра нет»): `direction_id`, `group_id` (или `none` — «без группы»), `operator_id`,
`date_from`/`date_to` (ГГГГ-ММ-ДД, по дате разговора), `reviewed` (`yes`/`no`),
`score_min`/`score_max` (0…100), `q` (имя сотрудника или номер субъекта). Неверное
значение — `400` с текстом, а не молча снятый фильтр. Подбор (`random-call`,
`random-chat`) берёт из набора только оси, сужающие круг РАЗГОВОРОВ: сотрудник, группа,
направление, период.

- `GET /api/ai-qa/filter-options?department=&subject=` →
  `{ directions:[{id,name}], groups:[{id,name,operators,evaluations}],
     operators:[{id,name,direction_id,direction,group_id,group,evaluations,fired,has_sip,can_pull}] }`
  — справочник для панели и диалога подтяжки. Своя ручка, а не общие `/api/groups` и
  `/api/admin/users`: те живут по своим правам, и часть зрителей раздела (наблюдатель
  «Маркетинга», доступ по whitelist) до них не допущена — панель была бы пустой при
  полном списке оценок. Уволенные попадают в список, только если оценки у них остались.

- `GET /api/ai-qa/review-queue?limit=&offset=&subject=` →
  `{ items: [{ id, subject, direction, operator, datetime, human_score,
               reasons: ["critical"|"lowconf"|"pending"|"asr"|"media"|"ok"|"new"],
               stale: true|false|null }], total, limit, offset }`
- `GET /api/ai-qa/call/:id?subject=&refresh=1` → `{ call: … }` (см. ниже).
  `409 { error, reason, detail }` — оценить нельзя по существу (например, доля ответов
  оператора ниже порога); `404` — не найден / нет записи.
- `GET /api/ai-qa/random-call?department=` →
  `{ call: { id, subject:'imported_call'|'call', in_journal, … } }` — сначала звонок из АТС БЕЗ
  оценки в журнале, и только когда такие кончились — оценённый человеком.
- `POST /api/ai-qa/pull-call` ← `{ department, date_from, date_to, incoming, outgoing, count }`
  → `{ calls:[…] }` — подтянуть НОВЫЙ звонок прямо из АТС (СЗоВ — Oktell, Тез КЦ — Binotel).
  Оценки в журнале не появляется. У ОП кнопки нет: там записи загружают вручную.
- `GET /api/ai-qa/random-chat?department=` →
  `{ call: { id, subject, operator_share, human_outbound_count, … } }` — случайная пригодная
  переписка источника отдела.
- `GET /api/ai-qa/chat-overview?department=` →
  `{ available, department, subject, directions:[{id,name}], min_operator_messages,
     dialogs, unattributed, multi_operator, evaluable, evaluated }`.
  `min_operator_share_pct` приходит ТОЛЬКО у эпизодных источников: у заявок Chat2Desk доли
  ответов не бывает, и по отсутствию поля фронт понимает, что эту мерку показывать не нужно.
- `GET /api/ai-qa/evaluations?limit=&offset=&subject=` →
  `{ items:[{ id, subject, direction, operator, datetime, ai, human }], total }`
- `POST /api/ai-qa/adjudicate` ←
  `{ call_id, subject_kind, direction_id, evaluation_run_id, scale_revision_id,
     evaluation_fingerprint,
     items:[{ criterion_id, criterion_idx, criterion_name, ai_verdict, correct_verdict,
              reason, not_covered, situation, excerpt, excerpt_verified, evidence_status }] }`
  `items: []` — это «Подтвердить» (тоже результат ревью: субъект уходит из очереди).
- `POST /api/ai-qa/adjudicate/refine` ← `{ direction_id, criterion_idx, criterion_name,
  ai_verdict, ai_comment, correct_verdict, reason, excerpt, excerpt_verified, evidence_status }`
  → `{ proposal: { rule, situation, not_covered, note_to_reviewer } }` (подсказка; сохраняет человек).
- `GET /api/ai-qa/stats` → метрики дашборда.
- `GET/POST /api/ai-qa/criteria-config?direction_id=` ↔ `criterion_config`.
- `GET /api/ai-qa/adjudications`, `PUT/DELETE /api/ai-qa/adjudications/:id`,
  `POST /api/ai-qa/adjudications/:id/reindex`, `GET/PUT /api/ai-qa/rag-rollout`.

### Объект `call` (карточка)

```
{ id, subject_kind: 'call'|'wz_episode', direction, direction_id, operator, datetime,
  ai_score, human_score, has_human_review,
  score_breakdown: { verified_weight, unchecked_weight,
                     unchecked: [{ idx, name, weight, source }] },
  score_breakdown: { verified_weight, unchecked_weight,
                     unchecked: [{ idx, name, weight, source }] },
  criteria: [{ idx, criterion_id, name, is_critical, deficiency?:{weight,description},
               source: 'transcript'|'system_api'|'manual',
               ai: 'Correct'|'Incorrect'|'N/A'|'Deficiency'|'Pending', conf, evidence, comment,
               human?, human_comment? }],
  transcript: [{ speaker: 'operator'|'other_operator'|'bot'|'client',
                 seg: [{ t, c? }], start_ms?, ts?, author?, message_id?,
                 media?: { kind: 'image'|'audio'|'document'|'file', label, url } }],
  evaluation: { run_id, fingerprint_short, knowledge_revision, retrieval_status,
                retrieval_ms, stale, rollout_mode, rag_enabled },
  _cached, _stale, _previous_evaluation_stale,
  _evaluation_run_id, _scale_revision_id, _evaluation_fingerprint,

  // только 'call'
  audio_url, languages: { ru: 62, kk: 38 }, asr_mean_conf,

  // только 'wz_episode'
  chat: { channel_id, chat_id, contact_name, contact_phone, started_at, ended_at,
          messages_count, inbound_count, operator_share, human_outbound_count, authors },
  media: { total, ready, failed, source: 'messages'|'expired' },
  eligibility: { operator_share_pct, min_operator_share_pct, human_outbound_count, … } }
```

`speaker` у чата: `operator` — оцениваемый сотрудник, `other_operator` — другой сотрудник в том
же эпизоде (его слова оператору не в счёт), `bot` — рассылка. `start_ms` есть только у звонка
(кнопка перехода по записи), у чата вместо него `ts` — локальное время сообщения.

Вердикты ИИ: `Correct | Incorrect | N/A | Deficiency | Pending` («Недочёт» — только у критериев
с `deficiency`). `human`/`human_comment` — пер-критерийная оценка супервайзера
(`Correct | Incorrect | N/A | Deficiency | Error`), прикрепляется свежей при каждом открытии.

`score_breakdown` — из чего сложился балл: `unchecked_weight` это вес критериев,
которые ИИ проверить не может (`system_api`/`manual`) и которые формула зачитывает
по умолчанию. Карточка показывает их отдельной пометкой рядом с баллом: «ИИ: 85 /
из них 30 не проверено».

`score_breakdown` — из чего сложился балл: `unchecked_weight` это вес критериев,
которые ИИ проверить не может (`system_api`/`manual`) и которые формула зачитывает
по умолчанию. Карточка показывает их отдельной пометкой рядом с баллом: «ИИ: 85 /
из них 30 не проверено».

## Инварианты, которые легко сломать

* Подтверждение недоступно при пустом `transcript` (`canSubmit = hasCriteria && hasTranscript`) —
  любой субъект обязан отдавать строки транскрипта в этой форме.
* Цитата разбора проверяется на клиенте по тексту `transcript` (NFKC + lowercase + удаление
  небуквенно-цифровых, минимум 4 символа) — зеркало `call_qa/review/evidence.py`. Поэтому текст
  строк карточки и текст, который видела модель, строятся одним проходом на бэкенде.
* Карточка перемонтируется по `key={callData._evaluation_run_id || callData.id}` — субъект без
  `_evaluation_run_id` должен иметь уникальный в разделе `id`.
* Оценка супервайзера (`criteria[].human`, `human_score`, `has_human_review`) прикрепляется
  свежей при каждом открытии и НЕ входит в immutable-кэш. У эпизода она берётся не по
  `calls.id = id эпизода`, а через снапшот переписки (`calls.c2d_snapshot_id`).
