import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Plus, Share, SquarePlus, X } from 'lucide-react';
import markUrl from './sidebar-logo-mark.svg';
import InstallGuideSheet from './InstallGuideSheet';
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
 * Устройство панели повторяет то, как это делают приложения, которыми люди
 * пользуются каждый день (образец — предложение VK Видео): крупный значок
 * приложения, короткий заголовок в две строки, ОДНА строка шагов и широкая
 * кнопка «Подробнее». Порядок именно такой, потому что читают панель сверху
 * вниз и решение принимают по первым двум строкам; кто не понял с первого
 * раза — уходит в подробную инструкцию, а не разбирается в мелком тексте.
 *
 * Две ветки, потому что установка на двух системах устроена по-разному
 * (подробно — в шапке src/utils/pwa.js): на Android портал ставится НАШЕЙ
 * кнопкой через системное окно, на iPhone кнопки не существует ни у кого, и
 * единственное честное решение — показать те же шаги, что человек увидит в
 * Safari. Придумывать «кнопку установки» для iOS нельзя: она не сработает, а
 * человек решит, что портал сломан.
 *
 * ПОЯВЛЯЕТСЯ С ЗАДЕРЖКОЙ. Панель, выехавшая в первую секунду, перекрывает
 * ровно то, ради чего портал открыли, и её закрывают не читая. Пауза
 * рассчитана на то, что человек уже добрался до нужного экрана.
 *
 * ЖИВЁТ РЯДОМ С ШАРИКОМ ПОМОЩНИКА, а не внутри main-content: там на части
 * разделов висит overflow-hidden, а у вики на поддереве действует zoom, и
 * position: fixed считался бы от масштабированного предка. Слой 90 — выше
 * шарика (84) и ниже полноэкранных окон (от 100): открытая карточка задачи
 * или «Новость дня» обязаны накрывать предложение, а не спорить с ним.
 *
 * ВЫСОТА ПАНЕЛИ ОТДАЁТСЯ СТРАНИЦЕ переменной --install-offer-height. Экран
 * входа центрирован по вертикали, и панель накрыла бы кнопку «Войти»; зная
 * высоту, экран поднимает форму ровно настолько, насколько нужно.
 */

/* Сколько ждать перед появлением. Девять секунд — это «человек уже открыл
   раздел и что-то в нём делает», но ещё не «человек забыл, что открывал
   портал». */
const APPEAR_DELAY_MS = 9000;

/* Длительность уезда панели — ровно как в install-app-prompt.css. */
const CLOSE_ANIMATION_MS = 180;

const OFFER_BODY_CLASS = 'has-install-offer';
const OFFER_HEIGHT_VAR = '--install-offer-height';

