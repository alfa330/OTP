/* «Обращения в Яндекс» — раздел CRM, из которого оператор пишет в поддержку
 * Яндекса от кабинета таксопарка.
 *
 * Справочник снят 20.11.2026 с формы «Новое обращение в поддержку Яндекса»:
 * темы и поля CRM каждый раз берёт из Диспетчерской, поэтому здесь лежит ровно
 * то дерево, которое оператор увидел в тот день, — 10 разделов, 83 темы, из
 * них 62 конечные. Тема бывает трёх видов: «nested» — раскрывает подтемы, «simple» — даёт
 * поля обращения, «link» — обращение по ней не создают, вместо формы
 * показывается подсказка.
 *
 * Поле темы — массив:
 *   ['label', подсказка]                       — текст над полями;
 *   [вид, имя, подпись, обязательное, подсказка?] — вид: text, textarea, date,
 *                                                  date_period, file.
 * Подсказка — список кусков: строка или [текст, ссылка]. Ссылки ведут в
 * открытую базу знаний Яндекса, как и в рабочей форме.
 *
 * Список обращений ниже — УЧЕБНЫЙ: авторы, тексты и номера придуманы. Настоящие
 * обращения содержат телефоны и имена водителей, им здесь не место.
 */

export const YA_PARKS = [
    "2донгелек (Алматы)",
    "Amanat (Актобе)",
    "Amanat (Павлодар)",
    "Amanat (Петропавловск)",
    "Amanat (Тараз)",
    "Amanat (Уральск)",
    "Anytime (Алматы)",
    "Global (Алматы)",
    "Global (Астана)",
    "Global (Шымкент)",
    "iPartner (Астана)",
    "iTaxi (Актау)",
    "iTaxi (Актобе)",
    "iTaxi (Алматы)",
    "iTaxi (Астана)",
    "iTaxi (Атырау)",
    "iTaxi (Жанаозен) — нет доступа к обращениям",
    "iTaxi (Караганда)",
    "iTaxi (Кокшетау)",
    "iTaxi (Костанай)",
    "iTaxi (Кызылорда)",
    "iTaxi (Павлодар)",
    "iTaxi (Петропавловск)",
    "iTaxi (Семей)",
    "iTaxi (Талдыкорган)",
    "iTaxi (Тараз)",
    "iTaxi (Туркестан)",
    "iTaxi (Уральск)",
    "iTaxi (Шымкент)",
    "iTaxi (доставка) (Алматы)",
    "iTaxi (доставка) (Астана)",
    "iTaxi (доставка) (Шымкент)",
    "iTaxi 2 (Алматы)",
    "iTaxi 2 (Астана)",
    "iTaxi 2 (Усть-Каменогорск)",
    "iTaxi 2 (Шымкент)",
    "iTaxi 3 (Алматы)",
    "iTaxi курьер (Алматы)",
    "Jana (Алматы)",
    "Jana Taxi (Актобе)",
    "Jana Taxi (Атырау)",
    "Jana Taxi (Тараз)",
    "Qazaq (Алматы)",
    "Qazaq (Астана)",
    "Salam Taxi (Алматы)",
    "Salam Taxi (доставка) (Алматы)",
    "Tenge (Астана)",
    "Адал (Семей)",
    "Аманат (Актау)",
    "Аманат (Караганда)",
    "Аманат (Кокшетау)",
    "Аманат (Костанай)",
    "Аманат (Кызылорда)",
    "Аманат (Семей)",
    "Аманат (Талдыкорган)",
    "Бизнес Партнер (Алматы)",
    "Бизнес Партнер (Астана)",
    "Бизнес Партнер (Шымкент)",
    "ДепТакси (Актобе)",
    "ДепТакси (Алматы)",
    "ДепТакси (Астана)",
    "ДепТакси (Кокшетау)",
    "ДепТакси (Костанай)",
    "ДепТакси (Павлодар)",
    "ДепТакси (Петропавловск)",
    "ДепТакси (Тараз)",
    "ДепТакси Доставка (Алматы)",
    "Достойный (Алматы)",
    "Достойный (Астана)",
    "Достойный (Шымкент)",
    "Жана межгород (Алматы)",
    "Каспий (Актау)",
    "Ноль такси (Алматы)",
    "Самозанятый (Павлодар)",
    "Самозанятый Партнер (Алматы)",
    "Стабильный (Алматы)",
    "Такси 24 (Алматы)",
    "Такси 24 (Астана)",
    "Усть-Каменогорск (Усть-Каменогорск)",
    "Халык (Алматы)",
    "Честный (Актау)",
    "Честный (Алматы)",
    "Честный (Астана)",
    "Честный (Атырау)",
    "Честный (Караганда)",
    "Честный (Кызылорда)",
    "Честный (Талдыкорган)",
    "Честный (Темиртау)",
    "Честный (Шымкент)",
    "Честный Каскелен (Алматы)",
];

