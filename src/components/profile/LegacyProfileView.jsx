import React from 'react';
import FaIcon from '../common/FaIcon';
import MobileScrollTitle from '../common/MobileScrollTitle';

/*
 * «Профиль» в прежнем виде — для всех отделов, кроме СЗоВ, ОП и Тез КЦ.
 *
 * Владелец 25.09.2026: «данные изменения должны применяться только к СЗоВ, ОП
 * и Тез КЦ». Поэтому переделка раздела (ProfileView) показывается только им, а
 * остальным (фронт-офис, Бухгалтерия, HR, Маркетинг и прочие) — эта разметка,
 * перенесённая из App.jsx дословно, какой она была до переделки: шапка слева,
 * плитки, четыре карточки, «Быстрые действия», те же подписи и прочерки.
 *
 * Отличие одно и невидимое: числа плиток приходят готовыми из того же расчёта,
 * что и у ProfileView (buildProfileStats в App.jsx), а не считаются здесь второй
 * копией — два расчёта одних и тех же часов разошлись бы на первой правке.
 */

const SkPulse = ({ className = '' }) => <div className={`sk-shimmer ${className}`} />;

const ProfilePageSkeleton = () => (
    <div className="space-y-6">
        <div className="flex flex-col sm:flex-row items-center gap-4 pb-4 sm:pb-6 border-b border-gray-200">
            <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-full sk-shimmer shrink-0" style={{ borderRadius: '9999px' }} />
            <div className="space-y-2 text-center sm:text-left w-full max-w-xs">
                <SkPulse className="h-6 w-36 mx-auto sm:mx-0" />
                <div className="flex gap-2 justify-center sm:justify-start">
                    <SkPulse className="h-5 w-20 rounded-full" />
                    <SkPulse className="h-5 w-24 rounded-full" />
                </div>
            </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4">
            {[0, 1, 2, 3].map((i) => (
                <div key={i} className="rounded-xl bg-gray-50 p-3 sm:p-4 text-center space-y-2">
                    <SkPulse className="h-8 w-16 mx-auto" />
                    <SkPulse className="h-3 w-20 mx-auto" />
                    <SkPulse className="h-3 w-20 mx-auto" />
                </div>
            ))}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-4">
            {[0, 1, 2].map((i) => (
                <div key={i} className="bg-gray-50 p-4 rounded-xl flex items-center gap-3">
                    <div className="w-10 h-10 sm:w-12 sm:h-12 sk-shimmer shrink-0" style={{ borderRadius: '9999px' }} />
                    <div className="space-y-2 flex-1 min-w-0">
                        <SkPulse className="h-3 w-20" />
                        <SkPulse className="h-4 w-28" />
                    </div>
                </div>
            ))}
        </div>
        <div className="pt-4 border-t border-gray-200 space-y-3">
            <SkPulse className="h-3 w-28" />
            <div className="flex gap-2">
                <SkPulse className="h-9 w-28 rounded-lg" />
                <SkPulse className="h-9 w-28 rounded-lg" />
            </div>
        </div>
    </div>
);

// Прочерк прежнего вида: у ProfileView «—», здесь, как было, «-».
const legacyValue = (value) => (value === '—' ? '-' : value);

// Цвет числа — как было в плитках. Балл без оценок был красным «-» (порог ниже
// 70), выполнение плана без плана — серым.
const scoreClass = (tone) => (tone === 'good' ? 'text-green-600' : tone === 'warn' ? 'text-yellow-600' : 'text-red-600');
const planClass = (tone) => (tone === 'muted' ? 'text-gray-400' : tone === 'good' ? 'text-green-600' : tone === 'warn' ? 'text-yellow-600' : 'text-red-600');
const noteClass = (tone) => (tone === 'muted' ? 'text-gray-500' : tone === 'warn' ? 'text-amber-600' : 'text-green-600');

