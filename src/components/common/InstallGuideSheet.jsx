import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight, Copy, EllipsisVertical, Plus, Share, SquarePlus, X } from 'lucide-react';
import useIsMobileShell from './useIsMobileShell';
import useScreenBackGesture from './useScreenBackGesture';
import markUrl from './sidebar-logo-mark.svg';
import qrUrl from './install-qr.svg';

/*
 * «Подробнее» — инструкция, как поставить портал на телефон.
 *
 * Устройство повторяет инструкцию VK Видео (образец от владельца, скриншоты
 * 09.09.2026): сверху крупный значок и короткий список шагов на голубой
 * подложке, ниже — те же шаги карточками, у каждой НАРИСОВАННЫЙ КУСОК ТЕЛЕФОНА
 * с подсвеченной кнопкой, синей стрелкой и «искрами». Палитра и скругления
 * наши: slate-50, кольца slate-200, синий blue-600.
 *
 * ГЛАВНОЕ ПРАВИЛО ЭТОГО ЭКРАНА — СМОТРЕТЬ, А НЕ ЧИТАТЬ. Под каждым шагом одна
 * короткая строка: человек ищет глазами кнопку, а не разбирает абзац. Всё, что
 * не влезло в строку, показано картинкой.
 *
 * Почему у шагов есть и список, и карточки. Список читается за пять секунд и
 * годится тому, кто уже понял, куда жать; карточки нужны тому, кто не нашёл
 * кнопку. Кнопка «Подробная инструкция» просто прокручивает к ним — как в
 * образце.
 *
 * ПОЧЕМУ МАКЕТЫ РИСУЮТСЯ РАЗМЕТКОЙ, А НЕ КАРТИНКАМИ. Снимок экрана устареет с
 * ближайшим обновлением iOS или Chrome, а хранить и обновлять десяток PNG под
 * каждую версию некому. Разметка весит ноль и правится одной строкой; форма
 * кнопок у Safari и Chrome не меняется годами.
 *
 * QR-код (src/components/common/install-qr.svg, собирается
 * scripts/build_install_qr.py) — для случая, когда инструкцию смотрят не на том
 * устройстве, куда ставят: с компьютера или показывая коллеге.
 *
 * ОБЕ СИСТЕМЫ ПОКАЗЫВАЮТСЯ ПЕРЕКЛЮЧАТЕЛЕМ, а не только своя: инструкцию часто
 * открывают, чтобы показать её коллеге с другим телефоном.
 */

/* ==== Кирпичики макетов ============================================== */

/* Синяя стрелка от руки — тот же приём, что в образце: показывает, куда
   смотреть, когда кнопка на макете маленькая. */
