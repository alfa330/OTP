import React from 'react';
import MobileScrollTitle from '../common/MobileScrollTitle';
import './profile-mobile.css';
import { APPLE_FONT, iosCard } from '../ui/ios';
import { formatHireDate, formatTenure, toneClass } from './profileFormat';
import { ProfileEmpty, ProfileGroup, ProfileRow, ProfileSkeletonValue, ProfileValue, textSize } from './profileUi';
import FaIcon from '../common/FaIcon';
import { DeskGroup, DeskRow, IconSquare, RowSkeleton, RowValue, pillButton } from './profileDesktop';

/*
 * Раздел «Профиль» рядового сотрудника.
 *
 * Две раскладки одного содержания:
 *
 * ТЕЛЕФОН (переделка 25.09.2026, «в стиле iOS») — язык «Моих часов» и «Моих
 * смен»: шапка по оси, полоса показателей из равных ячеек, группы «Работа» и
 * «Мои данные» строками «подпись — значение» (profileUi.jsx).
 *
 * КОМПЬЮТЕР — в духе «Системных настроек» macOS: белые карточки на сером
 * полотне, цвет — в маленьких квадратных значках, числа тёмные. Раскладка на
 * трёхколоночной сетке: шапка во всю ширину (аватар, имя, «Основа · стаж»,
 * справа «Мои часы» и «Мои оценки»), три карточки показателей (у ОП TEZ
 * четыре), ниже «Работа» и «Мои данные» строками «значок — подпись — значение»:
 * «Работа» — колонка из трёх строк, «Мои данные» — две такие же колонки, и все
 * девять строк стоят на трёх общих горизонталях. Владелец 25.09.2026 отверг и
 * узкую колонку «как на телефоне», и градиентные плитки — «мультяшно».
 *
 * Чего нет намеренно ни там, ни там: плашки роли под именем (решение владельца
 * 26.08.2026 — печатала сырое «operator» латиницей) и плана проверок (#272).
 * Операторские блоки (показатели, ставка, супервайзер) у бэк-офиса скрыты — у
 * Бухгалтерии, HR и Маркетинга нет ни линии, ни этих разделов; вместо них
 * должность и отдел (hidesOperatorBlocks, departmentHidesOperatorFields).
 */

// Без белого кольца вокруг аватара: тёмный слой портала (theme-dark.css)
// ring-white не перекрашивает, и на тёмной карточке оно горело бы обводкой.
const Avatar = ({ profile, AvatarImage }) => (
    <div className="grid h-24 w-24 place-items-center overflow-hidden rounded-full bg-gradient-to-br from-blue-500 to-blue-700 text-[36px] font-semibold text-white shadow-[0_6px_20px_rgba(15,23,42,0.14)]">
        {profile?.avatar_url && AvatarImage ? (
            <AvatarImage
                src={profile.avatar_url}
                alt={profile.name || 'Фото профиля'}
                className="h-full w-full object-cover"
                loading="eager"
                fetchPriority="high"
            />
        ) : (
            (profile?.name || '?').trim().charAt(0).toUpperCase()
        )}
    </div>
);

