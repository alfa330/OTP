import React from 'react';

import { Tap, TrainMark } from './screenKit';
import { ProviderMark, YpIcon, YpNavIcon } from './appIcons';
import { PROVIDERS, TRAINEE } from './scenarioYandexPro';

/* Экраны приложения Яндекс Про: путь до смены провайдера ЭДО.
 *
 * Нарисовано по настоящим кадрам за 22.06–22.08.2026. Отсюда и палитра: белый
 * фон, светло-серые карточки #f4f4f0 (на Android они темнее — #ececec), ЯРКО-
 * жёлтая кнопка #fce000 с чёрной надписью, серый второстепенный текст #90908c
 * и голубая ссылка #309ce4. Жёлтый в Про один — им же залита галочка активного
 * провайдера, и именно по ней водитель понимает, что провайдер сменился.
 *
 * Надписи дословные, вплоть до «Ваши выплаты могут быть приостановлены» и
 * правил в шторке Sapar: тренажёр готовит к настоящему экрану, а на настоящем
 * экране человек ищет знакомую строку, а не пересказ.
 */

/* ── Общие детали ─────────────────────────────────────────────────────────── */

/** Шапка раздела: стрелка назад и заголовок. Стрелка — настоящая кнопка
 *  (ловушка сценария): уйти назад посреди смены провайдера можно, и объяснить,
 *  чем это кончится, важнее, чем запретить нажатие. */
const YpHead = ({ title, tap, target, right = null }) => (
    <header className="wt-yp__head">
        <Tap id="yp_back" target={target} tap={tap} className="wt-yp__back" aria-label="Назад">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
                strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M14.5 5 8 12l6.5 7" />
            </svg>
        </Tap>
        <b>{title}</b>
        {right}
    </header>
);

const NAV = [
    ['nav_orders', 'Заказы', 'orders'],
    ['nav_intercity', 'Межгород', 'intercity'],
    ['nav_money', 'Деньги', 'money'],
    ['nav_chats', 'Чаты', 'chats'],
    ['nav_profile', 'Профиль', 'profile'],
];

const YpNav = ({ active, tap, target }) => (
    <nav className="wt-yp__nav" aria-label="Навигация Яндекс Про">
        {NAV.map(([id, label, icon]) => (
            <Tap key={id} id={id} target={target} tap={tap}
                className={active === id ? 'is-active' : ''}>
                <i aria-hidden="true"><YpNavIcon name={icon} /></i>
                {label}
            </Tap>
        ))}
    </nav>
);

/** Строка списка: значок в сером кружке, подпись, значение справа и шеврон. */
const YpRow = ({
    id, icon, label, value, tap, target, badge = 0, strong = false,
}) => (
    <Tap id={id} target={target} tap={tap} className={`wt-yp__row${strong ? ' is-strong' : ''}`}>
        {icon && <i className="wt-yp__row-ico" aria-hidden="true"><YpIcon name={icon} /></i>}
        <span>{label}</span>
        {badge > 0 && <em className="wt-yp__badge">{badge}</em>}
        {value && <u>{value}</u>}
        <svg className="wt-yp__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M9.5 5 16 12l-6.5 7" />
        </svg>
    </Tap>
);

/** Строка активного провайдера. Она же — кнопка, открывающая список: выглядит
 *  как заголовок карточки, и именно поэтому её не находят. */
const ActiveProvider = ({
    id, name, tap, target, check = false,
}) => (
    <Tap id={id} target={target} tap={tap} className="wt-yp__active">
        <i aria-hidden="true"><ProviderMark mark={name === 'Sapar' ? 'sapar' : 'paper'} /></i>
        <span>
            <b>{name}</b>
            <small>Активный провайдер</small>
        </span>
        {check ? (
            <em className="wt-yp__check" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="#111" strokeWidth="2.6"
                    strokeLinecap="round" strokeLinejoin="round">
                    <path d="m5 12.5 4.6 4.5L19 7.5" />
                </svg>
            </em>
        ) : (
            <svg className="wt-yp__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9.5 5 16 12l-6.5 7" />
            </svg>
        )}
    </Tap>
);

/* ── Новости Про: сообщение о провайдере ЭДО ──────────────────────────────
   С него всё и начинается: приложение само пишет, что выплаты могут встать.
   Картинку рисуем схемой (руль, телефон, искры) — фотографию из приложения
   сюда тащить незачем, а пустое место в сообщении читается как обрезанный
   экран. */

