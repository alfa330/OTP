# Раздел «ИИ-оценка» (UI)

Стиль — дизайн-кит `src/components/ui/ios.jsx` (SF Pro / slate / iOS), framer-motion, lucide-react.
Точка входа: `view === "ai_qa"` → `<CallQaView/>`. Мок-данных нет: всё приходит с `/api/ai-qa/*`,
при недоступности бэкенда рисуются состояния загрузки / ошибки / «пусто».

## Два субъекта оценки

Раздел оценивает **звонки** (`subject_kind: 'call'`) и **эпизоды переписки WhatsApp у
Верификаторов ОП** (`subject_kind: 'wz_episode'`). Форма карточки одна: строки транскрипта +
критерии + отпечатки прогона. Отличия у чата: нет аудио и языков ASR, есть метаданные
переписки, вложения в строках и порог атрибуции (эпизод, где отвечали несколько операторов,
не оценивается — бэкенд отдаёт `409` с причиной, карточка показывает её текстом).

## Экраны
| Файл | Экран |
|---|---|
| `CallQaView.jsx` | контейнер + вкладки (Обзор / Очередь ревью / Чаты / Оценки / Критерии / База разборов), открытие карточки, отправка разбора |
| `ChatQueue.jsx` | вкладка «Чаты»: сводка пригодности эпизодов + очередь по чатам + «Оценить случайный чат» |
| `CallReviewCard.jsx` | карточка ревью: транскрипт/переписка, вложения, критерии с вердиктом ИИ, подтверждение/правка → разбор |
| `QaDashboard.jsx` | метрики доверия (согласие, точность тревог, RAG-observability) |
| `EvaluationsList.jsx` | уже оценённые ИИ субъекты + «Оценить случайный звонок» |
| `CriteriaClassification.jsx` | классификация критериев (transcript / system_api / manual) по направлениям ОП |
| `AdjudicationsRag.jsx` | каталог правил (база разборов), lifecycle, reindex, rollout |

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

Везде `headers: withAccessTokenHeader()`. `subject` — `call` (по умолчанию) либо `wz_episode`.

- `GET /api/ai-qa/review-queue?limit=&offset=&subject=` →
  `{ items: [{ id, subject, direction, operator, datetime, human_score,
               reasons: ["critical"|"lowconf"|"pending"|"asr"|"media"|"ok"|"new"],
               stale: true|false|null }], total, limit, offset }`
- `GET /api/ai-qa/call/:id?subject=&refresh=1` → `{ call: … }` (см. ниже).
  `409 { error, reason, detail }` — оценить нельзя по существу (например, доля ответов
  оператора ниже порога); `404` — не найден / нет записи.
- `GET /api/ai-qa/random-call` → `{ call: { id, subject:'call', … } }` — случайный оценённый
  человеком звонок с записью.
- `GET /api/ai-qa/random-chat` → `{ call: { id, subject:'wz_episode', operator_share, … } }` —
  случайный пригодный эпизод переписки.
- `GET /api/ai-qa/chat-overview` →
  `{ available, directions:[{id,name}], min_operator_share_pct, min_operator_messages,
     dialogs, unattributed, multi_operator, evaluable, evaluated }`
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
  criteria: [{ idx, criterion_id, name, is_critical, deficiency?:{weight,description},
               source: 'transcript'|'system_api'|'manual',
               ai: 'Correct'|'Incorrect'|'N/A'|'Deficiency'|'Pending', conf, evidence, comment,
               human?, human_comment? }],
  transcript: [{ speaker: 'operator'|'other_operator'|'bot'|'client',
                 seg: [{ t, c? }], start_ms?, ts?, author?, message_id?,
                 media?: { kind: 'image'|'audio'|'file', label, url } }],
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
