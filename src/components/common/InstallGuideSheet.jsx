import React, { useEffect, useState } from 'react';
import { Check, ChevronDown, ChevronLeft, ChevronRight, Copy, EllipsisVertical, Plus, Share, SquarePlus, X } from 'lucide-react';
import markUrl from './sidebar-logo-mark.svg';

/*
 * «Подробнее» — пошаговая инструкция установки портала на телефон.
 *
 * Зачем отдельный экран, если на панели уже написаны шаги. Панель обязана
 * читаться за две секунды, поэтому там одна строка. Но реальная установка
 * спотыкается о вещи, которые в одну строку не влезают и без которых человек
 * бросает на середине:
 *
 *   * на iPhone установка работает ТОЛЬКО из Safari — в Chrome, Telegram и
 *     любом встроенном браузере нужного пункта нет вовсе;
 *   * пункт «На экран „Домой“» лежит НИЖЕ видимой части меню «Поделиться»,
 *     и человек, не пролистав, честно сообщает, что такого пункта нет;
 *   * на Android кнопка установки живёт в меню «три точки», а называется в
 *     разных версиях Chrome по-разному.
 *
 * УСТРОЙСТВО ВЗЯТО С ИНСТРУКЦИИ VK ВИДЕО (образец от владельца): две карточки
 * с предупреждениями, крупный заголовок и дальше карточки шагов, у каждой
 * сверху НАРИСОВАННЫЙ КУСОК ТЕЛЕФОНА с подсвеченной кнопкой. Оттуда же приём с
 * синей стрелкой и «искрами» у нужного места. Палитра и скругления наши:
 * серая подложка slate-50, кольца slate-200, синий blue-600 — те же, что во
 * всех окнах портала.
 *
 * ПОЧЕМУ МАКЕТЫ РИСУЮТСЯ РАЗМЕТКОЙ, А НЕ КАРТИНКАМИ. Снимок экрана устареет с
 * ближайшим обновлением iOS или Chrome, а хранить и обновлять десяток PNG под
 * каждую версию некому. Разметка весит ноль, масштабируется на любой экран и
 * правится одной строкой; форма кнопок у Safari и Chrome не меняется годами.
 *
 * ОБЕ СИСТЕМЫ ПОКАЗЫВАЮТСЯ ПЕРЕКЛЮЧАТЕЛЕМ, а не только своя. Инструкцию чаще
 * всего открывают, чтобы объяснить КОЛЛЕГЕ, у которого телефон другой, —
 * супервайзер с Android показывает оператору с iPhone.
 */

/* ==== Кирпичики макетов ============================================== */

/* Синяя стрелка от руки — тот же приём, что в образце: она показывает, куда
   именно смотреть, когда кнопка на макете маленькая. */
const AccentArrow = ({ className = '' }) => (
    <svg viewBox="0 0 60 44" fill="none" className={className} aria-hidden="true">
        <path
            d="M4 6C18 2 40 6 49 24"
            stroke="#2563eb"
            strokeWidth="3.2"
            strokeLinecap="round"
        />
        <path
            d="M40 20l10 6-2 -12"
            stroke="#2563eb"
            strokeWidth="3.2"
            strokeLinecap="round"
            strokeLinejoin="round"
        />
    </svg>
);

/* «Искры» — три расходящихся штриха над только что нажатым пунктом; тот же
   приём, что в образце: он говорит «нажали сюда» без единого слова. */
const Sparkles = ({ className = '' }) => (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
        <path
            d="M12 2.5v5.4M5 5.4l3.3 3.7M19 5.4l-3.3 3.7"
            stroke="#2563eb"
            strokeWidth="2.6"
            strokeLinecap="round"
        />
    </svg>
);

/* Нижний край телефона: корпус обрезан верхней кромкой карточки — ровно так
   нарисовано в образце, и это честно передаёт, что кнопка внизу экрана. */
const PhoneBottom = ({ children }) => (
    <div className="mx-auto w-[196px] rounded-b-[26px] border-[5px] border-t-0 border-slate-900 bg-white px-2 pb-2.5 pt-9">
        {children}
    </div>
);