const LegacyStats = ({ stats }) => {
    const byKey = Object.fromEntries(stats.map((stat) => [stat.key, stat]));
    const isTezOp = Boolean(byKey.successes);
    return (
        /* У ОП TEZ четыре плитки (успешки и план — их рабочие мерки),
           у остальных три: плитки плана проверок здесь больше нет. */
        <div className={`grid grid-cols-2 ${isTezOp ? 'sm:grid-cols-4' : 'sm:grid-cols-3'} gap-3 sm:gap-4`}>
            {isTezOp ? (
                <>
                    <div className="bg-gradient-to-br from-blue-50 to-blue-100 p-3 sm:p-4 rounded-xl text-center">
                        <div className="text-2xl sm:text-3xl font-bold text-blue-600 tabular-nums">{legacyValue(byKey.successes.value)}</div>
                        <div className="text-xs text-gray-600 mt-1">Успешки / план</div>
                        <div className={`text-[11px] mt-1 ${noteClass(byKey.successes.noteTone)}`}>{byKey.successes.note}</div>
                    </div>
                    <div className="bg-gradient-to-br from-green-50 to-green-100 p-3 sm:p-4 rounded-xl text-center">
                        <div className={`text-2xl sm:text-3xl font-bold tabular-nums ${planClass(byKey.plan?.tone)}`}>{legacyValue(byKey.plan?.value)}</div>
                        <div className="text-xs text-gray-600 mt-1">Выполнение плана</div>
                    </div>
                </>
            ) : (
                <div className="bg-gradient-to-br from-green-50 to-green-100 p-3 sm:p-4 rounded-xl text-center">
                    <div className={`text-2xl sm:text-3xl font-bold ${scoreClass(byKey.score?.tone)}`}>{legacyValue(byKey.score?.value)}</div>
                    <div className="text-xs text-gray-600 mt-1">Ср. балл</div>
                </div>
            )}
            <div className="bg-gradient-to-br from-purple-50 to-purple-100 p-3 sm:p-4 rounded-xl text-center">
                <div className="text-2xl sm:text-3xl font-bold text-purple-600">{legacyValue(byKey.hours?.value)}</div>
                <div className="text-xs text-gray-600 mt-1">Часов</div>
            </div>
            <div className="bg-gradient-to-br from-orange-50 to-orange-100 p-3 sm:p-4 rounded-xl text-center">
                <div className="text-2xl sm:text-3xl font-bold text-orange-600">{legacyValue(byKey.norm?.value)}</div>
                <div className="text-xs text-gray-600 mt-1">Норма</div>
            </div>
        </div>
    );
};

// Стаж — прежняя формула плитки: разница календарных месяцев.
const legacyTenure = (hireDate) => {
    if (!hireDate) return '-';
    const hire = new Date(hireDate);
    const now = new Date();
    const months = (now.getFullYear() - hire.getFullYear()) * 12 + (now.getMonth() - hire.getMonth());
    if (months < 1) return 'Меньше месяца';
    if (months < 12) return `${months} мес.`;
    const years = Math.floor(months / 12);
    const remMonths = months % 12;
    return `${years} г. ${remMonths > 0 ? remMonths + ' мес.' : ''}`;
};

