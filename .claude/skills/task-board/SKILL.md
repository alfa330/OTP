---
name: task-board
description: Работа с бэклогом, канбаном и отчётами о проделанной работе в разделе «Задачи» iCORE/OTP — посмотреть доску, создать задачу, поставить дедлайн и оценку, переставить приоритет в бэклоге, сменить статус, написать отчёт с трудозатратами. Используй, когда просят «посмотри задачи», «добавь в бэклог», «поставь дедлайн», «обнови статус задачи», «что в работе», «сколько времени заняла задача», «напиши отчёт о работе», «отчитайся по задаче».
---

# Бэклог и канбан задач OTP

Раздел «Задачи» на фронте — `src/components/tasks/TasksView.jsx` (вкладка «Обзор») +
`src/components/tasks/TaskBoardWorkspace.jsx` (вкладки «Бэклог», «Доска», «Таймлайн»).
Данные — таблица `tasks` в проде.

## Как подключаться

Через прод-API скриптом `scripts/task_board.py`. Логин/пароль берутся из
`.env.codex.local` (`ADMIN_LOGIN`/`ADMIN_PASSWORD`), транспорт — bearer.
Запускать из `C:/python/OTP-1`, для читаемого вывода Cyrillic — `python -X utf8`.

```bash
python -X utf8 scripts/task_board.py board            # канбан по колонкам
python -X utf8 scripts/task_board.py board --mine     # только мои
python -X utf8 scripts/task_board.py backlog          # бэклог в порядке приоритета
python -X utf8 scripts/task_board.py show 412         # карточка целиком (история, чек-лист, факт. старт)
python -X utf8 scripts/task_board.py recipients       # id исполнителей
```

Любая команда принимает `--json` — отдаёт сырой ответ API, удобно парсить.

## Модель данных (важно понимать до правок)

Новых статусов НЕ вводили. Колонки доски — это существующий жизненный цикл:

| Колонка | Условие |
|---|---|
| Бэклог | `is_backlog = true` (статус при этом `assigned`) |
| К выполнению | `is_backlog = false`, `status = assigned` |
| В работе | `status in (in_progress, returned)` |
| На проверке | `status = completed` |
| Готово | `status = accepted` |

Дополнительные поля `tasks`: `is_backlog`, `backlog_rank` (double, ручной порядок
приоритезации), `estimate_minutes` (оценка), `planned_start_at` (плановый старт),
`started_at` (факт. начало — ставится на первом переходе в `in_progress`).

Ключевое правило: **задача в бэклоге не тревожит исполнителя** — Telegram-уведомление
уходит только когда карточка выносится из бэклога (`promote`).

## Отчёты о проделанной работе

Таблица `task_reports`: журнал по задаче. `kind='progress'` — промежуточный отчёт (можно
писать по ходу работы), `kind='completion'` — итоговый (создаётся автоматически при сдаче
задачи через `status ... completed`). `spent_minutes` — фактические трудозатраты;
сумма по отчётам приходит в задаче как `spent_minutes` и сравнивается с `estimate_minutes`
(чип «факт / оценка» на карточках, метрика «Факт к оценке» на таймлайне).

`tasks.completion_summary` НЕ убирали — он денормализованно хранит текст последнего
итогового отчёта (для Telegram, закреплённого виджета и выгрузок). Правишь итоговый
отчёт — синхронизируется автоматически, руками `completion_summary` не трогай.

Права: писать отчёт может исполнитель, постановщик или админ; **править и удалять —
только автор** (админ может удалить чужой, но не переписать). Отчёт при сдаче
обязателен: `status <id> completed` без `--report` CLI отклонит.

## Команды изменения

