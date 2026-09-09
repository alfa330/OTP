import React, { useEffect, useState } from 'react';
import { BookOpen, Check, ChevronLeft, ChevronRight, Copy, EllipsisVertical, Plus, Share, SquarePlus, X } from 'lucide-react';
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
 * ОБЕ СИСТЕМЫ ПОКАЗЫВАЮТСЯ ПЕРЕКЛЮЧАТЕЛЕМ, а не только своя. Инструкцию чаще
 * всего открывают, чтобы объяснить КОЛЛЕГЕ, у которого телефон другой, —
 * супервайзер с Android показывает оператору с iPhone.
 */

const IOS_STEPS = [
    {
        title: 'Откройте портал в Safari',
        hint: 'Только Safari умеет добавлять сайты на домашний экран. Если портал открыт из Telegram или в Chrome — нажмите «Поделиться» → «Открыть в Safari».',
    },
    {
        title: 'Нажмите «Поделиться»',
        hint: 'Квадрат со стрелкой вверх в нижней панели браузера.',
        icon: Share,
    },
    {
        title: 'Пролистайте список вниз',
        hint: 'Нужный пункт лежит ниже строк «Скопировать» и «Добавить в закладки» — его не видно без прокрутки.',
    },
    {
        title: 'Выберите «На экран „Домой“»',
        hint: 'Затем нажмите «Добавить» в правом верхнем углу.',
        icon: SquarePlus,
    },
    {
        title: 'Готово',
        hint: 'Значок iCORE появится на домашнем экране. Открывайте портал с него — он запускается во весь экран.',
        icon: Check,
        done: true,
    },
];

const androidSteps = (hasInstallButton) => [
    {
        title: 'Откройте портал в Chrome',
        hint: 'В браузерах внутри Telegram и других приложений установки нет.',
    },
    hasInstallButton
        ? {
            title: 'Нажмите «Установить» внизу окна',
            hint: 'Если предпочитаете меню браузера — «три точки» справа сверху, пункт «Установить приложение».',
            icon: Plus,
        }
        : {
            title: 'Откройте меню «три точки»',
            hint: 'Справа сверху в Chrome, пункт «Установить приложение» (в старых версиях — «Добавить на главный экран»).',
            icon: EllipsisVertical,
        },
    {
        title: 'Подтвердите установку',
        hint: 'Chrome покажет своё окно с названием и значком — нажмите «Установить».',
    },
    {
        title: 'Готово',
        hint: 'Значок iCORE появится на главном экране и в списке приложений.',
        icon: Check,
        done: true,
    },
];

const BENEFITS = [
    'Открывается во весь экран, без адресной строки и поиска нужной вкладки',
    'Запускается с домашнего экрана в одно касание, как обычное приложение',
    'Переживает плохую связь: уже открытые разделы работают, а без интернета портал честно об этом говорит',
];

/* Маленький макет панели браузера: человек ищет кнопку глазами, и показать её
   на месте надёжнее, чем описать словами. Рисуется разметкой, а не картинкой —
   картинка на каждый телефон и каждую версию браузера устарела бы к следующему
   обновлению, а форма кнопки у Safari и Chrome не меняется годами. */
const SafariBarMock = () => (
    <div className="flex items-center justify-between rounded-2xl bg-slate-100 px-4 py-3 text-slate-400">
        <ChevronLeft className="h-4 w-4" aria-hidden="true" />
        <ChevronRight className="h-4 w-4 opacity-40" aria-hidden="true" />
        <span className="relative grid h-9 w-9 place-items-center rounded-full bg-white text-blue-600 shadow-sm ring-2 ring-blue-500">
            <Share className="h-4 w-4" aria-hidden="true" />
        </span>
        <BookOpen className="h-4 w-4" aria-hidden="true" />
        <Copy className="h-4 w-4" aria-hidden="true" />
    </div>
);

const ChromeBarMock = () => (
    <div className="flex items-center gap-2 rounded-2xl bg-slate-100 px-3 py-3 text-slate-400">
        <span className="h-4 w-4 shrink-0 rounded-full bg-slate-300" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate rounded-full bg-white px-3 py-1.5 text-[11.5px] text-slate-400 shadow-sm">
            alfa330.github.io/OTP
        </span>
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white text-blue-600 shadow-sm ring-2 ring-blue-500">
            <EllipsisVertical className="h-4 w-4" aria-hidden="true" />
        </span>
    </div>
);

const StepList = ({ steps }) => (
    <ol className="divide-y divide-slate-200/70 overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
        {steps.map((step, index) => {
            const Icon = step.icon;
            return (
                <li key={step.title} className="flex gap-3 px-3.5 py-3">
                    <span
                        className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11.5px] font-semibold ${
                            step.done ? 'bg-emerald-500 text-white' : 'bg-blue-600 text-white'
                        }`}
                    >
                        {step.done ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : index + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                        <p className="flex items-center gap-1.5 text-[14px] font-medium leading-snug text-slate-900">
                            {step.title}
                            {Icon && !step.done && (
                                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-slate-100 text-slate-500">
                                    <Icon className="h-3 w-3" aria-hidden="true" />
                                </span>
                            )}
                        </p>
                        <p className="mt-0.5 text-[12.5px] leading-snug text-slate-500">{step.hint}</p>
                    </div>
                </li>
            );
        })}
    </ol>
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
    const steps = tab === 'android' ? androidSteps(showInstallButton) : IOS_STEPS;

    return (
        <div className="fixed inset-0 z-[95] flex items-end justify-center sm:items-center" role="dialog" aria-modal="true" aria-labelledby="install-guide-title">
            <button
                type="button"
                className="absolute inset-0 h-full w-full cursor-default bg-slate-900/35 backdrop-blur-[2px]"
                onClick={onClose}
                aria-label="Закрыть инструкцию"
            />

            <div className="iap-guide relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-[28px] bg-white shadow-[0_-8px_40px_rgba(15,23,42,0.24)] sm:max-w-[420px] sm:rounded-[28px]">
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
                            <img src={markUrl} alt="" className="h-8 w-8" />
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

                    <div className="mt-3">{tab === 'android' ? <ChromeBarMock /> : <SafariBarMock />}</div>

                    <div className="mt-3">
                        <StepList steps={steps} />
                    </div>

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
