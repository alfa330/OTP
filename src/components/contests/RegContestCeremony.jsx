import React, { useEffect, useMemo, useRef, useState } from 'react';
import { APPLE_FONT, iosCard } from '../ui/ios';
import RegLaurelWreath from './RegLaurelWreath';
import trophyUrl from './contest-trophy.webp';
import { createConfetti } from './confetti';
import './reg-contest-ceremony.css';

/*
 * Церемония итогов конкурса «Топ по регистрациям».
 *
 * Сценарий по постановке владельца (25.09.2026):
 *   конфетти сверху → «Линия»: выходит 2 место (фото, ФИО, регистрации,
 *   приз), сдвигается вправо, выходит 1 место → блок «Линии» отъезжает
 *   вправо и притухает, так же выходят победители «Чатов» → «Чаты» уходят
 *   влево, обе группы встают рядом.
 *
 * Как устроено. Вся разметка на месте с первого кадра — невидимая, но
 * занимающая своё место, поэтому высота сцены не прыгает и страница под ней
 * не едет. Движение целиком в CSS: компонент только переключает состояния
 * (data-state) у колонок и карточек по таймеру, а переходы между
 * состояниями описаны в reg-contest-ceremony.css. Финальная раскладка —
 * обычная сетка; «по центру», «сдвинута» и «в стороне» — это transform
 * от финального места, поэтому к концу все transform снимаются в ноль.
 *
 * Оформление — как у остального сайта: белая iosCard, палитра slate, SF Pro,
 * аватарка в лавровом венке с медалью (как на подиуме рейтинга), приз —
 * янтарём. Цвета заданы утилитами Tailwind, поэтому тёмную тему церемония
 * получает из общего слоя theme-dark.css; в CSS самой церемонии — только
 * раскладка и движение. Победитель стоит на тумбе с призом, тумба первого
 * места выше — весь его столбец поднят над вторым, как на пьедестале.
 *
 * Играет при каждом входе во вкладку; «Пропустить» быстро доводит до финала,
 * «Показать ещё раз» проигрывает заново. Тем, кто в системе просил меньше
 * движения, сразу показываем итог — без конфетти и без хореографии.
 */

// Финальная раскладка слева направо и порядок выхода — разные: первой
// выходит «Линия», хотя стоит она справа.
const LAYOUT_ORDER = ['chat', 'line'];
const REVEAL_ORDER = ['line', 'chat'];

// Паузы сценария, мс.
const T_HEADER = 80;          // шапка: кубок, заголовок, даты
const T_FIRST_GROUP = 1150;   // первая группа выходит после шапки
const T_LABEL_TO_CARD = 420;  // подпись группы → первая карточка
const T_CARD = 1550;          // карточка раскрылась → следующий шаг
const T_WINNER_HOLD = 1750;   // первое место — подольше, это кульминация
const T_SETTLE = 950;         // разъезд групп по местам
const FAST_MS = 420;          // «Пропустить»: всё доезжает за это время

const MEDAL = { 1: 'medal-gold', 2: 'medal-silver', 3: 'medal-bronze' };

const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
    'августа', 'сентября', 'октября', 'ноября', 'декабря'];