export default function LegacyProfileView({
    loading,
    profile,
    isMobileShell,
    AvatarImage,
    hidesOperatorBlocks,
    stats = null,
    rateBanner = null,
    rateLabel = '',
    onChangeRate = null,
    onOpenView = null,
}) {
    const profileData = profile;
    const rateWindowOpen = typeof onChangeRate === 'function';
    return (
        <div className="bg-white p-4 sm:p-6 lg:p-8 rounded-xl shadow-md mb-8 border border-gray-200 transition-all duration-300 hover:shadow-lg">
            <h2 className="text-xl sm:text-2xl lg:text-3xl font-bold mb-4 sm:mb-6 lg:mb-8 text-gray-900 flex items-center gap-2">
                <FaIcon className="fas fa-user-circle text-blue-600"></FaIcon>
                <span className="text-blue-600">
                    Профиль
                </span>
            </h2>

            {rateBanner}

            {loading ? (
                <ProfilePageSkeleton />
            ) : profileData ? (
                <div className="space-y-6">
                    {/* Имя в шапке при прокрутке — как в Telegram. Только на
                        телефоне: на компьютере имя и так на виду, а раздел не
                        прокручивается страницей. */}
                    <MobileScrollTitle
                        active={isMobileShell}
                        title={profileData.name || ''}
                        avatarUrl={profileData.avatar_url}
                        initial={profileData.name}
                    />
                    {/* Profile header - avatar and name */}
                    <div className="flex flex-col sm:flex-row items-center gap-4 pb-4 sm:pb-6 border-b border-gray-200">
                        <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-full bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center text-white text-3xl sm:text-4xl font-bold shadow-lg overflow-hidden">
                            {profileData.avatar_url && AvatarImage ? (
                                <AvatarImage
                                    src={profileData.avatar_url}
                                    alt={profileData.name || 'avatar'}
                                    className="w-full h-full object-cover"
                                    loading="eager"
                                    fetchPriority="high"
                                />
                            ) : (
                                (profileData.name || 'U').charAt(0).toUpperCase()
                            )}
                        </div>
                        <div className="text-center sm:text-left">
                            <h3 className="text-xl sm:text-2xl font-bold text-gray-900">{profileData.name || '-'}</h3>
                            {/* Плашки роли под именем нет намеренно (решение владельца
                                26.08.2026): она печатала СЫРОЕ значение из базы. */}
                            {profileData.direction && (
                                <div className="flex flex-wrap justify-center sm:justify-start gap-2 mt-2">
                                    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
                                        <FaIcon className="fas fa-compass"></FaIcon> {profileData.direction}
                                    </span>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Stats cards - evaluation & hours summary */}
                    {!hidesOperatorBlocks && Array.isArray(stats) && stats.length > 0 && <LegacyStats stats={stats} />}

                    {/* Info cards grid */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
                        {/* Hire Date */}
                        <div className="bg-gray-50 p-4 rounded-xl shadow-sm hover:shadow-md transition flex items-center gap-3">
                            <div className="w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-green-100 flex items-center justify-center shrink-0">
                                <FaIcon className="fas fa-calendar-alt text-green-600 text-lg"></FaIcon>
                            </div>
                            <div className="min-w-0">
                                <p className="text-xs uppercase tracking-wide text-gray-500">Дата найма</p>
                                <p className="text-base sm:text-lg font-semibold text-gray-900 truncate">{profileData.hire_date || '-'}</p>
                            </div>
                        </div>

                        {/* Должность и отдел — вместо супервайзера и ставки:
                            у бэк-офиса человека определяют именно они. */}
                        {hidesOperatorBlocks && (
                            <>
                                <div className="bg-gray-50 p-4 rounded-xl shadow-sm hover:shadow-md transition flex items-center gap-3">
                                    <div className="w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-indigo-100 flex items-center justify-center shrink-0">
                                        <FaIcon className="fas fa-id-card text-indigo-600 text-lg"></FaIcon>
                                    </div>
                                    <div className="min-w-0">
                                        <p className="text-xs uppercase tracking-wide text-gray-500">Должность</p>
                                        <p className="text-base sm:text-lg font-medium text-gray-900 truncate">{profileData.job_title || '-'}</p>
                                    </div>
                                </div>
                                <div className="bg-gray-50 p-4 rounded-xl shadow-sm hover:shadow-md transition flex items-center gap-3">
                                    <div className="w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-blue-100 flex items-center justify-center shrink-0">
                                        <FaIcon className="fas fa-layer-group text-blue-600 text-lg"></FaIcon>
                                    </div>
                                    <div className="min-w-0">
                                        <p className="text-xs uppercase tracking-wide text-gray-500">Отдел</p>
                                        <p className="text-base sm:text-lg font-medium text-gray-900 truncate">{profileData.department_name || '-'}</p>
                                    </div>
                                </div>
                            </>
                        )}

                        {/* Supervisor */}
                        {!hidesOperatorBlocks && (
                            <div className="bg-gray-50 p-4 rounded-xl shadow-sm hover:shadow-md transition flex items-center gap-3">
                                <div className="w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-indigo-100 flex items-center justify-center shrink-0">
                                    <FaIcon className="fas fa-user-tie text-indigo-600 text-lg"></FaIcon>
                                </div>
                                <div className="min-w-0">
                                    <p className="text-xs uppercase tracking-wide text-gray-500">Супервайзер</p>
                                    <p className="text-base sm:text-lg font-medium text-gray-900 truncate">{profileData.supervisor_name || '-'}</p>
                                </div>
                            </div>
                        )}

                        {/* Rate */}
                        {!hidesOperatorBlocks && (
                            <div
                                className={`bg-gray-50 p-4 rounded-xl shadow-sm hover:shadow-md transition flex items-center gap-3 ${rateWindowOpen ? 'cursor-pointer ring-2 ring-blue-400 ring-offset-1' : ''}`}
                                onClick={() => { if (rateWindowOpen) onChangeRate(); }}
                            >
                                <div className="relative w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-blue-100 flex items-center justify-center shrink-0">
                                    <FaIcon className="fas fa-briefcase text-blue-600 text-lg"></FaIcon>
                                    {rateWindowOpen && (
                                        <span className="absolute -top-0.5 -right-0.5 flex h-3 w-3">
                                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                                            <span className="relative inline-flex rounded-full h-3 w-3 bg-blue-500"></span>
                                        </span>
                                    )}
                                </div>
                                <div className="min-w-0">
                                    <p className="text-xs uppercase tracking-wide text-gray-500">Ставка</p>
                                    <p className="text-base sm:text-lg font-semibold text-gray-900 truncate">
                                        {rateLabel || '-'}
                                        {rateWindowOpen && (
                                            <span className="ml-2 text-xs font-medium text-blue-600 normal-case">Изменить</span>
                                        )}
                                    </p>
                                </div>
                            </div>
                        )}

                        {/* Experience */}
                        <div className="bg-gray-50 p-4 rounded-xl shadow-sm hover:shadow-md transition flex items-center gap-3">
                            <div className="w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-yellow-100 flex items-center justify-center shrink-0">
                                <FaIcon className="fas fa-award text-yellow-600 text-lg"></FaIcon>
                            </div>
                            <div className="min-w-0">
                                <p className="text-xs uppercase tracking-wide text-gray-500">Стаж работы <span className="text-gray-400 normal-case">(приблизительно)</span></p>
                                <p className="text-base sm:text-lg font-semibold text-gray-900">{legacyTenure(profileData.hire_date)}</p>
                            </div>
                        </div>
                    </div>

                    {/* Quick actions. У бэк-офиса обе кнопки вели бы в разделы,
                        которых ему не выдали: гард видимости вернул бы его обратно
                        в профиль, и кнопка выглядела бы сломанной. */}
                    {!hidesOperatorBlocks && typeof onOpenView === 'function' && (
                        <div className="pt-4 border-t border-gray-200">
                            <p className="text-xs uppercase tracking-wide text-gray-500 mb-3">Быстрые действия</p>
                            <div className="flex flex-wrap gap-2">
                                <button
                                    onClick={() => onOpenView('hours')}
                                    className="inline-flex items-center gap-2 px-4 py-2 bg-blue-50 hover:bg-blue-100 text-blue-700 rounded-lg text-sm font-medium transition"
                                >
                                    <FaIcon className="fas fa-clock"></FaIcon>
                                    <span>Мои часы</span>
                                </button>
                                <button
                                    onClick={() => onOpenView('evaluation')}
                                    className="inline-flex items-center gap-2 px-4 py-2 bg-green-50 hover:bg-green-100 text-green-700 rounded-lg text-sm font-medium transition"
                                >
                                    <FaIcon className="fas fa-chart-bar"></FaIcon>
                                    <span>Мои оценки</span>
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            ) : (
                <p className="text-center text-gray-600 flex items-center justify-center">
                    <FaIcon className="fas fa-exclamation-circle mr-2 text-red-500"></FaIcon>
                    <span className="font-medium">Нету информации о профиле</span>
                </p>
            )}
        </div>
    );
}