export const YpNews = ({ tap, target }) => (
    <div className="wt-screen wt-yp wt-yp--news">
        <TrainMark />
        <YpHead
            title="Новости Про"
            tap={tap}
            target={target}
            right={<span className="wt-yp__news-badge" aria-hidden="true"><YpIcon name="doc" /></span>}
        />
        <div className="wt-yp__pic" aria-hidden="true">
            <svg viewBox="0 0 200 132">
                <rect width="200" height="132" rx="14" fill="#f4f4f0" />
                {/* Схема, а не сцена: телефон с документом и жёлтая галочка. В
                    настоящем сообщении здесь нарисован водитель в салоне, но
                    полуфигура в 200 px читалась как ошибка рисунка, а смысл
                    сообщения — «документы в приложении», и он передаётся так. */}
                <circle cx="62" cy="56" r="30" fill="#e4ecf4" />
                <rect x="70" y="16" width="66" height="100" rx="13" fill="#3d4a58" />
                <rect x="75" y="21" width="56" height="90" rx="9" fill="#ffffff" />
                <rect x="82" y="30" width="42" height="12" rx="4" fill="#fce000" />
                <rect x="82" y="50" width="42" height="6" rx="3" fill="#dfe1e4" />
                <rect x="82" y="62" width="42" height="6" rx="3" fill="#dfe1e4" />
                <rect x="82" y="74" width="28" height="6" rx="3" fill="#dfe1e4" />
                <circle cx="128" cy="96" r="14" fill="#fce000" />
                <path d="m121 96 5 5 10-10" fill="none" stroke="#111" strokeWidth="3"
                    strokeLinecap="round" strokeLinejoin="round" />
                <path d="M164 30l3.4 8.8 8.8 3.4-8.8 3.4L164 54l-3.4-8.8-8.8-3.4 8.8-3.4Z"
                    fill="#fce000" />
            </svg>
        </div>
        <article className="wt-yp__bubble">
            <b>Ваши выплаты могут быть приостановлены</b>
            <p>
                Вы еще не выбрали провайдера ЭДО. Сделайте это прямо сейчас, чтобы своевременно
                подписывать закрывающие документы.
            </p>
            <p>
                Без подписанных документов вы не сможете получать выплаты за бонусы
                и корпоративные заказы.
            </p>
            <p>Вы можете проконсультироваться с вашим парком перед выбором провайдера</p>
            <Tap id="yp_choose_provider" target={target} tap={tap} className="wt-yp__bubble-cta">
                Выбрать провайдера
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
                    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M9.5 5 16 12l-6.5 7" />
                </svg>
            </Tap>
        </article>
        <div className="wt-yp__reactions">
            <Tap id="yp_dislike" target={target} tap={tap} aria-label="Не нравится">👎</Tap>
            <Tap id="yp_like" target={target} tap={tap} aria-label="Нравится">👍</Tap>
        </div>
        <YpNav active="nav_chats" tap={tap} target={target} />
    </div>
);

/* ── Профиль ─────────────────────────────────────────────────────────────── */

export const YpProfile = ({ tap, target }) => (
    <div className="wt-screen wt-yp wt-yp--profile">
        <TrainMark />
        <Tap id="yp_name" target={target} tap={tap} className="wt-yp__me">
            <span className="wt-yp__ava" aria-hidden="true">
                <svg viewBox="0 0 40 40">
                    <circle cx="20" cy="20" r="20" fill="#ececec" />
                    <circle cx="20" cy="16" r="6" fill="#b9b9b7" />
                    <path d="M7 36a13 13 0 0 1 26 0Z" fill="#b9b9b7" />
                </svg>
            </span>
            <span>
                <b>{TRAINEE.name} <i aria-hidden="true">›</i></b>
                <small>{TRAINEE.car}</small>
            </span>
        </Tap>
        <div className="wt-yp__group is-grey">
            <Tap id="yp_rating" target={target} tap={tap} className="wt-yp__row is-rating">
                <i className="wt-yp__row-ico is-star" aria-hidden="true"><YpIcon name="star" /></i>
                <span><b>{TRAINEE.rating}</b><small>Рейтинг</small></span>
                <svg className="wt-yp__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M9.5 5 16 12l-6.5 7" />
                </svg>
            </Tap>
        </div>
        <div className="wt-yp__group is-grey">
            <YpRow id="yp_tariffs" label="Тарифы" value="2 из 2" tap={tap} target={target} />
            <YpRow id="yp_payment" label="Оплата" value="Наличными или картой" tap={tap} target={target} />
            <YpRow id="yp_options" label="Опции для тарифов" tap={tap} target={target} />
            <YpRow id="yp_autoaccept" label="Автоприём заказов" tap={tap} target={target} />
            <YpRow id="yp_inventory" label="Инвентарь" tap={tap} target={target} />
        </div>
        <div className="wt-yp__group">
            <YpRow id="yp_diagnostics" icon="diagnostics" label="Диагностика" badge={1}
                tap={tap} target={target} />
            <YpRow id="yp_photocontrol" icon="camera" label="Фотоконтроль" tap={tap} target={target} />
        </div>
        <div className="wt-yp__group">
            <YpRow id="yp_fuel" icon="fuel" label="Яндекс Заправки" tap={tap} target={target} />
            <YpRow id="yp_benefit" icon="gift" label="Выгода с Про" tap={tap} target={target} />
            {/* Тот самый пункт. Значок папки — по нему его и находят глазами. */}
            <YpRow id="yp_legal" icon="legal" label="Юридическая документация" strong
                tap={tap} target={target} />
            <YpRow id="yp_settings" icon="settings" label="Настройки" tap={tap} target={target} />
        </div>
        <YpNav active="nav_profile" tap={tap} target={target} />
    </div>
);

