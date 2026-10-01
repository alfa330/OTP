import React, { useEffect, useRef, useState } from 'react';
import { BookMarked, AlertTriangle, Link2 } from 'lucide-react';
import { IosBadge, IosHint } from '../ui/ios';
import { VERDICT_LABEL } from './humanReview';

/* «Такой разбор уже был?» — под исправленным критерием в карточке.
 *
 * Отвечает на вопрос владельца «нормально ли ИИ использует разборы» прямо там,
 * где человек исправляет ИИ: показывает разборы того же критерия, повторяющие
 * то, что он пишет, и правила, которые ИИ в ЭТОЙ оценке уже получил. У каждого —
 * одна строка о том, дошло ли правило до ИИ, а если нет — почему. Сервер:
 * POST /api/ai-qa/adjudicate/similar (call_qa/api.py · adjudication_similar),
 * пороги сходства измерены и описаны в call_qa/rag/similar.py.
 *
 * Пусто — ничего не рисуем: строка «похожих нет» под каждым исправлением была
 * бы шумом. Короче восьми знаков текст не ищем — сервер всё равно вернёт
 * только правила, бывшие в промпте. */

const STATUS = {
    active: { tone: 'green', label: 'Действует' },
    draft: { tone: 'amber', label: 'Черновик' },
    quarantined: { tone: 'red', label: 'Требует проверки' },
    deprecated: { tone: 'slate', label: 'Удалено' },
};

const MIN_QUERY = 8;
const DEBOUNCE_MS = 700;

const percent = (value) => `${Math.round(Number(value) * 100)}%`;
const decimal = (value, digits = 2) => Number(value).toFixed(digits).replace('.', ',');

/* «Сходство 0,67 ниже порога 0,68»: порог — с нужной точностью, сходство —
 * округлённое вниз до неё же, иначе 0,678 показалось бы как «0,68 ниже 0,68». */
function belowThreshold(similarity, threshold) {
    const digits = Math.abs(threshold * 100 - Math.round(threshold * 100)) < 1e-6 ? 2 : 3;
    const scale = 10 ** digits;
    const floored = Math.floor(Number(similarity) * scale + 1e-9) / scale;
    return `сходство с разговором ${decimal(floored, digits)} ниже порога ${decimal(threshold, digits)}`;
}

/* Что было с правилом в этой оценке (код — call_qa/api.py · _rule_run_state). */
export function runNote(item) {
    const run = item?.run || {};
    const status = item?.rule_status;
    switch (run.code) {
    case 'included':
        return item.same_verdict
            ? { tone: 'text-rose-600', text: 'ИИ получил это правило в этой оценке — и всё равно ошибся' }
            : { tone: 'text-slate-500', text: 'ИИ получил это правило в этой оценке' };
    case 'rag_off':
        return { tone: 'text-slate-500', text: 'Эту оценку ИИ делал без базы разборов' };
    case 'retrieval_failed':
        return { tone: 'text-amber-700', text: 'Поиск правил в этой оценке не сработал' };
    case 'below_threshold':
        return {
            tone: 'text-slate-500',
            text: run.similarity != null && run.threshold != null
                ? `Не попало в эту оценку: ${belowThreshold(run.similarity, run.threshold)}`
                : 'Не попало в эту оценку: ниже порога сходства с разговором',
        };
    case 'top_k':
        return { tone: 'text-slate-500', text: 'Не попало: по критерию ИИ получает три самых близких правила' };
    case 'not_retrieved':
        return { tone: 'text-slate-500', text: 'Не попало в поиск: другие правила критерия ближе к разговору' };
    case 'missing_from_snapshot':
        // Правило действовало, а в базе знаний оценки его нет — сбой публикации.
        return { tone: 'text-amber-700', text: 'Действовало, но в базу знаний этой оценки не попало' };
    default:
        // inactive_at_run: в момент оценки правило не действовало.
        if (status === 'active') return { tone: 'text-slate-500', text: 'Включено после этой оценки — следующие оценки его учтут' };
        if (status === 'deprecated') return { tone: 'text-slate-500', text: 'Правило с тех пор удалено' };
        if (status === 'quarantined') return { tone: 'text-slate-500', text: 'На проверке — ИИ его не получает' };
        return { tone: 'text-slate-500', text: 'Черновик — ИИ его не получает' };
    }
}

/* Заголовок — самая сильная находка: дубль важнее «похоже», а оба важнее
 * простого «вот что ИИ получал». */
export function headline(items) {
    if (items.some((item) => item.verdict === 'duplicate')) return 'Такой разбор уже есть';
    if (items.some((item) => item.verdict === 'similar')) return 'Похожий разбор уже есть';
    return 'ИИ получал правила по этому критерию';
}