const InstallAppPrompt = () => {
    const [install, setInstall] = useState(getInstallState);
    const [visible, setVisible] = useState(false);
    const [closing, setClosing] = useState(false);
    const [pending, setPending] = useState(false);
    const [guideOpen, setGuideOpen] = useState(false);
    const sheetRef = useRef(null);
    /* Просьбы открыть панель руками приходят счётчиками: сравниваем с прошлым
       значением, иначе первый же рендер принял бы «ноль» за просьбу. */
    const manualRequestsRef = useRef(install.manualRequests);
    const manualGuideRef = useRef(install.manualGuideRequests);

    useEffect(() => subscribeToInstallState(setInstall), []);

    useEffect(() => {
        if (install.manualRequests === manualRequestsRef.current) return;
        manualRequestsRef.current = install.manualRequests;
        setClosing(false);
        setVisible(true);
    }, [install.manualRequests]);

    useEffect(() => {
        if (install.manualGuideRequests === manualGuideRef.current) return;
        manualGuideRef.current = install.manualGuideRequests;
        setGuideOpen(true);
    }, [install.manualGuideRequests]);

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
        if (install.standalone) {
            setVisible(false);
            setGuideOpen(false);
        }
    }, [install.standalone]);

    /* Пока панель на экране, страница знает её высоту и своё место под ней.
       Замер живой (ResizeObserver): у панели разный текст на разных системах,
       а на узком экране заголовок переносится в три строки. */
    useLayoutEffect(() => {
        const root = typeof document === 'undefined' ? null : document.documentElement;
        const body = typeof document === 'undefined' ? null : document.body;
        if (!root || !body) return undefined;
        if (!visible || closing) {
            body.classList.remove(OFFER_BODY_CLASS);
            root.style.removeProperty(OFFER_HEIGHT_VAR);
            return undefined;
        }
        body.classList.add(OFFER_BODY_CLASS);
        const measure = () => {
            const height = sheetRef.current ? sheetRef.current.offsetHeight : 0;
            if (height) root.style.setProperty(OFFER_HEIGHT_VAR, height + 'px');
        };
        measure();
        let observer = null;
        if (typeof ResizeObserver === 'function' && sheetRef.current) {
            observer = new ResizeObserver(measure);
            observer.observe(sheetRef.current);
        }
        return () => {
            if (observer) observer.disconnect();
            body.classList.remove(OFFER_BODY_CLASS);
            root.style.removeProperty(OFFER_HEIGHT_VAR);
        };
    }, [visible, closing]);

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
        setGuideOpen(false);
        /* Отказ в системном окне откладывает предложение так же, как «Позже»:
           человек уже ответил, спрашивать его снова завтра — навязчивость. */
        hide(outcome !== 'accepted');
    }, [hide]);

    const guide = (
        <InstallGuideSheet
            open={guideOpen}
            onClose={() => setGuideOpen(false)}
            platform={install.platform}
            canPrompt={install.canPrompt}
            onInstall={handleInstall}
        />
    );

    /* Кнопка «Установить» рисуется только там, где ей есть что открыть.
       На iPhone и в браузерах без события установки ведём в инструкцию. */
    const canInstallHere = install.platform !== 'ios' && install.canPrompt;

    /* Панель и инструкция всегда стоят в дереве рядом, а не «или/или»:
       инструкция открыта и после того, как панель уехала, и её собственное
       состояние не должно сбрасываться от исчезновения соседа. */
    return (
        <>
            {visible && (
            <div
                className="pointer-events-none fixed inset-x-0 bottom-0 z-[90] flex justify-center px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
                role="dialog"
                aria-modal="false"
                aria-labelledby="install-app-title"
            >
                <div
                    ref={sheetRef}
                    className={`iap-sheet pointer-events-auto relative w-full max-w-[420px] rounded-[28px] bg-white/95 px-5 pb-4 pt-5 text-center shadow-[0_18px_50px_rgba(15,23,42,0.22)] ring-1 ring-slate-900/5 backdrop-blur-xl ${closing ? 'iap-sheet--out' : ''}`}
                >
                    <button
                        type="button"
                        onClick={() => hide(true)}
                        className="absolute right-3.5 top-3.5 grid h-8 w-8 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 active:scale-95"
                        aria-label="Закрыть"
                    >
                        <X className="h-4 w-4" aria-hidden="true" />
                    </button>

                    {/* Значок — тот самый, что появится на домашнем экране:
                        человек должен узнать его среди своих приложений. */}
                    <span
                        className="iap-sheet__icon mx-auto grid h-[76px] w-[76px] place-items-center rounded-[22px] shadow-[0_8px_22px_rgba(34,64,155,0.28)]"
                        style={{ background: 'linear-gradient(150deg, #22409B 0%, #4A3A96 45%, #7B2E92 100%)' }}
                        aria-hidden="true"
                    >
                        <img src={markUrl} alt="" className="iap-sheet__mark h-[42px] w-[42px]" />
                    </span>

                    <h2
                        id="install-app-title"
                        className="iap-sheet__title mt-3.5 text-[19px] font-semibold leading-tight tracking-tight text-slate-900"
                    >
                        Установите iCORE
                        <br />
                        на главный экран
                    </h2>

                    {canInstallHere ? (
                        <p className="iap-sheet__text mx-auto mt-2 max-w-[300px] text-[13.5px] leading-snug text-slate-500">
                            Портал откроется во весь экран, без адресной строки — как обычное приложение.
                        </p>
                    ) : (
                        <p className="iap-sheet__text mx-auto mt-2 max-w-[320px] text-[13.5px] leading-[1.5] text-slate-500">
                            Нажмите «Поделиться»
                            <span className="mx-1 inline-grid h-[22px] w-[22px] translate-y-[5px] place-items-center rounded-md bg-slate-100 text-slate-600">
                                <Share className="h-3.5 w-3.5" aria-hidden="true" />
                            </span>
                            → Пролистайте вниз → Добавить на экран «Домой»
                            <span className="mx-1 inline-grid h-[22px] w-[22px] translate-y-[5px] place-items-center rounded-md bg-slate-100 text-slate-600">
                                <SquarePlus className="h-3.5 w-3.5" aria-hidden="true" />
                            </span>
                        </p>
                    )}

                    <div className="iap-sheet__actions mt-4 space-y-2">
                        {canInstallHere && (
                            <button
                                type="button"
                                onClick={handleInstall}
                                disabled={pending}
                                className="flex h-12 w-full items-center justify-center gap-2 rounded-[14px] bg-blue-600 text-[15px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60"
                            >
                                <Plus className="h-4 w-4" aria-hidden="true" />
                                Установить
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={() => setGuideOpen(true)}
                            className={
                                canInstallHere
                                    ? 'h-12 w-full rounded-[14px] bg-slate-100 text-[15px] font-semibold text-slate-700 transition-all hover:bg-slate-200 active:scale-[0.99]'
                                    : 'h-12 w-full rounded-[14px] bg-blue-600 text-[15px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.99]'
                            }
                        >
                            Подробнее
                        </button>
                    </div>
                </div>
            </div>
            )}
            {guide}
        </>
    );
};

export default InstallAppPrompt;
