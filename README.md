# OTP

## Local start

```powershell
npm install
npm run dev
```

## Production build

```powershell
npm run build
```

`dist/` will contain ready static files.

## Chat2Desk daily metrics sync

The backend can import chat-manager response time and ratings from Chat2Desk once per day for the previous day.

Required:

```env
CHAT2DESK_API_TOKEN=your_api_token
```

Optional:

```env
CHAT2DESK_API_BASE_URL=https://api-02.chat2desk.kz
CHAT2DESK_AUTH_SCHEME=raw
CHAT2DESK_SYNC_ENABLED=true
CHAT2DESK_SYNC_TIMEZONE=Asia/Almaty
CHAT2DESK_SYNC_HOUR=4
CHAT2DESK_SYNC_MINUTE=10
CHAT2DESK_SYNC_DAYS_BACK=1
CHAT2DESK_API_MAX_PAGES=100
```

## Контроль опозданий (Workpace)

Раздел «Бот опозданий» следит за отметками сотрудников в Workpace и шлёт нарушения
в рабочие чаты Telegram нашим же ботом. Раньше это был отдельный сервис
`group_late_bot` на Render со своим ботом и состоянием в JSON-файлах — теперь всё
внутри приложения: опрос Workpace раз в 2 минуты идёт джобой планировщика, код
лежит в пакете `group_late/`, а настройки чатов, отбивки, отчёты и журнал опросов —
в таблицах `glb_*`.

Обязательные переменные (без них джоба не заводится, а отчёты собрать нечем):

```env
WORKPACE_LOGIN=your-workpace-login
WORKPACE_PASSWORD=your-workpace-password
```

Необязательные:

```env
WORKPACE_BASE_URL=https://api.workpace.kz
LATE_THRESHOLD_MINUTES=1                    # с какой минуты опоздание — нарушение
GROUP_LATE_MISSING_IN_MINUTES=10            # «отсутствует на месте» после начала смены
GROUP_LATE_MISSING_OUT_MINUTES=60           # «нет отметки об уходе» / «поздний уход»
GROUP_LATE_MAX_REPORT_DAYS=31               # максимальный период одного отчёта
GROUP_LATE_RETENTION_EVENTS_DAYS=180        # сколько хранить отбивки
GROUP_LATE_RETENTION_REPORT_FILES_DAYS=60   # сколько хранить Excel-файлы отчётов
GROUP_LATE_RETENTION_POLL_RUNS_DAYS=7       # сколько хранить журнал опросов
```

Чтобы подключить чат: добавьте бота в группу — она появится в разделе выключенной,
останется включить её тумблером и выбрать отделы.

Вкладка «Сотрудники» показывает дисциплину за период в разрезе отделов Workpace:
ФИО, город, число опозданий и минуты, минуты раннего ухода, неявки и подозрительные
отметки. В таблице **весь состав отдела**, а не только нарушители; нарушения берутся
из `glb_events` по дате смены, поэтому цифры сходятся с «Обзором» и «Отбивками».
Поздний уход и отсутствие отметки об уходе в таблицу не выносятся — они в «Отбивках».

Откуда состав, зависит от того, знаем ли мы отдел у себя. Список пар один на весь
раздел — `GROUP_LATE_BOT_DEPARTMENT_SCOPES` в `bot_schedule2.py` (сейчас
`front_office` → «Регионы»); он же ограничивает раздел главе отдела.

* **Отдел с парой** — состав из iCore: действующие операторы этого отдела
  (`role = 'operator'`, статус не `fired`), с их ФИО и городом. В Workpace числятся
  и другие компании холдинга, поэтому тех, кого нет в iCore, в таблице нет — их
  количество показано подписью под таблицей. Уволенные и руководители попадают в
  таблицу, только если за период у них были нарушения (помечены «вне состава»).
* **Остальные отделы** — состав из кэша `glb_employees`, который наполняет опрос
  Workpace (и кнопка «Обновить из Workpace» на вкладке «Отделы»): сверять не с чем,
  поэтому показываем всех, кого знает Workpace.

Нарушения сшиваются с сотрудником по `employeeId`/`employeeExternalId` и по ФИО.
Связи между справочниками нет, ключ — ФИО, поэтому вне пары матчить нельзя: тёзка
из соседнего отдела подставил бы чужое имя и чужой город. Написание складывается —
казахские буквы к русским, отчество необязательно («ҚҰРМАНОВ ҚАЙРАТ» →
«Курманов Кайрат», «Досанбаев Асан Тестович» → «Досанбаев Асан»), а
фамилия с опечаткой в одну букву при точно совпавшем имени тоже находится
(«УНГАРБАЕВА» → «Сынакабаева»), но только если кандидат в отделе один. Колонка
«Город» появляется только у отделов, где он заполнен.

## GitHub Pages

1. Push repository to GitHub (branch `main` or `master`).
2. Open repository settings: `Settings -> Pages`.
3. In **Build and deployment**, choose **Source: GitHub Actions**.
4. Workflow `.github/workflows/deploy-pages.yml` will build and publish automatically.
5. Wait for workflow completion in `Actions` tab and open published URL.

### Base path notes

- For GitHub Pages we use `VITE_BASE_PATH=/${REPO_NAME}/` in workflow.
- For custom domain (root), use `/` as base path.