```bash
# создать
python -X utf8 scripts/task_board.py create "Тема" --assignee 169 --backlog --estimate 4h
python -X utf8 scripts/task_board.py create "Тема" --assignee 169 --due "2026-08-05 18:00" --priority urgent

# дедлайн / оценка / плановый старт
python -X utf8 scripts/task_board.py deadline 412 --due "2026-08-05 18:00"
python -X utf8 scripts/task_board.py deadline 412 --in 3d4h --estimate 90
python -X utf8 scripts/task_board.py deadline 412 --clear

# бэклог ↔ доска
python -X utf8 scripts/task_board.py promote 412     # в работу (уведомит исполнителя)
python -X utf8 scripts/task_board.py park 412        # обратно в бэклог (только status=assigned)

# приоритет в бэклоге
python -X utf8 scripts/task_board.py rank 412 --top
python -X utf8 scripts/task_board.py rank 412 --after 408

# статус
python -X utf8 scripts/task_board.py status 412 in_progress --comment "взял"
python -X utf8 scripts/task_board.py status 412 completed --report "что сделано" --spent 3h30m
python -X utf8 scripts/task_board.py status 412 accepted

# отчёты о проделанной работе
python -X utf8 scripts/task_board.py reports 412                                  # журнал
python -X utf8 scripts/task_board.py report 412 "Поднял индексы" --spent 2h       # промежуточный
python -X utf8 scripts/task_board.py report 412 "Готово, вот итог" --spent 1h --final
```

Форматы длительности: `90`, `90m`, `4h`, `3d4h`, `2ч30м`.
Форматы даты: `"2026-08-05 18:00"`, `2026-08-05` (→ 18:00), `05.08.2026 14:30`, ISO.

## Права (API их проверяет, не пытайся обойти)

- `in_progress` / `completed` — только исполнитель.
- `accepted` / `returned` / `reopened` — постановщик, админ или СВ (не исполнитель).
- `due_at` / `planned_start_at` — постановщик или админ.
- `is_backlog` / `backlog_rank` / `estimate_minutes` — постановщик, исполнитель или админ.
- В бэклог возвращается только не начатая задача (иначе 409 `BACKLOG_ONLY_FOR_ASSIGNED`).

## Как ставить «правильный» дедлайн

1. `show <id>` — посмотри `estimate_minutes`, `started_at`, историю и чек-лист.
2. Если оценки нет — сначала поставь её (`deadline <id> --estimate ...`); без оценки
   срок берётся с потолка, и таймлайн ничего не показывает.
3. Дедлайн = плановый старт + оценка + буфер. Учитывай реальный темп: медианный цикл
   команды виден на вкладке «Таймлайн», а `show` даёт факт по конкретной задаче.
4. Ставь абсолютный `--due` (а не `--in`), если срок привязан к дате/событию.
5. Не двигай чужие дедлайны молча — это меняет обязательство, скажи пользователю.

## Прямой API (если нужен нестандартный запрос)

- `GET /api/tasks?backlog=only|exclude&status=&tag=&priority=&limit=&offset=`
- `POST /api/tasks` (multipart) — поля `is_backlog`, `estimate_minutes`, `due_at`, `planned_start_at`
- `PATCH /api/tasks/<id>` (json) — `due_at`, `estimate_minutes`, `planned_start_at` + прежние поля
- `POST /api/tasks/board` (json) — батч планирующих правок:
  `{"items":[{"task_id":412,"is_backlog":false,"backlog_rank":2.5,"estimate_minutes":120,"due_at":"..."}]}`
- `POST /api/tasks/<id>/status` (json) — `{"action":"in_progress","comment":""}`;
  для `completed` — `{"action":"completed","report":"...","spent_minutes":210}`
- `GET|POST /api/tasks/<id>/reports` — журнал отчётов; POST `{"body":"...","spent_minutes":120,"kind":"progress"}`
- `PATCH|DELETE /api/tasks/reports/<report_id>` — правка/удаление своего отчёта

Реализация: роуты в `bot_schedule2.py` (`handle_tasks`, `handle_single_task`,
`handle_tasks_board`, `handle_task_reports`, `handle_task_report_item`,
`update_task_status`), логика — `database.py` (`create_task`, `edit_task`,
`update_task_board_state`, `update_task_status`, `create_task_report`,
`update_task_report`, `delete_task_report`, `get_tasks_for_requester`).

## Как писать отчёт (когда просят отчитаться за меня)

1. `show <id>` — посмотри чек-лист, историю и уже написанные отчёты, чтобы не дублировать.
2. Текст по делу: что сделано → к какому результату пришли → что осталось/риски.
   Не пересказывай тему задачи и не пиши «выполнено» без содержания.
3. `--spent` ставь только если есть на чём основываться (история статусов, твои же
   замеры). Выдумывать трудозатраты нельзя — они идут в метрики оценок.
4. Промежуточный отчёт (`report`) не меняет статус. Сдача — `status <id> completed --report ...`.
