# Раздел «ИИ-оценка звонков» (UI)

Стиль — дизайн-кит `src/components/ui/ios.jsx` (SF Pro / slate / iOS), framer-motion, lucide-react.
Точка входа: `view === "ai_qa"` → `<CallQaView/>`.

## Экраны
| Файл | Экран | Статус |
|---|---|---|
| `CallQaView.jsx` | контейнер + вкладки (Обзор / Очередь / Оценки / Критерии / База разборов) | каркас готов |
| `CallReviewCard.jsx` | **карточка ревью** — транскрипт+диаризация+подсветка неуверенности, критерии с вердиктом ИИ, подтверждение/правка → RAG | готов (мок) |
| `QaDashboard.jsx` | метрики и графики согласия | готов (мок) |
| `EvaluationsList.jsx` | все AI-оценки + фильтры | готов (мок) |
| `CriteriaClassification.jsx` | классификация критериев (transcript/system_api/manual) | готов (мок) |
| `AdjudicationsRag.jsx` | просмотр/поиск/правка базы разборов | готов (мок) |

## Подключение в App.jsx (как `MonitoringScaleView`)
```jsx
import CallQaView from './components/call_qa/CallQaView';
// ...в области рендера вью:
{( view === "ai_qa" && (
    <CallQaView user={user} showToast={showToast} apiBaseUrl={API_BASE_URL}
                withAccessTokenHeader={withAccessTokenHeader} directions={directions} />
))}
```
Плюс пункт меню (иконка `Sparkles`), который ставит `view="ai_qa"`, гейт по роли (admin / sv / trainer).

## API-контракт (для бэкенда)
- `GET  /api/ai-qa/review-queue?direction=&limit=` → `[{ id, direction, operator, datetime, reasons:["critical"|"lowconf"|"pending"] }]`
- `GET  /api/ai-qa/call/:id` → объект как `MOCK_CALL` в `CallReviewCard.jsx`:
  `{ id, direction, operator, datetime, human_score, languages, asr_mean_conf,
     transcript:[{ speaker:"operator"|"client", seg:[{ t, c? }] }],
     criteria:[{ idx, name, is_critical, source, ai, conf, evidence, comment }] }`
- `POST /api/ai-qa/adjudicate` ← `{ call_id, decisions:{ [idx]:{ verdict, reason } } }`
  → создаёт записи в `qa_adjudications` (call_qa/review/queue.on_adjudication).
- `GET/POST /api/ai-qa/criteria-config` ↔ таблица `criterion_config`.
- `GET  /api/ai-qa/adjudications?direction=&criterion=&q=` → список разборов (RAG).

Все данные сейчас — мок в форме этого контракта; бэкенд подменяет на реальные вызовы (`axios` + `withAccessTokenHeader`, как в других вью).