/* Верхний край телефона — для последнего шага с домашним экраном. */
const PhoneTop = ({ children }) => (
    <div className="mx-auto w-[196px] rounded-t-[26px] border-[5px] border-b-0 border-slate-900 bg-gradient-to-b from-slate-100 to-white px-2 pb-2 pt-2">
        <span className="mx-auto mb-3 block h-[9px] w-[46px] rounded-full bg-slate-900" aria-hidden="true" />
        {children}
    </div>
);

/* Строка списка. Без подписи — серая полоска-заглушка: в образце подписан
   только тот пункт, который надо нажать, и глаз идёт прямо к нему. */
const MockRow = ({ label, icon: Icon, active = false, burst = false, width = 'w-full' }) => (
    <div className={`relative flex items-center gap-2 rounded-lg px-1.5 py-1.5 ${active ? 'bg-blue-50 ring-1 ring-blue-500' : ''}`}>
        {burst && <Sparkles className="pointer-events-none absolute -right-0.5 -top-4 h-5 w-5" />}
        {Icon ? (
            <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? 'text-blue-600' : 'text-slate-300'}`} aria-hidden="true" />
        ) : (
            <span className="h-3.5 w-3.5 shrink-0 rounded-full bg-slate-200" aria-hidden="true" />
        )}
        {label ? (
            <span className="truncate text-[10px] font-semibold text-slate-800">{label}</span>
        ) : (
            <span className={`h-1.5 rounded-full bg-slate-200 ${width}`} aria-hidden="true" />
        )}
    </div>
);

const SafariBar = ({ highlight = false }) => (
    <div className="flex items-center gap-1.5">
        <ChevronLeft className="h-3.5 w-3.5 shrink-0 text-slate-300" aria-hidden="true" />
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-200" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate rounded-full bg-slate-100 px-2 py-1 text-center text-[9px] text-slate-400">
            alfa330.github.io
        </span>
        <span
            className={`relative grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                highlight ? 'bg-white text-blue-600 ring-2 ring-blue-500' : 'text-slate-300'
            }`}
        >
            <Share className="h-3.5 w-3.5" aria-hidden="true" />
            {/* Стрелка живёт у самой кнопки, а не в углу карточки: так её конец
                всегда упирается именно в неё, на любом размере экрана. */}
            {highlight && <AccentArrow className="pointer-events-none absolute -top-8 right-3 h-8 w-10" />}
        </span>
        <Copy className="h-3.5 w-3.5 shrink-0 text-slate-300" aria-hidden="true" />
    </div>
);

const ChromeBar = ({ highlight = false }) => (
    <div className="flex items-center gap-1.5">
        <span className="h-3.5 w-3.5 shrink-0 rounded-full bg-slate-200" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate rounded-full bg-slate-100 px-2 py-1 text-[9px] text-slate-400">
            alfa330.github.io/OTP
        </span>
        <span
            className={`relative grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                highlight ? 'bg-white text-blue-600 ring-2 ring-blue-500' : 'text-slate-300'
            }`}
        >
            <EllipsisVertical className="h-3.5 w-3.5" aria-hidden="true" />
            {highlight && <AccentArrow className="pointer-events-none absolute -top-8 right-3 h-8 w-10" />}
        </span>
    </div>
);

/* Карточка приложения в меню «Поделиться»: по ней человек узнаёт, что делится
   именно порталом, а не чем-то ещё. */
const ShareSheetHeader = () => (
    <div className="flex items-center gap-2 rounded-xl bg-slate-50 p-1.5">
        <span
            className="grid h-7 w-7 shrink-0 place-items-center rounded-[8px]"
            style={{ background: 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)' }}
            aria-hidden="true"
        >
            <img src={markUrl} alt="" className="h-5 w-5" />
        </span>
        <span className="min-w-0">
            <span className="block truncate text-[9.5px] font-semibold text-slate-800">iCORE — рабочий портал</span>
            <span className="block truncate text-[8.5px] text-slate-400">alfa330.github.io</span>
        </span>
    </div>
);

const HomeScreenIcon = () => (
    <div className="flex flex-col items-center pb-1 pt-2">
        <span
            className="grid h-12 w-12 place-items-center rounded-[13px] shadow-[0_6px_16px_rgba(34,64,155,0.3)]"
            style={{ background: 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)' }}
            aria-hidden="true"
        >
            <img src={markUrl} alt="" className="h-[34px] w-[34px]" />
        </span>
        <span className="mt-1.5 text-[9px] font-medium text-slate-600">iCORE</span>
    </div>
);

/* ==== Шаги ============================================================ */

const iosSteps = () => [
    {
        text: 'Откройте портал в Safari. Из Chrome и браузера внутри Telegram установить нельзя.',
        art: (
            <PhoneBottom>
                <SafariBar />
            </PhoneBottom>
        ),
    },
    {
        text: 'Нажмите «Поделиться» — квадрат со стрелкой вверх в нижней панели.',
        art: (
            <PhoneBottom>
                <SafariBar highlight />
            </PhoneBottom>
        ),
    },
    {
        text: 'Пролистайте список вниз: нужный пункт лежит ниже «Скопировать» и «В закладки».',
        art: (
            <PhoneBottom>
                <ShareSheetHeader />
                <div className="mt-1.5 space-y-0.5">
                    <MockRow width="w-24" />
                    <MockRow width="w-16" />
                    <MockRow label="Показать больше" icon={ChevronDown} active />
                </div>
            </PhoneBottom>
        ),
    },
    {
        text: 'Выберите «На экран „Домой“» и нажмите «Добавить» в правом верхнем углу.',
        art: (
            <PhoneBottom>
                <div className="space-y-0.5">
                    <MockRow width="w-24" />
                    <MockRow width="w-20" />
                    <MockRow label="На экран «Домой»" icon={SquarePlus} active burst />
                </div>
            </PhoneBottom>
        ),
    },
    {
        text: 'Готово: значок появился на домашнем экране. Запускайте портал с него — он открывается во весь экран.',
        art: (
            <PhoneTop>
                <HomeScreenIcon />
            </PhoneTop>
        ),
    },
];

const androidSteps = (hasInstallButton) => [
    {
        text: 'Откройте портал в Chrome. В браузере внутри Telegram установки нет.',
        art: (
            <PhoneBottom>
                <ChromeBar />
            </PhoneBottom>
        ),
    },
    hasInstallButton
        ? {
            text: 'Нажмите «Установить» внизу этого окна — Chrome откроет своё окно установки.',
            art: (
                <PhoneBottom>
                    <div className="flex items-center justify-center rounded-xl bg-blue-600 px-2 py-2 text-[10px] font-semibold text-white">
                        Установить
                    </div>
                    <div className="mt-1.5 space-y-0.5">
                        <MockRow width="w-20" />
                        <MockRow width="w-14" />
                    </div>
                </PhoneBottom>
            ),
        }
        : {
            text: 'Нажмите «три точки» справа сверху и выберите «Установить приложение».',
            art: (
                <PhoneBottom>
                    <ChromeBar highlight />
                    <div className="mt-1.5 space-y-0.5">
                        <MockRow width="w-20" />
                        <MockRow label="Установить приложение" icon={Plus} active />
                    </div>
                </PhoneBottom>
            ),
        },
    {
        text: 'Chrome покажет своё окно с названием и значком — подтвердите установку.',
        art: (
            <PhoneBottom>
                <ShareSheetHeader />
                <div className="mt-1.5 flex items-center justify-end gap-1">
                    <span className="rounded-full bg-slate-100 px-2 py-1 text-[9px] font-semibold text-slate-500">Отмена</span>
                    <span className="relative rounded-full bg-blue-600 px-2 py-1 text-[9px] font-semibold text-white">
                        Установить
                        <Sparkles className="pointer-events-none absolute -right-1 -top-4 h-5 w-5" />
                    </span>
                </div>
            </PhoneBottom>
        ),
    },
    {
        text: 'Готово: значок появился на главном экране и в списке приложений.',
        art: (
            <PhoneTop>
                <HomeScreenIcon />
            </PhoneTop>
        ),
    },
];

const NOTES = {
    ios: {
        plain: {
            title: 'Портала нет в App Store',
            text: 'Значок ставится прямо из браузера за минуту. Это тот же портал, только без адресной строки и на весь экран.',
        },
        accent: {
            title: 'Ставится только из Safari',
            text: 'В Chrome, Telegram и других приложениях пункта «На экран „Домой“» нет вовсе. Если портал открыт не в Safari — «Поделиться» → «Открыть в Safari».',
        },
    },
    android: {
        plain: {
            title: 'Портала нет в Google Play',
            text: 'Значок ставится прямо из браузера за минуту. Это тот же портал, только без адресной строки и на весь экран.',
        },
        accent: {
            title: 'Ставится только из Chrome',
            text: 'В браузере внутри Telegram и других приложений установки нет. Откройте портал в Chrome — адрес можно скопировать и вставить.',
        },
    },
};

const BENEFITS = [
    'Открывается во весь экран, без адресной строки и поиска нужной вкладки',
    'Запускается с домашнего экрана в одно касание, как обычное приложение',
    'Переживает плохую связь: без интернета портал честно об этом говорит, а не показывает страницу браузера',
];

const StepCard = ({ index, text, art }) => (
    <li className="overflow-hidden rounded-[22px] bg-slate-50 ring-1 ring-slate-200/60">
        {/* Корпус телефона нарочно упирается в верхнюю кромку карточки и
            обрезается ею — так это нарисовано в образце, и так сразу читается,
            что перед тобой край экрана, а не отдельная картинка. */}
        <div className="overflow-hidden px-4">{art}</div>
        <div className="px-4 pb-3.5 pt-3">
            <p className="text-[14px] font-semibold text-slate-900">Шаг {index}</p>
            <p className="mt-1 text-[12.5px] leading-snug text-slate-500">{text}</p>
        </div>
    </li>
);

const InstallGuideSheet = ({ open, onClose, platform = 'ios', canPrompt = false, onInstall }) => {
    /* Своя система открывается первой, но переключатель остаётся: инструкцию
       часто показывают коллеге с другим телефоном. */
    const [tab, setTab] = useState(platform === 'android' ? 'android' : 'ios');

    useEffect(() => {
        if (open) setTab(platform === 'android' ? 'android' : 'ios');
    }, [open, platform]);

    useEffect(() => {
        if (!open) return undefined;
        const onKeyDown = (event) => {
            if (event.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', onKeyDown);
        /* Прокрутку страницы под окном запираем: на телефоне жест по инструкции
           иначе уезжает в раздел под ней. */
        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            window.removeEventListener('keydown', onKeyDown);
            document.body.style.overflow = previousOverflow;
        };
    }, [open, onClose]);

    if (!open) return null;

    const showInstallButton = tab === 'android' && canPrompt;
    const steps = tab === 'android' ? androidSteps(showInstallButton) : iosSteps();
    const notes = NOTES[tab === 'android' ? 'android' : 'ios'];

    return (
        <div
            className="fixed inset-0 z-[95] flex items-end justify-center sm:items-center"
            role="dialog"
            aria-modal="true"
            aria-labelledby="install-guide-title"
        >
            <button
                type="button"
                className="absolute inset-0 h-full w-full cursor-default bg-slate-900/35 backdrop-blur-[2px]"
                onClick={onClose}
                aria-label="Закрыть инструкцию"
            />

            <div className="iap-guide relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-[28px] bg-white shadow-[0_-8px_40px_rgba(15,23,42,0.24)] sm:max-w-[430px] sm:rounded-[28px]">
                {/* Полоска-ручка — так на iOS выглядит окно, которое тянут снизу. */}
                <div className="flex justify-center pt-2.5 sm:hidden">
                    <span className="h-1.5 w-9 rounded-full bg-slate-300" aria-hidden="true" />
                </div>

                <div className="relative flex items-center justify-center px-14 pb-3 pt-3">
                    <h2 id="install-guide-title" className="text-[16px] font-semibold tracking-tight text-slate-900">
                        Установка на телефон
                    </h2>
                    <button
                        type="button"
                        onClick={onClose}
                        className="absolute right-3 top-2.5 grid h-8 w-8 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 active:scale-95"
                        aria-label="Закрыть"
                    >
                        <X className="h-4 w-4" aria-hidden="true" />
                    </button>
                </div>

                <div className="flex-1 overflow-y-auto overscroll-contain px-4 pb-4">
                    <div className="flex items-center gap-3 rounded-2xl bg-slate-50 p-3.5 ring-1 ring-slate-200/70">
                        <span
                            className="grid h-14 w-14 shrink-0 place-items-center rounded-[18px] shadow-sm"
                            style={{ background: 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)' }}
                            aria-hidden="true"
                        >
                            <img src={markUrl} alt="" className="h-9 w-9" />
                        </span>
                        <div className="min-w-0">
                            <p className="text-[15px] font-semibold leading-tight text-slate-900">iCORE</p>
                            <p className="mt-0.5 text-[12.5px] leading-snug text-slate-500">
                                Так значок будет выглядеть на главном экране
                            </p>
                        </div>
                    </div>

                    {/* Переключатель систем — как сегменты в «Настройках» iOS. */}
                    <div className="mt-4 grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1">
                        {[
                            { key: 'ios', label: 'iPhone' },
                            { key: 'android', label: 'Android' },
                        ].map((option) => (
                            <button
                                key={option.key}
                                type="button"
                                onClick={() => setTab(option.key)}
                                className={`rounded-[9px] py-1.5 text-[13px] font-semibold transition ${
                                    tab === option.key
                                        ? 'bg-white text-slate-900 shadow-sm'
                                        : 'text-slate-500 hover:text-slate-700'
                                }`}
                                aria-pressed={tab === option.key}
                            >
                                {option.label}
                            </button>
                        ))}
                    </div>

                    {/* Две заметки перед шагами — как в образце: серая объясняет,
                        почему приложения нет в магазине, синяя предупреждает о
                        единственном браузере, из которого установка работает. */}
                    <div className="mt-3 space-y-2">
                        <div className="rounded-[18px] bg-slate-50 p-3.5 ring-1 ring-slate-200/60">
                            <p className="text-[13.5px] font-semibold leading-snug text-slate-900">{notes.plain.title}</p>
                            <p className="mt-1 text-[12.5px] leading-snug text-slate-500">{notes.plain.text}</p>
                        </div>
                        <div className="rounded-[18px] bg-blue-50 p-3.5 ring-1 ring-blue-200/70">
                            <p className="text-[13.5px] font-semibold leading-snug text-slate-900">{notes.accent.title}</p>
                            <p className="mt-1 text-[12.5px] leading-snug text-slate-600">{notes.accent.text}</p>
                        </div>
                    </div>

                    <h3 className="mt-5 text-[20px] font-semibold leading-tight tracking-tight text-slate-900">
                        Как поставить значок
                    </h3>

                    <ol className="mt-3 space-y-2.5">
                        {steps.map((step, index) => (
                            <StepCard key={step.text} index={index + 1} text={step.text} art={step.art} />
                        ))}
                    </ol>

                    <p className="mt-5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        Что это даёт
                    </p>
                    <ul className="mt-2 space-y-2">
                        {BENEFITS.map((text) => (
                            <li key={text} className="flex gap-2.5 text-[12.5px] leading-snug text-slate-600">
                                <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-emerald-100 text-emerald-600">
                                    <Check className="h-2.5 w-2.5" aria-hidden="true" />
                                </span>
                                {text}
                            </li>
                        ))}
                    </ul>
                </div>

                <div className="border-t border-slate-100 px-4 py-3 pb-[max(0.875rem,env(safe-area-inset-bottom))]">
                    {showInstallButton ? (
                        <button
                            type="button"
                            onClick={onInstall}
                            className="flex h-12 w-full items-center justify-center gap-2 rounded-[14px] bg-blue-600 text-[15px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.99]"
                        >
                            <Plus className="h-4 w-4" aria-hidden="true" />
                            Установить
                        </button>
                    ) : (
                        <button
                            type="button"
                            onClick={onClose}
                            className="h-12 w-full rounded-[14px] bg-slate-100 text-[15px] font-semibold text-slate-700 transition-all hover:bg-slate-200 active:scale-[0.99]"
                        >
                            Понятно
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
};

export default InstallGuideSheet;