export const YA_PARKS_CLOSED = ["iTaxi (Жанаозен) — нет доступа к обращениям"];

export const YA_FILES = {"max_count": 8, "max_size_mb": 20};

export const YA_TOPICS = [
    {
        title: "Вопросы об исполнителе",
        type: "nested",
        topics: [
            {
                title: "Платный найм",
                type: "simple",
                inputs: [
                    ["text", "driver_license", "Номер в/у", true],
                    ["text", "hiring_full_name", "ФИО водителя (указанное при найме)", false],
                    ["text", "hiring_date", "Дата привлечения", false],
                    ["text", "hiring_driver_type", "Тип водителя", false],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", true],
                ],
            },
            {
                title: "Не поступают заказы",
                type: "simple",
                inputs: [
                    ["label", [["Инструкция с общими рекомендациями", "https://fleet.yandex.ru/knowledge-base/low-demand.html"]]],
                    ["text", "driver_license", "Номер в/у", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Рейтинг и показатели",
                type: "simple",
                inputs: [
                    ["label", [["Общая информация", "https://fleet.yandex.ru/knowledge-base/drivers-loyality.html"]]],
                    ["text", "driver_license", "Номер в/у", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Проблемы с Яндекс ID",
                type: "simple",
                inputs: [
                    ["label", ["Как правило, для решения проблемы с Яндекс ID требуется фото водителя с ВУ и скриншоты экранов с ошибкой. Если вы прикрепите эти файлы к обращению сразу, то мы сможем помочь вам быстрее.\n\nПодробнее про Яндекс ID читайте в ", ["базе знаний", "https://parks.yandex/ru-ru/knowledge-base/abouttaxi/prepare-a-driver/yandex-id"], "."]],
                    ["text", "driver_license", "Номер в/у", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Обновить информацию об исполнителе",
                type: "nested",
                topics: [
                    {
                        title: "Редактирование карточки исполнителя",
                        type: "simple",
                        inputs: [
                            ["label", ["Если водитель сменил в/у, ему нужно обратиться в поддержку самостоятельно"]],
                            ["text", "driver_license", "Номер в/у", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", true],
                        ],
                    },
                ],
            },
            {
                title: "Добавление признака «Слабослышащий»",
                type: "simple",
                inputs: [
                    ["text", "driver_license", "Номер в/у", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", true],
                ],
            },
            {
                title: "Вопрос про тестирование",
                type: "simple",
                inputs: [
                    ["text", "driver_license", "Номер в/у", true],
                    ["label", ["Укажите, пожалуйста, тариф, по которому водитель проходит тестирование"]],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Работа приложения исполнителя",
                type: "simple",
                inputs: [
                    ["text", "driver_license", "Номер в/у", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", true],
                ],
            },
            {
                title: "Регистрация самозанятого исполнителя",
                type: "simple",
                inputs: [
                    ["label", [["Инструкция", "https://fleet.yandex.ru/knowledge-base/recurring-payments-from-self-employed.html"]]],
                    ["label", ["Если у вас вопрос по периодическому списанию, выберите раздел «Финансы и документация» — «Информация по выплатам сервиса»"]],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Ограничение доступа к сервису",
                type: "simple",
                inputs: [
                    ["label", [["Информация про ограничения", "https://fleet.yandex.ru/knowledge-base/blocking-drivers-and-cars.html"]]],
                    ["text", "driver_license", "Номер в/у", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Покупка смены",
                type: "nested",
                topics: [
                    {
                        title: "Исполнитель не может купить смену",
                        type: "simple",
                        inputs: [
                            ["text", "driver_license", "Номер в/у", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", true],
                        ],
                    },
                    {
                        title: "Вопрос о купленной смене",
                        type: "simple",
                        inputs: [
                            ["date", "purchase_date", "Дата покупки смены", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                ],
            },
        ],
    },
    {
        title: "Вопросы о ТС",
        type: "nested",
        topics: [
            {
                title: "Отсутствует категория ТС",
                type: "simple",
                inputs: [
                    ["text", "car_number", "Госномер ТС", true],
                    ["label", ["Укажите тариф, к которому нет доступа"]],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Обновить информацию о ТС",
                type: "simple",
                inputs: [
                    ["text", "car_number", "Госномер ТС", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", true],
                ],
            },
            {
                title: "Ограничение доступа ТС к сервису",
                type: "simple",
                inputs: [
                    ["label", ["Если у автомобиля полностью изменился госномер, создайте, пожалуйста, новую карточку"]],
                    ["text", "car_number", "Госномер ТС", true],
                    ["label", ["Укажите причину ограничения в карточке авто"]],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Брендирование и приоритет",
                type: "nested",
                topics: [
                    {
                        title: "Приоритет за оклейку",
                        type: "simple",
                        inputs: [
                            ["text", "car_number", "Госномер ТС", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                    {
                        title: "Другой приоритет",
                        type: "simple",
                        inputs: [
                            ["label", [["Информация", "https://pro.yandex/ru-ru/moskva/base/priority"]]],
                            ["text", "driver_license", "Номер в/у", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                ],
            },
            {
                title: "Гарантийное обслуживание",
                type: "simple",
                inputs: [
                    ["text", "car_number", "Госномер ТС", true],
                    ["text", "car_vin", "VIN", true],
                    ["text", "service_center", "Название сервисного центра", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Подтверждение права использования",
                type: "simple",
                inputs: [
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
        ],
    },
    {
        title: "Вопрос по заказам и бонусам",
        type: "nested",
        topics: [
            {
                title: "Вопрос про комиссию",
                type: "simple",
                inputs: [
                    ["label", [["Общая информация", "https://pro.yandex/ru-ru/moskva/base/comission"]]],
                    ["text", "order_id", "ID заказа", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Вопрос про бонус",
                type: "nested",
                topics: [
                    {
                        title: "Бонус за конкретный заказ",
                        type: "simple",
                        inputs: [
                            ["text", "order_id", "ID заказа", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                    {
                        title: "Бонус водителю за период",
                        type: "simple",
                        inputs: [
                            ["label", [["Общая информация", "https://pro.yandex/ru-ru/moskva/base/bonuses"]]],
                            ["text", "driver_license", "Номер в/у", true],
                            ["date_period", "period", "Период, за который имеются вопросы по начислению бонусов", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                ],
            },
            {
                title: "Вопрос по оплате заказа",
                type: "simple",
                inputs: [
                    ["label", [["Общая информация", "https://fleet.yandex.ru/knowledge-base/problems-with-payment.html"]]],
                    ["text", "order_id", "ID заказа", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Забытые вещи",
                type: "simple",
                inputs: [
                    ["text", "order_id", "ID заказа", false],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Жалоба на пользователя",
                type: "simple",
                inputs: [
                    ["text", "order_id", "ID заказа", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Помощь с завершением заказа по тарифам Доставка/Курьер/Грузовой",
                type: "simple",
                inputs: [
                    ["text", "order_id", "ID заказа", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
        ],
    },
    {
        title: "Профиль таксопарка",
        type: "nested",
        topics: [
            {
                title: "Смена названия и контактов Диспетчерской",
                type: "simple",
                inputs: [
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Смена учётной записи директора/почты для входа",
                type: "simple",
                inputs: [
                    ["label", ["Чтобы изменить почту директора, в Диспетчерской должен быть сотрудник с нужной почтой."]],
                    ["label", ["Важно: заявка на изменение почты будет обработана при запросе с учётной записи директора."]],
                    ["text", "email", "Новая почта директора", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Смена почты для рассылок",
                type: "nested",
                topics: [
                    {
                        title: "Информационные письма о новых тарифах/программах",
                        type: "simple",
                        inputs: [
                            ["label", ["Укажите почту, которую вы хотите использовать для рассылок"]],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                    {
                        title: "Письма об отмене платежа и фотоконтроле",
                        type: "simple",
                        inputs: [
                            ["label", ["Укажите почту, которую вы хотите использовать для рассылок"]],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                ],
            },
            {
                title: "Смена почты для документооборота",
                type: "nested",
                topics: [
                    {
                        title: "Для РФ",
                        type: "simple",
                        inputs: [
                            ["label", ["Напишите на почту taxi-docs@yandex-team.ru. В письме обязательно укажите номер договора."]],
                            ["label", [["Шаблон заявления", "https://yadi.sk/i/1_HlFKa1un5TXQ"]]],
                        ],
                    },
                    {
                        title: "Для РБ",
                        type: "simple",
                        inputs: [
                            ["label", ["Напишите на почту documents@taxi.yandex.com. В письме обязательно укажите номер договора."]],
                            ["label", [["Шаблон заявления", "https://yadi.sk/i/buVbmqXVD6rSEA"]]],
                        ],
                    },
                ],
            },
            {
                title: "Смена юридического лица",
                type: "simple",
                inputs: [
                    ["label", ["Чтобы сменить юридическое лицо, перейдите в Диспетчерской в раздел «Параметры»-«Профиль партнёра»-«Параметры Диспетчерской» (доступно только директору)"]],
                    ["label", [["Перейти", "https://taxi.yandex.ru/partnership"]]],
                ],
            },
            {
                title: "Ограничение доступа таксопарка к сервису",
                type: "simple",
                inputs: [
                    ["text", "contract_number", "Номер договора", true],
                ],
            },
        ],
    },
    {
        title: "Финансы и документация",
        type: "nested",
        topics: [
            {
                title: "Запрос на приостановку выплат",
                type: "simple",
                inputs: [
                    ["text", "contract_number", "Номер договора", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", true],
                ],
            },
            {
                title: "Запрос на возобновление выплат",
                type: "simple",
                inputs: [
                    ["text", "contract_number", "Номер договора", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", true],
                ],
            },
            {
                title: "Документация/Банковские реквизиты",
                type: "nested",
                topics: [
                    {
                        title: "Вопрос по закрывающим документам",
                        type: "simple",
                        inputs: [
                            ["label", [["Общая информация", "https://fleet.yandex.ru/knowledge-base/acts.html"]]],
                            ["text", "contract_number", "Номер договора", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                    {
                        title: "Заказать акт сверки",
                        type: "simple",
                        inputs: [
                            ["label", ["Акт сверки выгружается только за закрытый период. Период считается закрытым после 13 числа месяца, следующего за отчётным."]],
                            ["text", "contract_number", "Номер договора, по которому нужен акт сверки", true],
                            ["date_period", "period", "Период, по которому нужен акт", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                    {
                        title: "Изменить банковские реквизиты",
                        type: "simple",
                        inputs: [
                            ["label", [["Шаблон заявления", "https://disk.yandex.ru/i/fRrt3DPoXxMlwQ"]]],
                            ["text", "contract_number", "Номер договора", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", true],
                        ],
                    },
                    {
                        title: "Расторжение договора",
                        type: "simple",
                        inputs: [
                            ["label", ["Чтобы заявка была обработана, подайте запрос с учётной записи директора"]],
                            ["text", "contract_number", "Номер договора", true],
                            ["date_period", "period", "Период, по которому нужен акт", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                ],
            },
        ],
    },
    {
        title: "Моментальные выплаты в Яндекс Про",
        type: "simple",
        inputs: [
            ["textarea", "message", "Текст обращения", true],
            ["file", "files", "Файлы", false],
        ],
    },
    {
        title: "Информация по выплатам сервиса",
        type: "nested",
        topics: [
            {
                title: "Перечисления на р/с",
                type: "simple",
                inputs: [
                    ["label", [["Общая информация", "https://fleet.yandex.ru/knowledge-base/transfer-of-payment.html"]]],
                    ["text", "contract_number", "Номер договора", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", true],
                ],
            },
            {
                title: "Лицевой счёт таксопарка",
                type: "simple",
                inputs: [
                    ["label", [["Общая информация", "https://fleet.yandex.ru/knowledge-base/offset-and-turn-off-threshold.html"]]],
                    ["text", "contract_number", "Номер договора", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Расхождения в сумме выплат",
                type: "simple",
                inputs: [
                    ["date_period", "period", "Период, который нужно проверить", true],
                    ["label", [["Инструкция", "https://yadi.sk/d/U-yviSxrbVIFOQ?w=1"]]],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", true],
                ],
            },
            {
                title: "Периодические списания с самозанятых водителей",
                type: "simple",
                inputs: [
                    ["label", [["Инструкция", "https://fleet.yandex.ru/knowledge-base/recurring-payments-from-self-employed.html"]]],
                    ["label", ["Если после прочтения остались вопросы, напишите нам"]],
                    ["text", "driver_license", "Номер в/у", true],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
        ],
    },
    {
        title: "Вопрос по работе Диспетчерской",
        type: "nested",
        topics: [
            {
                title: "Вопрос по функционалу и работе Диспетчерского интерфейса",
                type: "simple",
                inputs: [
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Ошибки в работе Диспетчерского интерфейса",
                type: "simple",
                inputs: [
                    ["label", [["Общие рекомендации по устранению проблемы", "https://fleet.yandex.ru/knowledge-base/#"]]],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", true],
                ],
            },
            {
                title: "Интеграции",
                type: "nested",
                topics: [
                    {
                        title: "Онлайн-кассы",
                        type: "nested",
                        topics: [
                            {
                                title: "Другие вопросы",
                                type: "simple",
                                inputs: [
                                    ["label", ["Укажите оператора кассы"]],
                                    ["textarea", "message", "Текст обращения", true],
                                    ["file", "files", "Файлы", false],
                                ],
                            },
                            {
                                title: "Как подключиться",
                                type: "simple",
                                inputs: [
                                    ["label", ["Выбрать оператора для подключения автоматического формирования и отправки чеков в Яндекс.Про (Таксометр), а также подключиться к нему можно по ", ["ссылке", "https://taxi.yandex.ru/action/onlinecashregister"]]],
                                ],
                            },
                        ],
                    },
                    {
                        title: "СМС и телефония",
                        type: "simple",
                        inputs: [
                            ["label", ["Эта опция только для собственных заказов таксопарка"]],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                    {
                        title: "API",
                        type: "nested",
                        topics: [
                            {
                                title: "Добавить API",
                                type: "simple",
                                inputs: [
                                    ["label", [["Инструкция", "https://parks.yandex/ru-ru/moskva/knowledge-base/disp/settings/api"]]],
                                ],
                            },
                            {
                                title: "Вопрос по работе API",
                                type: "simple",
                                inputs: [
                                    ["label", ["Напишите на почту api-taxi@yandex-team.ru"]],
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    },
    {
        title: "Партнёрство",
        type: "nested",
        topics: [
            {
                title: "Сертификация партнёра",
                type: "simple",
                inputs: [
                    ["label", [["Общая информация", "https://driver.yandex/certification"]]],
                    ["textarea", "message", "Текст обращения", true],
                    ["file", "files", "Файлы", false],
                ],
            },
            {
                title: "Яндекс Заправки",
                type: "nested",
                topics: [
                    {
                        title: "Неправильная заправка у водителя",
                        type: "simple",
                        inputs: [
                            ["label", ["Пожалуйста, обратитесь с этим вопросом к команде Яндекс.Заправок, заполнив ", ["форму", "https://forms.yandex.ru/surveys/10021334.63129b7c49ec1d9fb5306900c9cfeb00953966d5/"]]],
                        ],
                    },
                    {
                        title: "Как подключить",
                        type: "simple",
                        inputs: [
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                    {
                        title: "Другой вопрос",
                        type: "simple",
                        inputs: [
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                ],
            },
            {
                title: "Лизинг ТС по программе «Мой таксопарк»",
                type: "nested",
                topics: [
                    {
                        title: "Приобретение автомобилей",
                        type: "simple",
                        inputs: [
                            ["label", ["Если вас интересует приобретение автомобилей со скидкой от производителей и льготные условия лизинга, предлагаем заполнить ссылку на участие в программе «Мой Таксопарк». Форма заявки на участие и основание преимущества доступны по ", ["ссылке", "https://taxi.yandex.ru/action/proposal/mypark_development"], "."]],
                        ],
                    },
                    {
                        title: "Вопрос о программе",
                        type: "simple",
                        inputs: [
                            ["label", ["По вопросам о программе напишите на почту park@yandex-team.ru"]],
                        ],
                    },
                ],
            },
            {
                title: "Услуга по поиску и привлечению водителей",
                type: "nested",
                topics: [
                    {
                        title: "Добавить услугу",
                        type: "simple",
                        inputs: [
                            ["label", ["Чтобы участвовать в программе по поиску и привлечению водителей в ваш таксопарк, заполните ", ["форму", "https://forms.yandex.ru/surveys/10017182/"]]],
                            ["label", [["Подробная инструкция", "https://yadi.sk/d/L7V7EbVtxDq_NA"]]],
                        ],
                    },
                    {
                        title: "Вопрос о водителе",
                        type: "simple",
                        inputs: [
                            ["text", "driver_license", "Номер в/у", true],
                            ["textarea", "message", "Текст обращения", true],
                            ["file", "files", "Файлы", false],
                        ],
                    },
                    {
                        title: "Общие вопросы",
                        type: "simple",
                        inputs: [
                            ["label", ["Напишите, пожалуйста, на drivers-offer@taxi.yandex.ru"]],
                        ],
                    },
                ],
            },
        ],
    },
    {
        title: "Проверка разрешения перевозчика",
        type: "simple",
        inputs: [
            ["label", ["Приложите к обращению выписку из реестра перевозчиков ФГИС \"Такси\""]],
            ["textarea", "message", "Текст обращения", true],
            ["file", "files", "Файлы", false],
        ],
    },
];

/* Статусы обращения в том порядке, в каком их предлагает фильтр списка.
   Цвет — тот же, что у плашки в CRM: синий «В работе», зелёный «Выполнен»,
   серый «Закрыт». */
export const YA_STATUSES = [
    ['in_work', 'В работе', 'primary'],
    ['need_info', 'Требуется информация', 'warning'],
    ['done', 'Выполнен', 'success'],
    ['closed', 'Закрыт', 'secondary'],
];

export const YA_SEARCH_TYPES = [
    ['order_id', 'ID заказа'],
    ['car_number', 'Госномер'],
    ['driver_license', 'ВУ'],
    ['text', 'Текст обращения'],
];

export const YA_UNREAD = [
    ['', 'Все ответы'],
    ['1', 'Новый ответ, я не читал'],
    ['open', 'Новый ответ в открытых'],
];

/** Почта учётки CRM в Диспетчерской — учебная, CRM подставляет её сама. */
export const YA_EMAIL = 'crm@example-park.kz';

/** Темы на уровне цепочки названий; пустая цепочка — первый уровень. */
export const topicChildren = (titles = []) => {
    let nodes = YA_TOPICS;
    for (const title of titles) {
        const node = nodes.find((item) => item.title === title);
        if (!node || node.type !== 'nested') return [];
        nodes = node.topics;
    }
    return nodes;
};

/** Тема в конце цепочки или null. */
export const topicAt = (titles = []) => {
    if (!titles.length) return null;
    const parent = topicChildren(titles.slice(0, -1));
    return parent.find((item) => item.title === titles[titles.length - 1]) || null;
};

/** Поля конечной темы объектами — так их удобнее рисовать и проверять. */
export const topicInputs = (topic) => (topic && topic.type === 'simple' ? topic.inputs : [])
    .map((entry) => (entry[0] === 'label'
        ? { kind: 'label', parts: entry[1] }
        : {
            kind: entry[0], name: entry[1], title: entry[2], required: entry[3],
            hint: entry[4] || null,
        }));

/** Заполнено ли поле: период — обе даты, файлы — хотя бы один. */
const filled = (input, values, files) => {
    if (input.kind === 'file') return files.length > 0;
    const value = values[input.name];
    if (input.kind === 'date_period') return Array.isArray(value) && Boolean(value[0]) && Boolean(value[1]);
    return String(value || '').trim() !== '';
};

/**
 * Чего не хватает для «Отправить». Правило то же, что в CRM: выбрана конечная
 * тема с полями и заполнены все поля со звёздочкой. Пустой список — можно
 * отправлять.
 */
export const yaMissing = (form) => {
    const topic = topicAt(form.topics || []);
    if (!form.park) return ['Таксопарк'];
    if (!topic || topic.type !== 'simple') return ['Тема'];
    return topicInputs(topic)
        .filter((input) => input.kind !== 'label' && input.required)
        .filter((input) => !filled(input, form.values || {}, form.files || []))
        .map((input) => input.title);
};

/* Кабинеты Яндекса у парков CRM. В CRM парк называется «Департамент» или
   «Jana такси», а в Диспетчерской — «ДепТакси (Алматы)», «Jana (Алматы)»:
   кабинеты заведены по городам и названы по-своему. Поэтому — таблица, а не
   «первое слово»: у «Аманат» в одних городах кабинет «Аманат», в других —
   «Amanat». Парка нет в таблице — кабинет ищем по тому же названию. */
const YA_CABINETS = {
    'Jana такси': ['Jana', 'Jana Taxi'],
    'Департамент': ['ДепТакси'],
    'Аманат': ['Аманат', 'Amanat'],
    'Тенге Такси': ['Tenge'],
    '2Донгелек': ['2донгелек'],
    'Самозанятый': ['Самозанятый', 'Самозанятый Партнер'],
};

/* Парк формы Яндекса для парка и города водителя. Не нашёлся — пусто, и
   форма откроется на парке по умолчанию: его человек сменит сам. */
export const yaParkFor = (name, city) => {
    const crmName = String(name || '').trim();
    if (!crmName || !city) return '';
    const brands = YA_CABINETS[crmName] || [crmName];
    for (const brand of brands) {
        const park = `${brand} (${city})`;
        if (YA_PARKS.includes(park) && !YA_PARKS_CLOSED.includes(park)) return park;
    }
    return '';
};

/* В рабочей форме пустого парка не бывает: выбран первый из списка, и темы
   с почтой загружаются сразу. Сменить его на парк водителя — дело оператора. */
export const YA_DEFAULT_PARK = YA_PARKS.find((park) => !YA_PARKS_CLOSED.includes(park));

/** Пустая форма нового обращения. Парк может прийти из карточки водителя. */
export const emptyYaForm = (park = '') => ({
    park: park || YA_DEFAULT_PARK,
    restricted: false,
    topics: [],
    values: {},
    files: [],
});

/* Для фильтров списка: разделы — по алфавиту, подтемы — КОНЕЧНЫЕ темы
   раздела любой глубины, как в рабочем фильтре «Любая подтема». */
export const YA_TOPIC_TITLES = YA_TOPICS.map((topic) => topic.title).sort((a, b) => a.localeCompare(b, 'ru'));

export const topicLeaves = (title) => {
    const leaves = [];
    const walk = (nodes) => nodes.forEach((node) => {
        if (node.type === 'nested') walk(node.topics);
        else leaves.push(node.title);
    });
    const top = YA_TOPICS.find((topic) => topic.title === title);
    if (top && top.type === 'nested') walk(top.topics);
    return [...new Set(leaves)].sort((a, b) => a.localeCompare(b, 'ru'));
};

/* Учебный список обращений. Всё выдумано: тексты, ответы поддержки, авторы,
   номера заказов. Время — минуты назад от «сейчас» тренажёра, чтобы список не
   старел. */
export const YA_SAMPLE_APPEALS = [
    {
        id: 14817, topic: ['Вопрос по заказам и бонусам', 'Вопрос по оплате заказа'],
        text: 'Курьер выполнил заказ, оплата за него не поступила. Просим проверить начисление.',
        status: 'in_work', author: 'Оператор Учебный', park: 'Jana (Алматы)',
        values: { order_id: 'cargo_0000a1b2c3d4e5f6' }, createdAgo: 38, updatedAgo: 37, unread: false,
    },
    {
        id: 14806, topic: ['Вопросы об исполнителе', 'Не поступают заказы'],
        text: 'Водителю не поступают заказы, показатели в норме. Просим проверить.',
        status: 'done', author: 'Оператор Учебный', park: 'Такси 24 (Астана)',
        values: { driver_license: 'УЧ 000001' }, createdAgo: 160, updatedAgo: 41, unread: true,
        reply: 'Здравствуйте! Проверили аккаунт: ограничений нет, заказы распределяются по общей очереди. Если ситуация повторится, пришлите скриншот экрана заказов.',
    },
    {
        id: 14779, topic: ['Вопросы об исполнителе', 'Ограничение доступа к сервису'],
        text: 'Просим проверить ограничение доступа: водитель прошёл фотоконтроль, доступ не вернулся.',
        status: 'closed', author: 'Оператор Учебный', park: 'Честный (Караганда)',
        values: { driver_license: 'УЧ 000002' }, createdAgo: 1460, updatedAgo: 45, unread: false,
        reply: 'Доступ восстановлен. Если ограничение повторится, напишите в этом обращении.',
    },
    {
        id: 14751, topic: ['Вопрос по заказам и бонусам', 'Жалоба на пользователя'],
        text: 'Пассажир указал адрес, где остановка запрещена. Просим учесть при разборе заказа.',
        status: 'need_info', author: 'Оператор Учебный', park: 'iTaxi 2 (Алматы)',
        values: { order_id: '0000f1e2d3c4b5a6' }, createdAgo: 2900, updatedAgo: 52, unread: false,
        reply: 'Пришлите, пожалуйста, скриншот маршрута и время подачи — без них не сможем проверить заказ.',
    },
    {
        id: 14733, topic: ['Вопросы о ТС', 'Отсутствует категория ТС'],
        text: 'У автомобиля нет доступа к тарифу «Комфорт», хотя модель подходит. Госномер в поле.',
        status: 'closed', author: 'Оператор Учебный', park: 'Tenge (Астана)',
        values: { car_number: '000 УЧБ 01' }, createdAgo: 4400, updatedAgo: 58, unread: false,
        reply: 'Проверили классификатор: модель доступна в «Комфорте» с определённого года выпуска. Сверьте год автомобиля в карточке.',
    },
];

/** Плашка статуса: подпись и цвет. */
export const yaStatus = (code) => {
    const found = YA_STATUSES.find(([key]) => key === code);
    return found ? { label: found[1], tone: found[2] } : { label: code, tone: 'secondary' };
};