/* ── Юридическая документация ────────────────────────────────────────────── */

export const YpLegal = ({ tap, target }) => (
    <div className="wt-screen wt-yp">
        <TrainMark />
        <YpHead title="Юридическая документация" tap={tap} target={target} />
        <div className="wt-yp__group">
            <YpRow id="yp_legal_docs" label="Правовые документы" tap={tap} target={target} />
            <YpRow id="yp_edo" label="Электронный документооборот" strong tap={tap} target={target} />
            <YpRow id="yp_closing_docs" label="Закрывающие документы" tap={tap} target={target} />
        </div>
    </div>
);

/* ── Электронный документооборот ─────────────────────────────────────────
   Один и тот же экран до смены и после: меняется ровно строка активного
   провайдера. Так и проверяют результат — по ней. */

export const YpEdo = ({ world, tap, target }) => (
    <div className="wt-screen wt-yp">
        <TrainMark />
        <YpHead title="Электронный документооборот" tap={tap} target={target} />
        <ActiveProvider
            id="yp_active_provider"
            name={world.activeProvider}
            tap={tap}
            target={target}
        />
        {/* Раздел месяца в приложении ПУСТОЙ — ни списка, ни надписи «документов
            нет». Выдумывать сюда плашку нельзя: человек будет искать её на
            настоящем экране. Заголовок при этом нажимается — по нему и приходит
            объяснение, чем «Документы в месяце» отличаются от выбора провайдера. */}
        <Tap id="yp_docs_month" target={target} tap={tap} className="wt-yp__section">
            Документы в {world.monthIn}
        </Tap>
        <Tap id="yp_providers_info" target={target} tap={tap} className="wt-yp__notice">
            <em aria-hidden="true">!</em>
            <span>Провайдеры ЭДО — теперь в Про</span>
            <svg className="wt-yp__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9.5 5 16 12l-6.5 7" />
            </svg>
        </Tap>
        {/* Кнопка завершения появляется только после смены: пока активен
            бумажный, урок не пройден, и заканчивать нечего. */}
        {world.switched && (
            <div className="wt-yp__done">
                <b>Активный провайдер — Sapar</b>
                <Tap id="finish" target={target} tap={tap} className="wt-yp__primary">
                    Завершить урок
                </Tap>
            </div>
        )}
    </div>
);

/* ── Экран «Провайдер» ───────────────────────────────────────────────────── */

/** Строка-карточка провайдера: белый кружок с маркой, название, шеврон. */
const ProviderRow = ({
    id, mark, name, tap, target,
}) => (
    <Tap id={id} target={target} tap={tap} className="wt-yp__prow">
        <i className="wt-yp__mark" aria-hidden="true"><ProviderMark mark={mark} /></i>
        <span>{name}</span>
        <svg className="wt-yp__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M9.5 5 16 12l-6.5 7" />
        </svg>
    </Tap>
);

/** Список провайдеров. dim — под шторкой: список остаётся видимым, но приглушён,
 *  как в приложении. */
const ProviderList = ({
    world, tap, target, dim = false,
}) => (
    <div className={`wt-yp__providers${dim ? ' is-dim' : ''}`}>
        <ActiveProvider id="yp_active_row" name={world.activeProvider} tap={tap} target={target} check />
        <h3 className="wt-yp__section">Доступные провайдеры</h3>
        <div className="wt-yp__plist">
            {PROVIDERS.filter((p) => p.name !== world.activeProvider).map((p) => (
                <ProviderRow key={p.id} id={p.id} mark={p.mark} name={p.name}
                    tap={tap} target={target} />
            ))}
            {world.activeProvider !== 'Бумажный документооборот' && (
                <ProviderRow id="pick_paper" mark="paper" name="Бумажный документооборот"
                    tap={tap} target={target} />
            )}
        </div>
    </div>
);

export const YpProviders = ({ world, tap, target }) => (
    <div className="wt-screen wt-yp">
        <TrainMark />
        <YpHead title="Провайдер" tap={tap} target={target} />
        <ProviderList world={world} tap={tap} target={target} />
    </div>
);

