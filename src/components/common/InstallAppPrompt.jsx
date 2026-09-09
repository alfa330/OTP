import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Share, SquarePlus, X } from 'lucide-react';
import markUrl from './sidebar-logo-mark.svg';
import './install-app-prompt.css';
import {
    getInstallState,
    promptInstall,
    readInstallSnoozeUntil,
    shouldOfferInstall,
    snoozeInstallOffer,
    subscribeToInstallState,
} from '../../utils/pwa';

/*
 * Предложение установить портал на телефон — панель снизу экрана.
 *
 * Две ветки, потому что установка на двух системах устроена по-разному
 * (подробно — в шапке src/utils/pwa.js): на Android портал ставится НАШЕЙ
 * кнопкой через системное окно, на iPhone кнопки не существует ни у кого, и
 * единственное честное решение — показать те же два шага, что человек увидит
 * в Safari. Придумывать «кнопку установки» для iOS нельзя: она не сработает,
 * а человек решит, что портал сломан.
 *
 * ПОЯВЛЯЕТСЯ С ЗАДЕРЖКОЙ. Панель, выехавшая в первую секунду, перекрывает
 * ровно то, ради чего портал открыли, и её закрывают не читая. Пауза
 * рассчитана на то, что человек уже добрался до нужного раздела.
 *
 * ЖИВЁТ РЯДОМ С ШАРИКОМ ПОМОЩНИКА, а не внутри main-content: там на части
 * разделов висит overflow-hidden, а у вики на поддереве действует zoom, и
 * position: fixed считался бы от масштабированного предка. Слой 90 — выше
 * шарика (84) и ниже полноэкранных окон (от 100): открытая карточка задачи
 * или «Новость дня» обязаны накрывать предложение, а не спорить с ним.
 */

/* Сколько ждать перед появлением. Девять секунд — это «человек уже открыл
   раздел и что-то в нём делает», но ещё не «человек забыл, что открывал
   портал». */
const APPEAR_DELAY_MS = 9000;

/* Длительность уезда панели — ровно как в install-app-prompt.css. */
const CLOSE_ANIMATION_MS = 180;

const IOS_STEPS = [
    { icon: Share, text: 'Нажмите «Поделиться» в панели браузера' },
    { icon: SquarePlus, text: 'Выберите «На экран „Домой“»' },
];

const InstallAppPrompt = () => {
    const [install, setInstall] = useState(getInstallState);
    const [visible, setVisible] = useState(false);
    const [closing, setClosing] = useState(false);
    const [pending, setPending] = useState(false);
    /* Просьба открыть панель руками приходит счётчиком: сравниваем с прошлым
       значением, иначе первый же рендер принял бы «ноль» за просьбу. */
    const manualRequestsRef = useRef(install.manualRequests);

    useEffect(() => subscribeToInstallState(setInstall), []);

    useEffect(() => {
        if (install.manualRequests === manualRequestsRef.current) return;
        manualRequestsRef.current = install.manualRequests;
        setClosing(false);
        setVisible(true);
    }, [install.manualRequests]);

    useEffect(() => {
        if (visible || install.standalone) return undefined;
        const offer = shouldOfferInstall({
            standalone: install.standalone,
            platform: install.platform,
            canPrompt: install.canPrompt,
            snoozedUntil: readInstallSnoozeUntil(),
            now: Date.now(),
        });
        if (!offer) return undefined;
        const timer = setTimeout(() => setVisible(true), APPEAR_DELAY_MS);
        return () => clearTimeout(timer);
    }, [visible, install.standalone, install.platform, install.canPrompt]);

    /* Портал запустили с иконки — предлагать больше нечего, даже если панель
       в этот момент открыта. */
    useEffect(() => {
        if (install.standalone) setVisible(false);
    }, [install.standalone]);

    const hide = useCallback((snooze) => {
        if (snooze) snoozeInstallOffer();
        setClosing(true);
        setTimeout(() => {
            setClosing(false);
            setVisible(false);
        }, CLOSE_ANIMATION_MS);
    }, []);

    const handleInstall = useCallback(async () => {
        setPending(true);
        const outcome = await promptInstall();
        setPending(false);
        /* Отказ в системном окне откладывает предложение так же, как «Позже»:
           человек уже ответил, спрашивать его снова завтра — навязчивость. */
        hide(outcome !== 'accepted');
    }, [hide]);

    if (!visible) return null;

    const iosFlow = install.platform === 'ios' || !install.canPrompt;

    return (
        <div
            className="fixed inset-x-0 bottom-0 z-[90] flex justify-center px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pointer-events-none"
            role="dialog"
            aria-modal="false"
            aria-labelledby="install-app-title"
        >
            <div
                className={`iap-sheet pointer-events-auto w-full max-w-sm rounded-3xl bg-white/95 p-4 ring-1 ring-slate-200/70 shadow-[0_18px_45px_rgba(15,23,42,0.22)] backdrop-blur-xl ${closing ? 'iap-sheet--out' : ''}`}
            >
                <div className="flex items-start gap-3">
                    {/* Плитка знака — та же, что станет иконкой на домашнем
                        экране: человек должен узнать её среди своих значков. */}
                    <span
                        className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl shadow-sm"
                        style={{ background: 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)' }}
                        aria-hidden="true"
                    >
                        <img src={markUrl} alt="" className="h-6 w-6" />
                    </span>

                    <div className="min-w-0 flex-1">
                        <h2 id="install-app-title" className="text-[15px] font-semibold leading-snug text-slate-900">
                            Установите iCORE на телефон
                        </h2>
                        <p className="mt-1 text-[12.5px] leading-snug text-slate-500">
                            Портал будет открываться с домашнего экрана — во весь экран, без адресной строки и поиска вкладки.
                        </p>
                    </div>

                    <button
                        type="button"
                        onClick={() => hide(true)}
                        className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 active:scale-95"
                        aria-label="Закрыть"
                    >
                        <X className="h-4 w-4" aria-hidden="true" />
                    </button>
                </div>

                {iosFlow ? (
                    <>
                        <ol className="mt-3.5 space-y-2 border-t border-slate-100 pt-3.5">
                            {IOS_STEPS.map(({ icon: Icon, text }, index) => (
                                <li key={text} className="flex items-center gap-2.5">
                                    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-600">
                                        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                                    </span>
                                    <span className="text-[13px] leading-snug text-slate-700">
                                        <span className="font-semibold text-slate-400">{index + 1}.</span> {text}
                                    </span>
                                </li>
                            ))}
                        </ol>
                        <div className="mt-3.5 flex justify-end">
                            <button
                                type="button"
                                onClick={() => hide(true)}
                                className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-100 px-4 py-2.5 text-[13.5px] font-semibold text-slate-600 transition-all hover:bg-slate-200 active:scale-[0.98]"
                            >
                                Понятно
                            </button>
                        </div>
                    </>
                ) : (
                    <div className="mt-3.5 flex items-center justify-end gap-2 border-t border-slate-100 pt-3.5">
                        <button
                            type="button"
                            onClick={() => hide(true)}
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-100 px-4 py-2.5 text-[13.5px] font-semibold text-slate-600 transition-all hover:bg-slate-200 active:scale-[0.98]"
                        >
                            Позже
                        </button>
                        <button
                            type="button"
                            onClick={handleInstall}
                            disabled={pending}
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-[13.5px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                            Установить
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default InstallAppPrompt;