// Полоса показателей: ячейки равной ширины. Сетка — инлайн-стилем: оболочка
// телефона перестраивает классы grid-cols-3/4 в две колонки, и полоса
// рассыпалась бы обратно в «две плюс одну».
//
// Четыре ячейки (ОП TEZ) на телефоне — квадратом 2×2: в ряд по четыре «123/160»
// упиралось в разделители уже на 375 px. Квадрат так же симметричен, а
// разделители ставятся по положению ячейки — divide-x провёл бы лишнюю линию
// у первой ячейки второго ряда.
const StatStrip = ({ stats, isMobileShell }) => {
    const columns = isMobileShell && stats.length === 4 ? 2 : stats.length;
    return (
    <div
        style={{ display: 'grid', gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        className="border-t border-slate-100"
    >
        {stats.map((stat, index) => {
            const edges = `${index % columns ? 'border-l ' : ''}${index >= columns ? 'border-t ' : ''}border-slate-100`;
            const content = (
                <>
                    <span className={`block text-[22px] font-semibold leading-7 tracking-tight tabular-nums ${toneClass(stat.tone)}`}>
                        {stat.value}
                    </span>
                    <span className="mt-0.5 block text-[12px] leading-4 text-slate-500">{stat.label}</span>
                    {stat.note && (
                        <span className={`mt-1 block text-[11px] leading-4 ${toneClass(stat.noteTone, 'text-slate-400')}`}>
                            {stat.note}
                        </span>
                    )}
                </>
            );
            const cellClass = `min-w-0 px-2 py-4 text-center ${edges}`;
            // Содержимое прижато к верху: кнопка по умолчанию центрирует его по
            // вертикали, и ячейка с третьей строкой («Осталось 59») опускала бы
            // числа соседей — ряд чисел и ряд подписей должны стоять по линейке.
            const cellStyle = { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-start' };
            return typeof stat.onClick === 'function' ? (
                <button
                    key={stat.key}
                    type="button"
                    onClick={stat.onClick}
                    style={cellStyle}
                    title={stat.hint}
                    aria-label={stat.hint ? `${stat.label}: ${stat.value}${stat.note ? `, ${stat.note}` : ''}. ${stat.hint}` : undefined}
                    className={`${cellClass} transition-colors hover:bg-slate-50 active:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/60`}
                >
                    {content}
                </button>
            ) : (
                <div key={stat.key} style={cellStyle} className={cellClass}>{content}</div>
            );
        })}
    </div>
    );
};

const HeroSkeleton = () => (
    <div className={`${iosCard} overflow-hidden`} aria-hidden="true">
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }} className="px-5 pb-6 pt-7">
            <div className="h-24 w-24 animate-pulse rounded-full bg-slate-100" />
            <div className="mt-4 h-5 w-40 animate-pulse rounded-full bg-slate-100" />
            <div className="mt-2 h-3.5 w-24 animate-pulse rounded-full bg-slate-100" />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }} className="divide-x divide-slate-100 border-t border-slate-100">
            {[0, 1, 2].map((i) => (
                <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }} className="py-4">
                    <div className="h-6 w-12 animate-pulse rounded-lg bg-slate-100" />
                    <div className="mt-2 h-3 w-16 animate-pulse rounded-full bg-slate-100" />
                </div>
            ))}
        </div>
    </div>
);

