import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import {
    FileText, Gamepad2, Layers, Loader2, PlayCircle, Smartphone,
} from 'lucide-react';

import { iosCard, iosGroupLabel, iosBtnSecondary, IosBadge } from '../ui/ios';
import { TRAINERS, TRAINER_CARDS, findTrainer } from './trainers/registry';

const TrainerModal = lazy(() => import('./trainers/TrainerPlayer'));

/* Третья половина вкладки «Статьи»: тренажёры.
 *
 * Зачем отдельный экран, если тренажёр и так открывается кнопкой из статьи.
 * Затем, что у него два разных читателя. Оператору тренажёр нужен внутри
 * инструкции — там, где он читает про подписание. Тому, кто ведёт базу знаний,
 * нужен ответ на другие вопросы: какие тренажёры вообще есть, что внутри
 * каждого и в каких статьях он уже стоит. Второй набор вопросов в статье не
 * задать — для него и нужен список.
 *
 * «Где вставлен» считает сервер (/api/wiki/trainers): кнопка живёт в ТЕКСТЕ
 * статьи, и найти её можно только поиском по содержимому — во фронте таких
 * данных нет вовсе.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 >= 11 && mod100 <= 14) return many;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
};

const STATUS_LABELS = {
    draft: 'Черновик',
    on_approval: 'На согласовании',
    published: 'Опубликована',
    requires_verification: 'Требует проверки',
    archived: 'В архиве',
    expired: 'Устарела',
};

export default function WikiTrainers({ base, headers, onOpenArticle = null }) {
    const [usages, setUsages] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [openKey, setOpenKey] = useState(null);

    const load = useCallback(() => {
        setLoading(true);
        return axios.get(`${base}/trainers`, { headers })
            .then((r) => { setUsages(r.data?.usages || {}); setError(''); })
            .catch((e) => {
                // Список тренажёров живёт в коде и от сервера не зависит: даже
                // если запрос упал, запустить тренажёр можно. Поэтому ошибка
                // здесь — строка над списком, а не пустой экран.
                setUsages({});
                setError(errText(e, 'Не удалось узнать, в каких статьях стоят тренажёры'));
            })
            .finally(() => setLoading(false));
    }, [base, headers]);

    useEffect(() => { load(); }, [load]);

    const scenario = openKey ? findTrainer(openKey) : null;

    return (
        <div className="space-y-3">
            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Как это работает</div>
                <div className={`${iosCard} px-4 py-3.5`}>
                    <p className="text-[13px] leading-relaxed text-slate-600">
                        Тренажёр — учебный телефон с экранами настоящего приложения: помощник
                        объясняет шаг, а неверное нажатие не ломает урок, а получает объяснение.
                        Чтобы поставить тренажёр в статью, выберите у статьи тип
                        {' '}<b className="font-semibold text-slate-800">«Тренажёр»</b> — в панели
                        редактора появится выбор тренажёра, а кнопку в тексте можно перетащить
                        и растянуть.
                    </p>
                </div>
            </section>

            {error && (
                <div className={`${iosCard} flex items-center gap-2 px-4 py-3 text-[13px] text-amber-700`}>
                    {error}
                    <button type="button" className="font-semibold text-amber-800 underline"
                        onClick={load}>
                        Повторить
                    </button>
                </div>
            )}

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>
                    Готовые тренажёры · {TRAINER_CARDS.length}
                </div>

                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                    {TRAINERS.map((trainer) => {
                        const card = TRAINER_CARDS.find((c) => c.key === trainer.key);
                        const articles = usages?.[trainer.key] || [];
                        return (
                            <article key={trainer.key} className={`${iosCard} flex flex-col gap-3 p-4`}>
                                <header className="flex items-start gap-3">
                                    <div className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl
                                                    bg-indigo-50 text-indigo-600">
                                        <Gamepad2 size={19} />
                                    </div>
                                    <div className="min-w-0">
                                        <h3 className="text-[15px] font-semibold leading-tight text-slate-900">
                                            {trainer.title}
                                        </h3>
                                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                                            <IosBadge tone="slate">
                                                <Smartphone size={11} /> {trainer.app}
                                            </IosBadge>
                                            <IosBadge tone="blue">
                                                <Layers size={11} /> {card.stages}
                                                {' '}{plural(card.stages, 'шаг', 'шага', 'шагов')}
                                            </IosBadge>
                                        </div>
                                    </div>
                                </header>

                                <p className="text-[13px] leading-relaxed text-slate-600">
                                    {trainer.description}
                                </p>

                                {/* Список шагов открытым: на этом экране он и есть содержание
                                    тренажёра — по нему решают, тот ли это тренажёр, который
                                    нужен статье. */}
                                <ol className="space-y-1 pl-5 text-[12.5px] leading-relaxed text-slate-500
                                               [list-style:decimal]">
                                    {(trainer.checklist || []).map((item, index) => (
                                        <li key={`${index}-${item}`}>{item}</li>
                                    ))}
                                </ol>

                                <div className="rounded-xl bg-slate-50 px-3 py-2.5">
                                    <div className="mb-1 flex items-center gap-1.5 text-[12px] font-medium text-slate-500">
                                        <FileText size={13} /> Где вставлен
                                    </div>
                                    {loading ? (
                                        <span className="inline-flex items-center gap-1.5 text-[12.5px] text-slate-400">
                                            <Loader2 size={13} className="animate-spin" /> считаем…
                                        </span>
                                    ) : articles.length === 0 ? (
                                        <span className="text-[12.5px] text-slate-400">
                                            Пока ни в одной статье
                                        </span>
                                    ) : (
                                        <div className="flex flex-wrap gap-1.5">
                                            {articles.map((item) => (
                                                <button
                                                    key={item.id}
                                                    type="button"
                                                    onClick={() => onOpenArticle?.(item.slug)}
                                                    className="inline-flex max-w-full items-center gap-1.5 rounded-full
                                                               bg-white px-2.5 py-1 text-[12px] text-slate-600
                                                               ring-1 ring-slate-200 transition hover:text-indigo-600"
                                                    title={STATUS_LABELS[item.status] || item.status}
                                                >
                                                    <span className="truncate">{item.title}</span>
                                                    {item.status !== 'published' && (
                                                        <span className="shrink-0 text-[10.5px] text-slate-400">
                                                            {STATUS_LABELS[item.status] || item.status}
                                                        </span>
                                                    )}
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                <button
                                    type="button"
                                    className={`${iosBtnSecondary} justify-center`}
                                    onClick={() => setOpenKey(trainer.key)}
                                >
                                    <PlayCircle size={15} /> Пройти тренажёр
                                </button>
                            </article>
                        );
                    })}
                </div>
            </section>

            {scenario && (
                <Suspense fallback={(
                    <div className="fixed inset-0 z-[95] flex items-center justify-center gap-2
                                    bg-slate-900/40 text-white backdrop-blur-md">
                        <Loader2 size={18} className="animate-spin" />
                        <span className="text-[13px]">Готовим тренажёр…</span>
                    </div>
                )}>
                    <TrainerModal scenario={scenario} onClose={() => setOpenKey(null)} />
                </Suspense>
            )}
        </div>
    );
}