function RuleRow({ item, linkTarget }) {
    const status = STATUS[item.rule_status] || STATUS.draft;
    const note = runNote(item);
    return (
        <li className="space-y-1">
            <p className="line-clamp-2 break-words text-[12.5px] leading-snug text-slate-700">
                {linkTarget && <Link2 size={12} className="mr-1 inline-block align-[-1px] text-slate-500" aria-label="Сюда добавится разбор" />}
                «{item.rule_text}»
            </p>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-500">
                <span>→ {VERDICT_LABEL[item.correct_verdict] || item.correct_verdict}</span>
                <IosBadge tone={status.tone} className="!px-2 !py-0.5">{status.label}</IosBadge>
                {item.score != null && (
                    <span className="tabular-nums" title="Сходство текста правил">
                        {item.verdict === 'duplicate' ? 'дубль' : 'похоже'} {percent(item.score)}
                    </span>
                )}
                {!item.same_verdict && (
                    <span className="inline-flex items-center gap-1 font-medium text-amber-700">
                        <AlertTriangle size={11} />там другой вердикт
                    </span>
                )}
            </div>
            <p className={`text-[11.5px] leading-snug ${note.tone}`}>{note.text}</p>
        </li>
    );
}

/* Итог сохранения разбора одной строкой (ответ POST /api/ai-qa/adjudicate).
 * «ИИ учтёт» — только по факту: направление оценивается с базой разборов
 * (rag_mode), и каждое исправление включено, привязано к правилу из промпта или
 * включится само после подготовки к поиску (auto_activation). Иначе — что
 * сохранено и что осталось черновиком. */
export function adjudicationSummary(count, result) {
    const activated = Number(result?.activated || 0);
    const linked = Number(result?.linked || 0);
    const auto = result?.auto_activation !== false;
    const pending = auto ? Number(result?.pending_index || 0) : 0;
    const drafts = (result?.rules || []).filter((rule) => rule.status === 'draft').length - pending;
    const live = !result?.rag_mode || ['active', 'canary'].includes(result.rag_mode);
    const parts = [];
    if (!live) {
        parts.push(`Исправлений: ${count} сохранено — направление сейчас оценивается без базы разборов`);
    } else if (!result || activated + linked + pending >= count) {
        parts.push(`Исправлений: ${count} — ИИ учтёт их в следующих оценках`);
    } else {
        parts.push(`Исправлений: ${count} сохранено`);
        if (activated) parts.push(`уже в оценке: ${activated}`);
    }
    if (linked) parts.push(`повтор правила, которое ИИ уже получал: ${linked}`);
    if (live && pending) parts.push(`включится после подготовки к поиску: ${pending}`);
    if (live && drafts > 0) parts.push(`ждёт включения в «Базе разборов»: ${drafts}`);
    return parts.join(' · ');
}

export default function SimilarRules({ criterion, text, verdict, onSimilar }) {
    // Ответ хранится вместе с запросом, на который он пришёл: пока человек
    // правит текст или вердикт, старое «новое правило не создастся» не показываем.
    const [answer, setAnswer] = useState(null);
    const request = useRef(0);
    // Последняя функция запроса — через ref: родитель пересоздаёт её на каждый
    // рендер, и в зависимостях эффекта она перезапускала бы поиск без конца.
    const fetcher = useRef(onSimilar);
    fetcher.current = onSimilar;
    const clean = String(text || '').trim();
    const query = clean.length >= MIN_QUERY ? clean : '';
    const criterionId = criterion?.criterion_id;
    const key = `${criterionId}\n${verdict}\n${query}`;

    useEffect(() => {
        if (!fetcher.current || !criterionId) return undefined;
        const id = ++request.current;
        const timer = window.setTimeout(async () => {
            const result = await fetcher.current?.(criterion, { text: query, verdict });
            if (id === request.current) setAnswer({ key, data: result || null });
        }, query ? DEBOUNCE_MS : 0);
        return () => window.clearTimeout(timer);
        // criterion берётся по id: объект критерия пересоздаётся вместе с карточкой.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [criterionId, query, verdict]);
    useEffect(() => () => { request.current += 1; }, []);

    return <SimilarRulesView data={answer?.data} fresh={answer?.key === key} />;
}

export function SimilarRulesView({ data, fresh = true }) {
    const items = data?.items || [];
    if (!items.length) return null;
    const duplicate = fresh && data?.duplicate_of
        ? items.find((item) => item.rule_id === data.duplicate_of) : null;
    return (
        <div className="mt-2 rounded-xl bg-slate-50/80 p-2.5 ring-1 ring-slate-200/70" aria-live="polite">
            <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold text-slate-600">
                <BookMarked size={13} className="shrink-0 text-slate-400" />
                {headline(items)}
                <IosHint label="Откуда это"
                    text={'Разборы того же критерия из «Базы разборов»: найденные по смыслу того, что вы пишете, '
                        + 'и правила, которые ИИ получил в этой оценке. Под каждым — дошло ли правило до ИИ. '
                        + 'Если ИИ правило получил и всё равно ошибся, это видно здесь же.'} />
            </div>
            <ul className="space-y-2.5">
                {items.map((item) => (
                    <RuleRow key={item.rule_id} item={item} linkTarget={item === duplicate} />
                ))}
            </ul>
            {duplicate && (
                <p className="mt-2 flex items-start gap-1.5 text-[11.5px] leading-snug text-slate-500">
                    <Link2 size={12} className="mt-[2px] shrink-0" />
                    Новое правило не создастся: ИИ это правило получил и всё равно ошибся — разбор добавится к нему.
                </p>
            )}
            {data?.degraded && (
                <p className="mt-2 text-[11px] text-slate-400">
                    Поиск по смыслу сейчас недоступен — сравнили только по словам.
                </p>
            )}
        </div>
    );
}
