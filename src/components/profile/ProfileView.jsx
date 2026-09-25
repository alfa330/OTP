import React from 'react';
import MobileScrollTitle from '../common/MobileScrollTitle';
import './profile-mobile.css';
import { APPLE_FONT, iosCard } from '../ui/ios';
import { formatHireDate, formatTenure, toneClass } from './profileFormat';
import { ProfileEmpty, ProfileGroup, ProfileRow, ProfileSkeletonValue, ProfileValue, textSize } from './profileUi';

/*
 * Раздел «Профиль» рядового сотрудника (переделка 25.09.2026, просьба владельца:
 * «гармонично, с симметрией, аккуратно и удобно в стиле iOS/macOS»).
 *
 * Одна колонка по центру, как «Настройки» macOS и «Apple ID» на iPhone:
 *   1. Карточка-шапка: аватар, имя и строка под ним — всё по оси симметрии.
 *      Внизу той же карточки — полоса показателей из РАВНЫХ ячеек с тонкими
 *      разделителями. Три показателя или четыре — полоса всегда ровная; прежняя
 *      сетка раскладывала три плитки «две сверху, одна снизу».
 *   2. Группы «Работа» и «Мои данные» — одного вида (profileUi.jsx): одна
 *      высота строки, одна колонка подписей, одна линия значений.
 *
 * Чего нет намеренно:
 *   - плашки роли под именем (решение владельца 26.08.2026 — печатала сырое
 *     «operator» латиницей);
 *   - отдельных кнопок «Мои часы» / «Мои оценки»: они повторяли пункты меню и
 *     нижнего бара. Теперь в свой раздел ведёт сама ячейка показателя —
 *     балл в «Мои оценки», часы и норма в «Мои часы», как сводки в iOS;
 *   - цвета без смысла: число окрашено, только когда порог достигнут или нет.
 *
 * Операторские блоки (показатели, ставка, супервайзер) у бэк-офиса скрыты —
 * у Бухгалтерии, HR и Маркетинга нет ни линии, ни этих разделов; вместо них
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

export default function ProfileView({
    loading,
    profile,
    isMobileShell,
    AvatarImage,
    hidesOperatorBlocks,
    stats = null,
    rateBanner = null,
    rateLabel = '',
    onChangeRate = null,
    myData = null,
    onRetry = null,
}) {
    const hireDate = formatHireDate(profile?.hire_date);
    const tenure = formatTenure(profile?.hire_date);
    // Строка под именем: направление у линии, должность у бэк-офиса. Внизу
    // в группе «Работа» они не повторяются.
    const subtitle = hidesOperatorBlocks ? profile?.job_title : profile?.direction;
    const workRow = (label, value, props = {}) => (
        <ProfileRow key={label} label={label} {...props}>
            {loading ? <ProfileSkeletonValue /> : value ? <ProfileValue>{value}</ProfileValue> : <ProfileEmpty />}
        </ProfileRow>
    );

    return (
        // Телефон: корень — общий экран sa-m-root с полями 16 px и своим фоном.
        // Фон непрозрачный намеренно: у body логотип компании в центре экрана
        // (styles.css), и в зазорах между группами он просвечивал бы серым
        // пятном. Верхняя полоса берёт этот же цвет (--mobile-page-bg).
        // Компьютер: одна колонка по центру, как «Настройки» macOS.
        <div
            className={isMobileShell ? 'sa-m-root pf-m-root min-h-screen bg-slate-100' : 'mx-auto w-full max-w-2xl space-y-6 pb-6'}
            style={{ fontFamily: APPLE_FONT }}
        >
            {rateBanner}

            {!loading && !profile ? (
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
            ) : (
                <>
                    {/* Имя в шапке при прокрутке — как в Telegram. Только на
                        телефоне: на компьютере имя и так на виду. */}
                    {!loading && (
                        <MobileScrollTitle
                            active={isMobileShell}
                            title={profile.name || ''}
                            avatarUrl={profile.avatar_url}
                            initial={profile.name}
                        />
                    )}

                    {loading ? <HeroSkeleton /> : (
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

                    {myData}
                </>
            )}
        </div>
    );
}