const AccentArrow = ({ className = '' }) => (
    <svg viewBox="0 0 60 44" fill="none" className={className} aria-hidden="true">
        <path d="M4 6C18 2 40 6 49 24" stroke="#2563eb" strokeWidth="3.2" strokeLinecap="round" />
        <path d="M40 20l10 6-2 -12" stroke="#2563eb" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

/* «Искры» — три расходящихся штриха над только что нажатым пунктом. */
const Sparkles = ({ className = '' }) => (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
        <path d="M12 2.5v5.4M5 5.4l3.3 3.7M19 5.4l-3.3 3.7" stroke="#2563eb" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
);

/* Нижний край телефона: корпус обрезан верхней кромкой карточки — так это
   нарисовано в образце, и так сразу читается, что кнопка внизу экрана. */
const PhoneBottom = ({ children }) => (
    <div className="mx-auto w-[196px] rounded-b-[26px] border-[5px] border-t-0 border-slate-900 bg-white px-2 pb-2.5 pt-9">
        {children}
    </div>
);

/* Верхний край телефона — для последнего шага с домашним экраном. */
const PhoneTop = ({ children }) => (
    <div className="mx-auto w-[196px] overflow-hidden rounded-t-[26px] border-[5px] border-b-0 border-slate-900 bg-white px-2 pb-2 pt-2">
        <span className="mx-auto mb-2 block h-[9px] w-[46px] rounded-full bg-slate-900" aria-hidden="true" />
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
                упирается именно в неё на любом размере экрана. */}
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

/* Шапка меню «Поделиться»: по ней видно, что делятся именно порталом. */
const ShareSheetHeader = () => (
    <div className="flex items-center gap-2 rounded-xl bg-slate-50 p-1.5">
        <span
            className="grid h-7 w-7 shrink-0 place-items-center rounded-[8px]"
            style={{ background: 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)' }}
            aria-hidden="true"
        >
            <img src={markUrl} alt="" className="h-6 w-6" />
        </span>
        <span className="min-w-0">
            <span className="block truncate text-[9.5px] font-semibold text-slate-800">iCORE — рабочий портал</span>
            <span className="block truncate text-[8.5px] text-slate-400">alfa330.github.io</span>
        </span>
    </div>
);

/* Домашний экран: значок в лучах — так в образце показан итог установки. */
const HomeScreenIcon = () => (
    <div className="flex flex-col items-center pb-2 pt-4">
        {/* Лучи и значок лежат в ОДНОЙ коробке размером со значок: центр
            конического градиента и центр плитки — одна точка. Первая версия
            рисовала лучи в отдельной полосе сверху, и они расходились из точки
            под значком — выглядело так, будто светит что-то другое. Лучи идут
            раньше плитки по разметке, поэтому она рисуется поверх них без
            возни с z-index; лишнее обрезает корпус телефона. */}
        <span className="relative grid h-12 w-12 place-items-center">
            <span
                className="pointer-events-none absolute left-1/2 top-1/2 h-[132px] w-[132px] -translate-x-1/2 -translate-y-1/2"
                style={{
                    background: 'repeating-conic-gradient(from 0deg at 50% 50%, #dbe3ff 0deg 6deg, #ffffff 6deg 13deg)',
                    WebkitMaskImage: 'radial-gradient(circle at 50% 50%, #000 18%, transparent 70%)',
                    maskImage: 'radial-gradient(circle at 50% 50%, #000 18%, transparent 70%)',
                }}
                aria-hidden="true"
            />
            <span
                className="relative grid h-12 w-12 place-items-center rounded-[13px] shadow-[0_6px_16px_rgba(34,64,155,0.3)]"
                style={{ background: 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)' }}
                aria-hidden="true"
            >
                <img src={markUrl} alt="" className="h-[41px] w-[41px]" />
            </span>
        </span>
        <span className="relative mt-1.5 text-[9px] font-medium text-slate-600">iCORE</span>
    </div>
);

/* ==== Содержание ====================================================== */

/* Короткий список сверху — по нему ставят те, кто уже понял, куда жать. */
const IOS_SUMMARY = [
    { text: 'Откройте портал в Safari' },
    { text: 'Нажмите «Поделиться»', icon: Share },
    { text: 'Пролистайте список вниз', icon: ChevronDown },
    { text: 'Выберите «На экран „Домой“»', icon: SquarePlus },
    { text: 'Значок появится на главном экране' },
];

/* Два пути, а не один список с оговорками: когда браузер отдал событие
   установки, у нас есть своя кнопка и человеку незачем идти в меню; когда не
   отдал (Firefox, старый Chrome) — кнопки нет, и «нажмите Установить внизу»
   отправило бы искать несуществующее. */
const androidSummary = (hasInstallButton) => (hasInstallButton
    ? [
        { text: 'Откройте портал в Chrome' },
        { text: 'Нажмите «Установить» внизу окна', icon: Plus },
        { text: 'Подтвердите установку в окне Chrome' },
        { text: 'Значок появится на главном экране' },
    ]
    : [
        { text: 'Откройте портал в Chrome' },
        { text: 'Откройте меню «три точки»', icon: EllipsisVertical },
        { text: 'Выберите «Установить приложение»', icon: Plus },
        { text: 'Значок появится на главном экране' },
    ]);

const IOS_STEPS = [
    {
        text: 'Откройте портал в Safari и нажмите «Поделиться» внизу',
        art: (
            <PhoneBottom>
                <SafariBar highlight />
            </PhoneBottom>
        ),
    },
    {
        text: 'Пролистайте список вниз',
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
        text: 'Выберите пункт «На экран „Домой“»',
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
        text: 'Нажмите «Добавить» в правом верхнем углу',
        art: (
            <PhoneBottom>
                {/* Окно iOS «На экран „Домой“»: сверху строка с кнопками, ниже
                    имя будущего значка. Кнопка «Добавить» — синий текст, а не
                    плашка: так она выглядит в самой системе. */}
                <div className="rounded-xl bg-slate-50 px-2 py-1.5">
                    <div className="flex items-center justify-between gap-1.5">
                        <span className="text-[8.5px] text-slate-400">Отменить</span>
                        <span className="truncate text-[9px] font-semibold text-slate-800">На экран «Домой»</span>
                        <span className="relative shrink-0 text-[9px] font-semibold text-blue-600">
                            Добавить
                            <Sparkles className="pointer-events-none absolute -right-2 -top-4 h-5 w-5" />
                        </span>
                    </div>
                    <div className="mt-1.5 flex items-center gap-1.5 rounded-lg bg-white px-1.5 py-1">
                        <span
                            className="grid h-5 w-5 shrink-0 place-items-center rounded-[6px]"
                            style={{ background: 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)' }}
                            aria-hidden="true"
                        >
                            <img src={markUrl} alt="" className="h-[17px] w-[17px]" />
                        </span>
                        <span className="text-[9px] font-medium text-slate-700">iCORE</span>
                    </div>
                </div>
            </PhoneBottom>
        ),
    },
    {
        text: 'Готово — значок на главном экране, вам туда',
        art: (
            <PhoneTop>
                <HomeScreenIcon />
            </PhoneTop>
        ),
    },
];

const androidSteps = (hasInstallButton) => [
    hasInstallButton
        ? {
            text: 'Нажмите «Установить» внизу этого окна',
            art: (
                <PhoneBottom>
                    <div className="relative flex items-center justify-center rounded-xl bg-blue-600 px-2 py-2 text-[10px] font-semibold text-white">
                        Установить
                        <Sparkles className="pointer-events-none absolute -right-1 -top-5 h-5 w-5" />
                    </div>
                    <div className="mt-1.5 space-y-0.5">
                        <MockRow width="w-20" />
                        <MockRow width="w-14" />
                    </div>
                </PhoneBottom>
            ),
        }
        : {
            text: 'Откройте меню «три точки» в Chrome',
            art: (
                <PhoneBottom>
                    <ChromeBar highlight />
                </PhoneBottom>
            ),
        },
    {
        text: 'Выберите «Установить приложение»',
        art: (
            <PhoneBottom>
                <div className="space-y-0.5">
                    <MockRow width="w-24" />
                    <MockRow width="w-20" />
                    <MockRow label="Установить приложение" icon={Plus} active burst />
                </div>
            </PhoneBottom>
        ),
    },
    {
        text: 'Подтвердите установку в окне Chrome',
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
        text: 'Готово — значок на главном экране, вам туда',
        art: (
            <PhoneTop>
                <HomeScreenIcon />
            </PhoneTop>
        ),
    },
];

const StepCard = ({ index, text, art }) => (
    <li className="overflow-hidden rounded-[22px] bg-slate-50 ring-1 ring-slate-200/60">
        {/* Корпус телефона упирается в верхнюю кромку карточки и обрезается ею —
            так это нарисовано в образце, и так сразу читается край экрана. */}
        <div className="overflow-hidden px-4">{art}</div>
        <div className="px-4 pb-3.5 pt-3">
            <p className="text-[15px] font-semibold text-slate-900">Шаг {index}</p>
            <p className="mt-0.5 text-[13px] leading-snug text-slate-500">{text}</p>
        </div>
    </li>
);

const InstallGuideSheet = ({ open, onClose, platform = 'ios', canPrompt = false, onInstall }) => {
    /* Лист на телефоне — экран: «назад» закрывает его, а не уносит в соседний
       раздел (без своей записи жест снимал верхнюю чужую). */
    const isMobileShell = useIsMobileShell();
    useScreenBackGesture(isMobileShell && open, onClose);
    /* Своя система открывается первой, но переключатель остаётся: инструкцию
       часто показывают коллеге с другим телефоном. */
    const [tab, setTab] = useState(platform === 'android' ? 'android' : 'ios');
    /* Докручено ли до конца. Нужно ради подсказки внизу: без неё нижний край
       окна читается как конец экрана, и до карточек с шагами не доходят. */
    const [atBottom, setAtBottom] = useState(false);
    const stepsRef = useRef(null);

    const handleScroll = (event) => {
        const el = event.currentTarget;
        setAtBottom(el.scrollTop + el.clientHeight >= el.scrollHeight - 8);
    };

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

    const isAndroid = tab === 'android';
    const showInstallButton = isAndroid && canPrompt;
    const summary = isAndroid ? androidSummary(showInstallButton) : IOS_SUMMARY;
    const steps = isAndroid ? androidSteps(showInstallButton) : IOS_STEPS;

    const scrollToSteps = () => {
        if (stepsRef.current) stepsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

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

            {/* Высота задана в install-app-prompt.css через dvh, а не классом
                max-h-[92vh]: 92vh на iOS считаются от экрана БЕЗ панелей
                браузера, окно оказывалось выше видимой части, и его шапка
                («Установка на телефон» с крестиком) уезжала под адресную
                строку. */}
            <div className="iap-guide relative flex w-full flex-col overflow-hidden rounded-t-[28px] bg-white shadow-[0_-8px_40px_rgba(15,23,42,0.24)] sm:max-w-[430px] sm:rounded-[28px]">
                {/* Полоска-ручка — так на iOS выглядит окно, которое тянут снизу. */}
                <div className="flex justify-center pt-2.5 sm:hidden">
                    <span className="h-1.5 w-9 rounded-full bg-slate-300" aria-hidden="true" />
                </div>

                <div className="relative flex items-center justify-center px-14 pb-2 pt-3">
                    <h2 id="install-guide-title" className="text-[15px] font-semibold tracking-tight text-slate-500">
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

                {/* Прокручиваемый блок — САМ гибкий элемент, без обёртки:
                    обёртка с flex-1 и вложенный h-full ломали прокрутку —
                    процентная высота внутри элемента без определённой высоты
                    считается как auto, содержимое переставало помещаться и
                    просто обрезалось. */}
                <div onScroll={handleScroll} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-4">
                    {/* Шапка: значок ровно такой, каким станет на домашнем экране. */}
                    <div className="flex flex-col items-center pt-1 text-center">
                        <span
                            className="grid h-[84px] w-[84px] place-items-center rounded-[24px] shadow-[0_10px_26px_rgba(34,64,155,0.28)]"
                            style={{ background: 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)' }}
                            aria-hidden="true"
                        >
                            <img src={markUrl} alt="" className="h-[71px] w-[71px]" />
                        </span>
                        <p className="mt-3.5 text-[21px] font-semibold leading-tight tracking-tight text-slate-900">
                            Установите iCORE
                            <br />
                            на{' '}
                            <span className="underline decoration-blue-500 decoration-[3px] underline-offset-4">
                                {isAndroid ? 'Android' : 'iPhone'}
                            </span>
                        </p>
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
                                    tab === option.key ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                                }`}
                                aria-pressed={tab === option.key}
                            >
                                {option.label}
                            </button>
                        ))}
                    </div>

                    {/* Короткий список шагов на голубой подложке — как в образце. */}
                    <div className="mt-3 rounded-[22px] bg-blue-50 p-4">
                        <ol className="space-y-2.5">
                            {summary.map((item, index) => {
                                const Icon = item.icon;
                                return (
                                    <li key={item.text} className="flex items-center gap-2.5">
                                        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-white text-[11.5px] font-semibold text-blue-700">
                                            {index + 1}
                                        </span>
                                        <span className="text-[13.5px] leading-snug text-slate-800">{item.text}</span>
                                        {Icon && (
                                            <span className="grid h-[22px] w-[22px] shrink-0 place-items-center rounded-md bg-white text-slate-600">
                                                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                                            </span>
                                        )}
                                    </li>
                                );
                            })}
                        </ol>
                        {/* Единственное предупреждение, которое нельзя убрать: без
                            него человек ищет несуществующий пункт в чужом браузере. */}
                        <p className="mt-3 text-[12px] leading-snug text-slate-500">
                            {isAndroid
                                ? 'В браузере внутри Telegram установки нет — откройте портал в Chrome.'
                                : 'Пункта нет в Chrome и внутри Telegram: портал должен быть открыт в Safari.'}
                        </p>
                        <button
                            type="button"
                            onClick={scrollToSteps}
                            className="mt-3.5 h-12 w-full rounded-[14px] bg-blue-600 text-[15px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.99]"
                        >
                            Подробная инструкция
                        </button>
                    </div>

                    {/* QR — для случая, когда инструкцию смотрят не на том телефоне,
                        куда ставят: с компьютера или показывая коллеге. */}
                    <div className="mt-3 flex items-center gap-3.5 rounded-[22px] bg-slate-50 p-3.5 ring-1 ring-slate-200/60">
                        <img
                            src={qrUrl}
                            alt="QR-код на портал iCORE"
                            className="h-[104px] w-[104px] shrink-0 rounded-xl bg-white"
                        />
                        <div className="min-w-0">
                            <p className="text-[14px] font-semibold leading-snug text-slate-900">
                                Ставите на другой телефон?
                            </p>
                            <p className="mt-1 text-[12.5px] leading-snug text-slate-500">
                                Наведите на код камеру — портал откроется там, и дальше по шагам.
                            </p>
                        </div>
                    </div>

                    <h3
                        ref={stepsRef}
                        className="mt-6 scroll-mt-2 text-[21px] font-semibold leading-tight tracking-tight text-slate-900"
                    >
                        Если не нашли кнопку —
                        <br />
                        смотрите по шагам
                    </h3>

                    <ol className="mt-3 space-y-2.5">
                        {steps.map((step, index) => (
                            <StepCard key={step.text} index={index + 1} text={step.text} art={step.art} />
                        ))}
                    </ol>

                    {/* «Понятно» стоит В КОНЦЕ содержимого, а не липкой полосой
                        внизу. Липкая кнопка честно выглядела дном окна: под ней
                        не видно, что дальше есть шаги, и до них не доходили.
                        Липкой остаётся только «Установить» — это действие, ради
                        которого экран открыт, и оно должно быть под рукой. */}
                    {!showInstallButton && (
                        <button
                            type="button"
                            onClick={onClose}
                            className="mt-6 h-12 w-full rounded-[14px] bg-slate-100 text-[15px] font-semibold text-slate-700 transition-all hover:bg-slate-200 active:scale-[0.99]"
                        >
                            Понятно
                        </button>
                    )}
                </div>

                {/* Растушёвка у нижнего края: пока не докрутили, край окна не
                    должен выглядеть законченным. Отсчитывается от самого окна,
                    а не от прокручиваемого блока, и поднимается над липкой
                    кнопкой установки, когда та есть. */}
                {!atBottom && (
                    <div
                        className={`pointer-events-none absolute inset-x-0 h-12 bg-gradient-to-t from-white via-white/80 to-transparent ${
                            showInstallButton ? 'bottom-[72px]' : 'bottom-0'
                        }`}
                        aria-hidden="true"
                    />
                )}

                {showInstallButton && (
                    <div className="border-t border-slate-100 px-4 py-3 pb-[max(0.875rem,env(safe-area-inset-bottom))]">
                        <button
                            type="button"
                            onClick={onInstall}
                            className="flex h-12 w-full items-center justify-center gap-2 rounded-[14px] bg-blue-600 text-[15px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.99]"
                        >
                            <Plus className="h-4 w-4" aria-hidden="true" />
                            Установить
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default InstallGuideSheet;