/* ── Шторка выбранного провайдера ────────────────────────────────────────
   Ключевой экран урока: здесь написаны оба правила, из-за которых потом пишут
   в поддержку («документы со следующего месяца», «меняется для всех
   профилей»), и здесь же кнопка, которую принимают за конец пути. */

export const YpSheet = ({ world, tap, target }) => (
    <div className="wt-yp__stage">
        <div className="wt-screen wt-yp is-behind">
            <TrainMark />
            <YpHead title="Провайдер" tap={tap} target={target} />
            <ProviderList world={world} tap={tap} target={target} dim />
        </div>
        {/* Обёртка нужна ровно для крестика: он висит НАД шторкой, а сама шторка
            прокручивается — внутри неё крестик обрезался бы. */}
        <div className="wt-yp__sheet-hold">
            <Tap id="yp_close_sheet" target={target} tap={tap} className="wt-yp__sheet-close"
                aria-label="Закрыть">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                    strokeLinecap="round" aria-hidden="true">
                    <path d="M6 6l12 12M18 6 6 18" />
                </svg>
            </Tap>
            <section className="wt-yp__sheet">
                <div className="wt-yp__sheet-head">
                    <i aria-hidden="true"><ProviderMark mark="sapar" /></i>
                    <b>Sapar</b>
                </div>
                <p>
                    При выборе нового провайдера по ЭДО документы у него вы сможете подписать
                    со следующего месяца. Провайдер поменяется для всех ваших профилей.
                </p>
                <p>Поменять провайдера можно в любой момент, кроме:</p>
                <ul>
                    <li>Последнего дня месяца с 23:30 до 0:00.</li>
                    <li>
                        1–5 числа нового месяца — только если вы уже меняли провайдера
                        в этом месяце
                    </li>
                </ul>
                <Tap id="yp_terms" target={target} tap={tap} className="wt-yp__terms">
                    <em aria-hidden="true">i</em>
                    Тарифы и условия
                    <svg className="wt-yp__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                        strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M9.5 5 16 12l-6.5 7" />
                    </svg>
                </Tap>
                <Tap id="change_provider" target={target} tap={tap} className="wt-yp__primary">
                    Сменить провайдера
                </Tap>
            </section>
        </div>
    </div>
);

/* ── Согласие на передачу данных ─────────────────────────────────────────
   Тот самый экран, на котором смену бросают недоделанной. Текст дословный:
   именно по нему водитель понимает, что подписывает согласие, а не «ещё одну
   лишнюю кнопку». */

export const YpConsent = ({ world, tap, target }) => (
    <div className="wt-yp__stage">
        <div className="wt-screen wt-yp is-behind">
            <TrainMark />
            <YpHead title="Провайдер" tap={tap} target={target} />
            <ProviderList world={world} tap={tap} target={target} dim />
        </div>
        <div className="wt-yp__sheet-hold is-full">
            <Tap id="yp_consent_back" target={target} tap={tap} className="wt-yp__sheet-back"
                aria-label="Назад">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
                    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M14.5 5 8 12l6.5 7" />
                </svg>
            </Tap>
            <section className="wt-yp__sheet is-full">
                <div className="wt-yp__consent">
                    <h2>Смена провайдера</h2>
                    <p>
                        Даю согласие на то, чтобы Яндекс организовал передачу провайдеру
                        ТОО &quot;OI GROUP&quot; (БИН: 231240006818) моих данных, включая,
                        в частности:
                    </p>
                    <ul>
                        <li>
                            мои данные как Водителя-ИП в понимании оферты на предоставление
                            информационных услуг, размещенной в сети Интернет по адресу{' '}
                            <Tap id="yp_consent_link" target={target} tap={tap} className="wt-yp__link">
                                https://clck.ru/3NAiZB
                            </Tap>,
                        </li>
                        <li>
                            данные о стоимости услуг, оказанных мною как Водителем-ИП по моему ИИН
                            в рамках Сервиса Яндекс.Такси (Сервиса Яндекс Go)
                        </li>
                    </ul>
                    <p>
                        для целей последующего заключения провайдером ЭДО САПАР
                        (ТОО &quot;OI GROUP&quot;, БИН: 231240006818) со мной договора на оказание
                        услуг по организации ЭДО (электронного документооборота) в рамках
                        заключенных договоров между мной и Яндексом.
                    </p>
                </div>
                <div className="wt-yp__consent-foot">
                    <small>
                        Нажимая «Подтвердить» вы соглашаетесь с условиями передачи данных
                        провайдеру
                    </small>
                    <Tap id="confirm" target={target} tap={tap} className="wt-yp__primary">
                        Подтвердить
                    </Tap>
                </div>
            </section>
        </div>
    </div>
);