const fmtDay = (iso, withYear = false) => {
    if (!iso) return '';
    const d = new Date(`${iso}T00:00:00`);
    return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}${withYear ? ` ${d.getFullYear()}` : ''}`;
};

const pluralRegistrations = (n) => {
    const abs = Math.abs(n) % 100;
    const d = abs % 10;
    if (abs > 10 && abs < 20) return 'регистраций';
    if (d === 1) return 'регистрация';
    if (d >= 2 && d <= 4) return 'регистрации';
    return 'регистраций';
};

const initials = (name) => {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    return (parts.slice(0, 2).map((w) => w[0]).join('') || '•').toUpperCase();
};

const fmtMoney = (n) => Math.round(n).toLocaleString('ru-RU');

const prefersReducedMotion = () =>
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Победители группы — строки с призом, по местам. Призы расставляет сервер
// по итоговому протоколу, поэтому здесь ничего не пересчитываем.
const pickWinners = (items) => (items || [])
    .filter((item) => item && item.prize != null)
    .sort((a, b) => a.place - b.place);

// Сценарий: список шагов с моментом наступления. Шаг — строка:
// 'header', '<группа>' (вышла подпись), '<группа>:<место>' (вышла карточка),
// 'settled' (группы разъехались по местам), 'done'.
const buildTimeline = (groupsInReveal) => {
    const steps = [{ key: 'header', at: T_HEADER }];
    let t = T_FIRST_GROUP;
    groupsInReveal.forEach(({ key, winners }, gi) => {
        if (gi > 0) t += 250;
        steps.push({ key, at: t });
        t += T_LABEL_TO_CARD;
        // С последнего места к первому: интрига держится до конца.
        [...winners].reverse().forEach((w) => {
            steps.push({ key: `${key}:${w.place}`, at: t });
            t += w.place === 1 ? T_WINNER_HOLD : T_CARD;
        });
    });
    steps.push({ key: 'settled', at: t });
    steps.push({ key: 'done', at: t + T_SETTLE });
    return steps;
};

// Плавный счётчик суммы приза: 0 → value, пока карточка раскрывается.
const CountUp = ({ value, active, instant }) => {
    const [shown, setShown] = useState(instant ? value : 0);
    useEffect(() => {
        if (!active) {
            setShown(0);
            return undefined;
        }
        if (instant) {
            setShown(value);
            return undefined;
        }
        let raf = 0;
        const started = performance.now();
        const duration = 1100;
        const tick = (now) => {
            const k = Math.min(1, (now - started) / duration);
            const eased = 1 - Math.pow(1 - k, 3);
            setShown(value * eased);
            if (k < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(raf);
    }, [value, active, instant]);
    return <>{fmtMoney(shown)}</>;
};

const WinnerPhoto = ({ item }) => {
    const [broken, setBroken] = useState(false);
    if (item.avatar_url && !broken) {
        return (
            <img src={item.avatar_url} alt="" className="h-full w-full object-cover"
                 decoding="async" referrerPolicy="no-referrer"
                 onError={() => setBroken(true)} />
        );
    }
    return <span className="rcc-initials font-semibold text-slate-400 select-none">{initials(item.name)}</span>;
};

// Столбец пьедестала: венок с фото и медалью, ФИО, регистрации — и тумба с
// призом. Тумба первого места выше, поэтому весь столбец стоит над вторым.
const WinnerSlot = ({ item, state, fast, isMe, photoRef }) => {
    const first = item.place === 1;
    return (
        <div className="rcc-slot" data-place={item.place} data-state={state}>
            <div className="rcc-person">
                <div className="rcc-photo" ref={photoRef}>
                    <RegLaurelWreath place={item.place} className="pointer-events-none absolute inset-0 h-full w-full" />
                    <div className="rcc-avatar flex items-center justify-center overflow-hidden rounded-full bg-slate-100 ring-1 ring-slate-200/70">
                        <WinnerPhoto item={item} />
                    </div>
                    <span className={`rcc-medal ${MEDAL[item.place] || 'bg-slate-300'} flex items-center justify-center rounded-full font-bold text-white shadow ring-2 ring-white`}
                          aria-label={`${item.place} место`}>{item.place}</span>
                </div>
                <div className="rcc-name-box">
                    <p className="rcc-name font-semibold text-slate-800" title={item.name}>{item.name}</p>
                </div>
                <p className="rcc-count text-slate-500">
                    {isMe && <span className="mr-1.5 rounded-full bg-blue-600 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">Вы</span>}
                    {item.drivers} {pluralRegistrations(item.drivers)}
                </p>
            </div>
            <div className={`rcc-pedestal ${first
                ? 'bg-amber-50 text-amber-700 ring-1 ring-amber-200/70'
                : 'bg-slate-50 text-slate-700 ring-1 ring-slate-200/70'}`}>
                <p className="rcc-prize font-bold tabular-nums">
                    <CountUp value={item.prize} active={state !== 'hidden'} instant={fast} />
                    <span className="rcc-prize-currency">₸</span>
                </p>
                <p className="rcc-place text-[11px] font-semibold uppercase tracking-wider opacity-70">{item.place} место</p>
            </div>
        </div>
    );
};

const RegContestCeremony = ({ contest, groups, currentUserId = null }) => {
    const groupLabels = contest?.group_labels || { chat: 'Чаты', line: 'Линия' };

    // Группы, где есть кого награждать, — в порядке финальной раскладки.
    const layout = useMemo(() => LAYOUT_ORDER
        .map((key) => ({ key, winners: pickWinners(groups?.[key]) }))
        .filter((g) => g.winners.length > 0), [groups]);
    const reveal = useMemo(() => REVEAL_ORDER
        .map((key) => layout.find((g) => g.key === key))
        .filter(Boolean), [layout]);
    // Стабильный ключ состава: фоновое обновление данных каждые полчаса
    // приносит новый объект groups, но церемонию перезапускать не должно.
    const lineupKey = layout
        .map((g) => `${g.key}=${g.winners.map((w) => `${w.user_id}:${w.place}:${w.prize}`).join(',')}`)
        .join('|');

    const [reached, setReached] = useState(() => new Set());
    const [fast, setFast] = useState(false);
    // Кадр «сброса» перед повтором: всё прячется без переходов.
    const [resetting, setResetting] = useState(true);
    const [reduced] = useState(prefersReducedMotion);
    const [runId, setRunId] = useState(0);

    const stageRef = useRef(null);
    const confettiRef = useRef(null);
    const timersRef = useRef([]);
    // «Пропустить» и «Показать ещё раз» — одна и та же кнопка на одном месте:
    // второй щелчок двойного клика попадал бы уже в новую и делал обратное.
    const lastControlAtRef = useRef(0);
    const photoRefs = useRef({});

    const timeline = useMemo(() => buildTimeline(reveal), [lineupKey]);

    const clearTimers = () => {
        timersRef.current.forEach((id) => clearTimeout(id));
        timersRef.current = [];
    };

    const burstFrom = (groupKey, place) => {
        const el = photoRefs.current[`${groupKey}:${place}`];
        if (!el || !confettiRef.current) return;
        const rect = el.getBoundingClientRect();
        confettiRef.current.burst(rect.left + rect.width / 2, rect.top + rect.height / 2);
    };

    useEffect(() => {
        if (!reveal.length) return undefined;
        clearTimers();
        if (reduced) {
            setResetting(false);
            setFast(true);
            setReached(new Set(timeline.map((s) => s.key)));
            return undefined;
        }
        if (!confettiRef.current) confettiRef.current = createConfetti();
        const confetti = confettiRef.current;
        setResetting(true);
        setFast(false);
        setReached(new Set());

        // Два кадра на «сброс»: сначала всё прячется без анимации, потом
        // запускается сценарий — иначе повтор начинался бы с середины.
        let raf2 = 0;
        const raf1 = requestAnimationFrame(() => {
            raf2 = requestAnimationFrame(() => {
                setResetting(false);
                // Сцена под шапкой раздела и вкладками: на ноутбуке 1366×768 и
                // на телефоне тумбы с призами оказывались ниже края экрана, и
                // всё раскрытие шло там. Докручиваем к сцене, только если она
                // не видна целиком (отступ сверху — scroll-margin-top в CSS).
                const stage = stageRef.current;
                if (stage) {
                    const rect = stage.getBoundingClientRect();
                    if (rect.top < 0 || rect.bottom > window.innerHeight) {
                        stage.scrollIntoView({ block: 'start', behavior: 'smooth' });
                    }
                }
                confetti.rain(2800);
                timeline.forEach((step) => {
                    timersRef.current.push(setTimeout(() => {
                        setReached((prev) => new Set(prev).add(step.key));
                        const [groupKey, place] = step.key.split(':');
                        // Салют — когда карточка первого места уже встала.
                        if (place === '1') {
                            timersRef.current.push(setTimeout(() => burstFrom(groupKey, 1), 380));
                        }
                    }, step.at));
                });
            });
        });
        return () => {
            cancelAnimationFrame(raf1);
            cancelAnimationFrame(raf2);
            clearTimers();
        };
    }, [timeline, runId, reduced]);

    // Уход со вкладки посреди дождя — холст убираем вместе с компонентом.
    useEffect(() => () => {
        clearTimers();
        if (confettiRef.current) confettiRef.current.destroy();
    }, []);

    const done = reached.has('done');
    const controlGuard = () => {
        const now = performance.now();
        if (now - lastControlAtRef.current < 500) return false;
        lastControlAtRef.current = now;
        return true;
    };
    const skip = () => {
        if (!controlGuard()) return;
        clearTimers();
        if (confettiRef.current) confettiRef.current.stopRain();
        setFast(true);
        setReached(new Set(timeline.map((s) => s.key)));
    };
    const replay = () => {
        if (!controlGuard()) return;
        setRunId((n) => n + 1);
    };

    if (!layout.length) return null;

    const settled = reached.has('settled');
    // Какая группа сейчас «в свете»: последняя вышедшая, пока не разъехались.
    const spotlight = settled ? null : [...reveal].reverse().find((g) => reached.has(g.key))?.key || null;

    const columnState = (key) => {
        if (!reached.has(key)) return 'hidden';
        if (settled) return 'final';
        return key === spotlight ? 'spot' : 'aside';
    };
    const cardState = (key, place, winners) => {
        if (!reached.has(`${key}:${place}`)) return 'hidden';
        // Пока не вышло место выше, карточка стоит по центру своей группы.
        const higher = winners.filter((w) => w.place < place);
        const alone = higher.length > 0 && higher.every((w) => !reached.has(`${key}:${w.place}`));
        return alone ? 'center' : 'final';
    };

    const period = contest?.registered_from && contest?.registered_to
        ? `${fmtDay(contest.registered_from)} — ${fmtDay(contest.registered_to, true)}`
        : '';

    return (
        <section ref={stageRef} className={`rcc-stage ${iosCard}${fast ? ' is-fast' : ''}${resetting ? ' is-reset' : ''}${reached.has('header') ? ' is-open' : ''}`}
                 style={{ fontFamily: APPLE_FONT, '--rcc-fast': `${FAST_MS}ms` }}
                 aria-label="Итоги конкурса">
            <div className="rcc-glow" aria-hidden="true" />

            {!reduced && (
                done ? (
                    <button type="button" className="rcc-control rcc-control-icon bg-slate-100 text-slate-600 hover:bg-slate-200" onClick={replay}
                            aria-label="Показать ещё раз" title="Показать ещё раз">
                        <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
                            <path d="M15.5 10a5.5 5.5 0 1 1-1.61-3.89" fill="none" stroke="currentColor"
                                  strokeWidth="1.7" strokeLinecap="round" />
                            <path d="M15.8 3.6v3.2h-3.2" fill="none" stroke="currentColor"
                                  strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                    </button>
                ) : (
                    <button type="button" className="rcc-control bg-slate-100 text-slate-600 hover:bg-slate-200" onClick={skip}>
                        Пропустить
                        <svg viewBox="0 0 20 20" width="13" height="13" aria-hidden="true">
                            <path d="M4 5l6 5-6 5V5zm7 0l6 5-6 5V5z" fill="currentColor" />
                        </svg>
                    </button>
                )
            )}

            <header className="rcc-header">
                <div className="rcc-trophy">
                    <span className="rcc-trophy-glow" aria-hidden="true" />
                    {/* Кубок сгенерирован в Higgsfield (gpt_image_2), чёрный фон снят в альфу. */}
                    <div className="rcc-trophy-float">
                        <img src={trophyUrl} alt="" className="rcc-trophy-img" decoding="async" />
                        <span className="rcc-trophy-shine" aria-hidden="true"
                              style={{ WebkitMaskImage: `url(${trophyUrl})`, maskImage: `url(${trophyUrl})` }} />
                    </div>
                </div>
                <p className="rcc-eyebrow font-semibold uppercase text-slate-400">Конкурс «{contest?.title || 'Топ по регистрациям'}»</p>
                <h2 className="rcc-title font-bold text-slate-900">Победители</h2>
                {period && <p className="rcc-period text-slate-500">{period}</p>}
            </header>

            <div className={`rcc-groups${layout.length === 1 ? ' is-single' : ''}`}>
                {layout.map(({ key, winners }, slot) => (
                    <div key={key} className="rcc-col" data-state={columnState(key)}
                         data-slot={layout.length === 1 ? 'single' : slot === 0 ? 'start' : 'end'}>
                        <h3 className="rcc-group-label font-semibold text-slate-900"><span>{groupLabels[key] || key}</span></h3>
                        <div className={`rcc-podium${winners.length === 1 ? ' is-single' : ''}`}>
                            {winners.map((item) => (
                                <WinnerSlot key={`${key}:${item.place}`} item={item} fast={fast}
                                            state={cardState(key, item.place, winners)}
                                            isMe={currentUserId != null && item.user_id === currentUserId}
                                            photoRef={(el) => { photoRefs.current[`${key}:${item.place}`] = el; }} />
                            ))}
                        </div>
                    </div>
                ))}
            </div>

            <p className="rcc-thanks text-slate-500" data-state={done ? 'final' : 'hidden'}>
                Спасибо всем участникам — вы задали высокую планку! Ждём новых побед{'\u00a0'}🎉
            </p>
        </section>
    );
};

export default RegContestCeremony;