// Телефон: шапка и «Работа». Корень, баннер ставки и «Мои данные» рисует сам
// ProfileView — на постоянных местах, общих с компьютером (см. ниже).
function PhoneTop({
    firstLoad,
    failed,
    profile,
    isMobileShell,
    AvatarImage,
    hidesOperatorBlocks,
    stats = null,
    rateLabel = '',
    onChangeRate = null,
    onRetry = null,
}) {
    const hireDate = formatHireDate(profile?.hire_date);
    const tenure = formatTenure(profile?.hire_date);
    // Строка под именем: направление у линии, должность у бэк-офиса. Внизу
    // в группе «Работа» они не повторяются.
    const subtitle = hidesOperatorBlocks ? profile?.job_title : profile?.direction;
    const workRow = (label, value, props = {}) => (
        <ProfileRow key={label} label={label} {...props}>
            {firstLoad ? <ProfileSkeletonValue /> : value ? <ProfileValue>{value}</ProfileValue> : <ProfileEmpty />}
        </ProfileRow>
    );

    if (failed) {
        return (
            // section, а не div: единственный div-ребёнок корня оболочка
            // телефона «уплощает» (.main-content > div > div:only-child —
            // без скругления и тени), и карточка стала бы серой плитой.
            <section className={`${iosCard} px-5 py-10 text-center`}>
                <p className="text-[15px] font-medium text-slate-900">Не удалось загрузить профиль</p>
                <p className="mt-1 text-[13px] text-slate-500">Проверьте соединение и попробуйте ещё раз.</p>
                {typeof onRetry === 'function' && (
                    <button
                        type="button"
                        onClick={onRetry}
                        className="mt-4 rounded-xl bg-slate-100 px-4 py-2 text-[14px] font-medium text-slate-700 transition hover:bg-slate-200 active:scale-[0.98]"
                    >
                        Повторить
                    </button>
                )}
            </section>
        );
    }

    return (
        <>
            {/* Имя в шапке при прокрутке — как в Telegram. Только на
                телефоне: на компьютере имя и так на виду. */}
            {profile && (
                <MobileScrollTitle
                    active={isMobileShell}
                    title={profile.name || ''}
                    avatarUrl={profile.avatar_url}
                    initial={profile.name}
                />
            )}

            {firstLoad ? <HeroSkeleton /> : (
                <section className={`${iosCard} overflow-hidden`} aria-label="Профиль">
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }} className="px-5 pb-6 pt-7 text-center">
                        <Avatar profile={profile} AvatarImage={AvatarImage} />
                        <div role="heading" aria-level={2} className="mt-4 text-[22px] font-semibold leading-7 tracking-tight text-slate-900">
                            {profile.name || 'Без имени'}
                        </div>
                        {subtitle && <div className="mt-1 text-[14px] leading-5 text-slate-500">{subtitle}</div>}
                    </div>
                    {!hidesOperatorBlocks && Array.isArray(stats) && stats.length > 0 && <StatStrip stats={stats} isMobileShell={isMobileShell} />}
                </section>
            )}

            <ProfileGroup id="profile-work-title" title="Работа">
                {hidesOperatorBlocks ? (
                    workRow('Отдел', profile?.department_name)
                ) : (
                    <>
                        {workRow('Супервайзер', profile?.supervisor_name)}
                        {workRow('Ставка', rateLabel, typeof onChangeRate === 'function' ? {
                            // Окно смены ставки — 1-е число месяца: строка
                            // сама открывает то же окно, что и баннер сверху.
                            onClick: onChangeRate,
                            accessory: <span className={`font-medium text-blue-600 ${textSize(isMobileShell)}`}>Изменить</span>,
                        } : {})}
                    </>
                )}
                {workRow('Дата найма', hireDate)}
                {workRow('Стаж', tenure)}
            </ProfileGroup>
        </>
    );
}

// ── Компьютер ────────────────────────────────────────────────────────────────

// Показатели: значок и подпись сверху, крупное тёмное число; цветом — только
// порог (балл, выполнение плана).
const STAT_ICON = {
    score: { icon: 'fa-award', tone: 'orange' },
    successes: { icon: 'fa-check-circle', tone: 'blue' },
    plan: { icon: 'fa-flag-checkered', tone: 'green' },
    hours: { icon: 'fa-clock', tone: 'indigo' },
    norm: { icon: 'fa-chart-line', tone: 'teal' },
};
const STAT_TONE = {
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    bad: 'text-rose-600',
    muted: 'text-slate-300',
};
const NOTE_TONE = {
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    muted: 'text-slate-400',
};

const StatCards = ({ stats }) => (
    <div className={`xl:col-span-3 grid gap-6 ${stats.length === 4 ? 'grid-cols-2 xl:grid-cols-4' : 'grid-cols-3'}`}>
        {stats.map((stat) => {
            const meta = STAT_ICON[stat.key] || { icon: 'fa-chart-bar', tone: 'blue' };
            return (
                <div key={stat.key} className={`${iosCard} p-6`}>
                    <div className="flex items-center gap-3">
                        <IconSquare icon={meta.icon} tone={meta.tone} size="stat" />
                        <span className="text-[15px] font-medium text-slate-500">{stat.label}</span>
                    </div>
                    <div className={`mt-4 text-[40px] font-semibold leading-[44px] tracking-tight tabular-nums ${STAT_TONE[stat.tone] || 'text-slate-900'}`}>
                        {stat.value}
                    </div>
                    {stat.note && <div className={`mt-1.5 text-[14px] ${NOTE_TONE[stat.noteTone] || 'text-slate-400'}`}>{stat.note}</div>}
                </div>
            );
        })}
    </div>
);

