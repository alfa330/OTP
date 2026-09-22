import React from 'react';
import { Check, Loader2, CloudDownload, Sparkles, PhoneIncoming, AlertTriangle, XCircle, RotateCcw, ChevronLeft } from 'lucide-react';
import { iosCard, iosBtnGhost, iosBtnSecondary } from '../ui/ios';

/* Звонок подтянут из АТС, а записи в облаке ещё нет: у отдела продаж файл
 * приносит мост с сервера записей станции (обычно до минуты), у Тез КЦ Binotel
 * отдаёт ссылку не сразу. Раньше карточка в этот момент отвечала «у звонка нет
 * записи» — сразу после кнопки «Из АТС» это читалось как сломанная оценка.
 * Теперь видно, на каком шаге процесс: подтянут → запись → распознавание и
 * оценка. Опрос ведёт CallQaView (раз в retry_after секунд), здесь — только
 * картинка состояния и кнопка «проверить сейчас».
 *
 * pending: { stage: 'downloading'|'evaluating'|'error'|'missing', source, job,
 *            job_error, retry_after, polls } */

const SOURCE_NOTE = {
    cdr: 'Мост забирает файл с сервера записей станции — обычно это меньше минуты.',
    binotel: 'Binotel готовит ссылку на запись — обычно это меньше минуты.',
    oktell: 'Запись забирается из Oktell.',
    pbx: 'Запись забирается из АТС.',
};

function Step({ state, Icon, title, note }) {
    // state: done | active | error | missing | todo
    const ring = state === 'done' ? 'bg-emerald-500 text-white'
        : state === 'active' ? 'bg-blue-600 text-white'
        : state === 'error' ? 'bg-amber-500 text-white'
        : state === 'missing' ? 'bg-rose-500 text-white'
        : 'bg-slate-100 text-slate-400';
    const Glyph = state === 'done' ? Check
        : state === 'active' ? Loader2
        : state === 'error' ? AlertTriangle
        : state === 'missing' ? XCircle : Icon;
    return (
        <li className="flex gap-3">
            <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full ${ring}`} aria-hidden="true">
                <Glyph size={15} strokeWidth={2.5} className={state === 'active' ? 'animate-spin' : ''} />
            </span>
            <div className="min-w-0 pt-1">
                <p className={`text-[13.5px] font-semibold ${state === 'todo' ? 'text-slate-400' : 'text-slate-800'}`}>{title}</p>
                {note && <p className="mt-0.5 text-[12.5px] leading-snug text-slate-500">{note}</p>}
            </div>
        </li>
    );
}

export default function AudioPendingCard({ pending, subject, polling = false, onRetry, onClose }) {
    const stage = pending?.stage || 'downloading';
    const missing = stage === 'missing';
    const evaluating = stage === 'evaluating';
    const audioState = missing ? 'missing' : evaluating ? 'done' : stage === 'error' ? 'error' : 'active';
    const audioNote = missing
        ? 'Сервер записей станции ответил, что файла нет: разговор был, а записи не осталось. Оценить нечего.'
        : stage === 'error'
            ? `Мост не смог забрать файл${pending?.job_error ? ` (${String(pending.job_error).slice(0, 120)})` : ''} — заказ поставлен снова, пробую ещё.`
            : evaluating ? 'Запись в облаке.' : (SOURCE_NOTE[pending?.source] || SOURCE_NOTE.pbx);
    const title = subject?.id ? `Звонок #${subject.id}` : 'Звонок';
    const who = [subject?.operator, subject?.datetime].filter(Boolean).join(' · ');

    return (
        <div className={`${iosCard} mx-auto max-w-xl px-6 py-6`} role="status" aria-live="polite">
            <div className="mb-5">
                <p className="text-[15px] font-semibold text-slate-900">{title}</p>
                {who && <p className="mt-0.5 text-[12.5px] text-slate-500">{who}</p>}
            </div>
            <ol className="space-y-4">
                <Step state="done" Icon={PhoneIncoming} title="Звонок подтянут из АТС" />
                <Step state={audioState} Icon={CloudDownload}
                      title={missing ? 'Записи нет' : evaluating ? 'Запись получена' : 'Запись скачивается'}
                      note={audioNote} />
                <Step state={missing ? 'todo' : evaluating ? 'active' : 'todo'} Icon={Sparkles}
                      title="Распознавание и оценка ИИ"
                      note={evaluating ? 'Транскрипт и оценка по критериям — обычно 30–60 секунд.' : 'Начнётся, как только запись будет в облаке.'} />
            </ol>
            <div className="mt-6 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-4">
                <p className="text-[12px] text-slate-400">
                    {missing ? 'Дальше ждать нечего.'
                        : evaluating ? 'Карточка откроется сама.'
                        : `Проверяю каждые ${pending?.retry_after || 10} с${pending?.polls > 1 ? ` · попыток ${pending.polls}` : ''}.`}
                </p>
                <div className="flex items-center gap-2">
                    {onClose && (
                        <button type="button" onClick={onClose} className={iosBtnGhost}>
                            <ChevronLeft size={14} />Назад
                        </button>
                    )}
                    {!missing && !evaluating && onRetry && (
                        <button type="button" onClick={onRetry} disabled={polling} className={iosBtnSecondary}>
                            {polling ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                            Проверить сейчас
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}