const DesktopSkeleton = () => (
    <>
        <div className={`${iosCard} xl:col-span-3 flex items-center gap-6 p-7`} aria-hidden="true">
            <div className="h-24 w-24 animate-pulse rounded-full bg-slate-100" />
            <div className="space-y-3">
                <div className="h-7 w-64 animate-pulse rounded-full bg-slate-100" />
                <div className="h-4 w-44 animate-pulse rounded-full bg-slate-100" />
            </div>
        </div>
        <div className="xl:col-span-3 grid grid-cols-3 gap-6" aria-hidden="true">
            {[0, 1, 2].map((i) => <div key={i} className={`${iosCard} h-[150px] animate-pulse`} />)}
        </div>
    </>
);

// Компьютер: шапка, показатели и «Работа» — прямыми детьми сетки корня
// (фрагмент), чтобы «Мои данные», стоящие в корне следом, встали рядом с
// «Работой» в той же строке сетки.
function DesktopTop({
    firstLoad,
    failed,
    profile,
    AvatarImage,
    hidesOperatorBlocks,
    stats = null,
    rateLabel = '',
    onChangeRate = null,
    onRetry = null,
    myData = null,
    quickActions = [],
}) {
    if (firstLoad) return <DesktopSkeleton />;
    if (failed) {
        return (
            <section className={`${iosCard} xl:col-span-3 px-6 py-14 text-center`}>
                <p className="text-[18px] font-semibold text-slate-900">Не удалось загрузить профиль</p>
                <p className="mt-1.5 text-[15px] text-slate-500">Проверьте соединение и попробуйте ещё раз.</p>
                {typeof onRetry === 'function' && (
                    <button type="button" onClick={onRetry} className={`${pillButton} mt-4`}>Повторить</button>
                )}
            </section>
        );
    }

    const hireDate = formatHireDate(profile?.hire_date);
    const tenure = formatTenure(profile?.hire_date);
    const canChangeRate = typeof onChangeRate === 'function';
    // Под именем — направление (у бэк-офиса должность) и стаж: стажу не нужна
    // своя строка, а ряд «Работы» остаётся в три строки, как колонки «Моих данных».
    const subtitle = [
        hidesOperatorBlocks ? profile.job_title : profile.direction,
        tenure && (tenure === 'Меньше месяца' ? 'в компании меньше месяца' : `${tenure} в компании`),
    ].filter(Boolean).join(' · ');
    // Без «Моих данных» (стажёр) «Работа» идёт во всю ширину — три строки
    // встают в ряд, а не узкой колонкой у левого края.
    const wide = !myData;

    const workRows = hidesOperatorBlocks ? [
        { key: 'job', icon: 'fa-id-card', tone: 'indigo', label: 'Должность', value: profile.job_title },
        { key: 'dept', icon: 'fa-layer-group', tone: 'blue', label: 'Отдел', value: profile.department_name },
        { key: 'hire', icon: 'fa-calendar-alt', tone: 'red', label: 'Дата найма', value: hireDate },
    ] : [
        { key: 'sv', icon: 'fa-user-tie', tone: 'indigo', label: 'Супервайзер', value: profile.supervisor_name },
        {
            key: 'rate',
            icon: 'fa-briefcase',
            tone: 'blue',
            label: 'Ставка',
            value: rateLabel,
            // 1-е число: строка открывает то же окно смены ставки, что и баннер.
            onClick: canChangeRate ? onChangeRate : null,
            accessory: canChangeRate ? <span className="text-[15px] font-medium text-blue-600">Изменить</span> : null,
        },
        { key: 'hire', icon: 'fa-calendar-alt', tone: 'red', label: 'Дата найма', value: hireDate },
    ];

    return (
        <>
            <section className={`${iosCard} xl:col-span-3 flex flex-wrap items-center gap-6 p-7`} aria-label="Профиль">
                <div className="grid h-24 w-24 shrink-0 place-items-center overflow-hidden rounded-full bg-gradient-to-br from-blue-500 to-blue-700 text-[38px] font-semibold text-white shadow-sm">
                    {profile.avatar_url && AvatarImage ? (
                        <AvatarImage
                            src={profile.avatar_url}
                            alt={profile.name || 'Фото профиля'}
                            className="h-full w-full object-cover"
                            loading="eager"
                            fetchPriority="high"
                        />
                    ) : (
                        (profile.name || '?').trim().charAt(0).toUpperCase()
                    )}
                </div>
                <div className="min-w-0 flex-1">
                    <h2 className="truncate text-[28px] font-semibold leading-9 tracking-tight text-slate-900">{profile.name || 'Без имени'}</h2>
                    {subtitle && <p className="mt-1 text-[16px] text-slate-500">{subtitle}</p>}
                </div>
                {quickActions.length > 0 && (
                    <div className="flex flex-wrap items-center gap-3">
                        {quickActions.map((action) => (
                            <button key={action.key} type="button" onClick={action.onClick} className={pillButton}>
                                <FaIcon className={`${action.icon} ${action.tone === 'green' ? 'text-green-600' : 'text-blue-600'}`} />
                                <span>{action.label}</span>
                            </button>
                        ))}
                    </div>
                )}
            </section>

            {!hidesOperatorBlocks && Array.isArray(stats) && stats.length > 0 && <StatCards stats={stats} />}

            <DeskGroup id="profile-work-title" title="Работа" className={wide ? 'xl:col-span-3' : 'xl:col-span-1'}>
                <div className={wide ? 'grid md:grid-cols-3 md:divide-x divide-slate-100' : 'divide-y divide-slate-100'}>
                    {workRows.map((row) => (
                        <DeskRow key={row.key} icon={row.icon} tone={row.tone} label={row.label} onClick={row.onClick} accessory={row.accessory}>
                            <RowValue empty={row.key === 'hire' ? 'Не указана' : 'Не указано'}>{row.value}</RowValue>
                        </DeskRow>
                    ))}
                </div>
            </DeskGroup>
        </>
    );
}

/*
 * Корень раздела — ОДИН на обе раскладки, меняется только его класс. Баннер
 * ставки и «Мои данные» стоят в нём на постоянных местах (первое и третье),
 * поэтому React их не пересоздаёт ни при переходе окна через 768 px (телефон ↔
 * компьютер), ни при перезапросе данных: несохранённая правка «Моих данных» и
 * открытое окно смены ставки не пропадают. Скелетон — только при первой
 * загрузке, пока профиля ещё нет: общий флаг загрузки App поднимают и другие
 * действия (смена пароля), и прятать за ним уже показанный профиль незачем.
 *
 * На компьютере — вся ширина области раздела (до 1800 px) и крупный кегль:
 * узкая колонка 1150 px с 14-м кеглем на большом мониторе оставляла, по словам
 * владельца, «слишком всё мелкое, столько свободного пространства».
 * Корень — сетка в три колонки: шапка и показатели занимают всю
 * ширину, «Работа» — одну колонку, «Мои данные» (MyDataCard) — две соседние.
 * Баннер ставки (кнопка RateSelfChangeCard) растянут на всю ширину правилом
 * для прямого ребёнка-кнопки, а его старый нижний отступ снят.
 */
export default function ProfileView(props) {
    const { isMobileShell, loading, profile, rateBanner = null, myData = null } = props;
    const firstLoad = Boolean(loading && !profile);
    const failed = !loading && !profile;
    return (
        <div
            // Телефон: общий экран sa-m-root с полями 16 px и своим фоном.
            // Фон непрозрачный намеренно: у body логотип компании в центре экрана
            // (styles.css), и в зазорах между группами он просвечивал бы серым
            // пятном. Верхняя полоса берёт этот же цвет (--mobile-page-bg).
            className={isMobileShell
                ? 'sa-m-root pf-m-root min-h-screen bg-slate-100'
                : 'mx-auto grid w-full max-w-[1800px] grid-cols-1 gap-6 pb-8 xl:grid-cols-3 [&>button]:mb-0 xl:[&>button]:col-span-3'}
            style={{ fontFamily: APPLE_FONT }}
        >
            {rateBanner}
            {isMobileShell
                ? <PhoneTop {...props} firstLoad={firstLoad} failed={failed} />
                : <DesktopTop {...props} firstLoad={firstLoad} failed={failed} />}
            {failed ? null : myData}
        </div>
    );
}
