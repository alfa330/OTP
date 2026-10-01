import React, { memo, useCallback, useMemo, useRef } from 'react';
import DealBadge from './DealBadge';
import { Languages, AlertTriangle, MessageSquare, Paperclip, PhoneOff } from 'lucide-react';
import { APPLE_FONT, iosCard, IosBadge, IosHint, scoreTone } from '../ui/ios';
import ChatThread from '../c2d_eval/ChatThread';
import CriteriaReviewPanel from './CriteriaReviewPanel';
import { CHAT_SUBJECTS } from './subjects';

/* Карточка ревью одного субъекта оценки — центральный экран взаимодействия с ИИ.
 * Субъект — звонок (аудио + диаризация; из журнала или подтянутый из АТС) либо
 * переписка: эпизод Wazzup у Верификаторов ОП, заявка Chat2Desk у СЗоВ, эпизод
 * ChatApp у Тез КЦ (сообщения + содержимое вложений). Форма данных одна: строки
 * транскрипта, критерии, отпечатки прогона. Данные приходят только с бэкенда
 * (props.call). Мок-данных нет. */

// Видов переписки три (Wazzup у ОП, Chat2Desk у СЗоВ, ChatApp у Тез КЦ) —
// сравнение с одной строкой открывало бы заявку СЗоВ как звонок.
const isChatSubject = (kind) => CHAT_SUBJECTS.includes(kind || 'call');

// Переписку чата рисуем тем же компонентом, что «Чаты ОП» (ChatThread):
// строки транскрипта эпизода → сообщения снапшота. Тело строки уже без префикса
// «кто:» (новый бэкенд), но для старых закэшированных эпизодов префикс срезаем тут.
const CHAT_SPEAKER_LABEL = {
    operator: 'Оператор', other_operator: 'Другой сотрудник', bot: 'Рассылка', client: 'Клиент',
};
const CHAT_PREFIX_RE = /^(Оператор|Клиент|Другой сотрудник|Рассылка)\b[^:]*:\s+/;

function chatLinesToSnapshot(lines, operatorName) {
    const messages = (lines || []).map((line, i) => {
        const isClient = line.speaker === 'client';
        const label = CHAT_SPEAKER_LABEL[line.speaker] || 'Клиент';
        const name = line.author ? String(line.author) : '';
        const media = line.media || {};
        // Новый бэкенд отдаёт чистое тело в line.body; для старых закэшированных
        // эпизодов срезаем префикс «кто:» из seg.
        const body = (line.body != null
            ? line.body
            : (line.seg || []).map((s) => s.t).join(' ').replace(CHAT_PREFIX_RE, '')).trim();
        return {
            id: line.message_id || `m${i}`,
            type: (line.outgoing ?? (line.speaker !== 'client')) ? 'to_client' : 'from_client',
            created: line.created || null,
            text: body,
            author: isClient ? undefined : (name ? `${label} · ${name}` : label),
            photo: media.kind === 'image' ? media.url : undefined,
            audio: media.kind === 'audio' ? media.url : undefined,
            video: media.kind === 'video' ? media.url : undefined,
            pdf: media.kind === 'document' ? media.url : undefined,
            attachments: (media.url && !['image', 'audio', 'video', 'document'].includes(media.kind))
                ? [{ link: media.url, name: media.label || 'файл' }] : undefined,
            annotation: line.annotation || null,
        };
    });
    return { messages, operator_name: operatorName };
}

const formatTimestamp = (ms) => {
    const total = Math.max(0, Math.floor(Number(ms || 0) / 1000));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
};

// Кто говорит: у звонка две стороны, у чата к ним добавляются чужой сотрудник
// (в эпизоде мог ответить кто-то ещё — за него оператор не отвечает) и рассылка.
const SPEAKER = {
    operator:       { label: 'Оператор',         side: 'left',  cls: 'bg-blue-50 text-slate-800 ring-1 ring-blue-100',   head: 'text-blue-600' },
    other_operator: { label: 'Другой сотрудник', side: 'left',  cls: 'bg-violet-50 text-slate-800 ring-1 ring-violet-100', head: 'text-violet-600' },
    bot:            { label: 'Рассылка',         side: 'left',  cls: 'bg-slate-50 text-slate-500 ring-1 ring-slate-200',  head: 'text-slate-400' },
    client:         { label: 'Клиент',           side: 'right', cls: 'bg-slate-100 text-slate-700',                        head: 'text-slate-500' },
};

function LineMedia({ media }) {
    if (!media?.url) return null;
    if (media.kind === 'image') {
        return (
            <a href={media.url} target="_blank" rel="noreferrer" className="mt-1.5 block">
                <img src={media.url} alt={media.label || 'вложение'} loading="lazy"
                     className="max-h-44 w-auto rounded-xl ring-1 ring-slate-200" />
            </a>
        );
    }
    if (media.kind === 'audio') {
        return <audio controls preload="none" src={media.url} className="mt-1.5 h-8 w-full" aria-label="Голосовое сообщение" />;
    }
    return (
        <a href={media.url} target="_blank" rel="noreferrer"
           className="mt-1.5 inline-flex items-center gap-1 rounded-lg bg-white/70 px-2 py-1 text-[11.5px] font-medium text-slate-600 ring-1 ring-slate-200 hover:text-slate-900">
            <Paperclip size={11} />{media.label || 'вложение'}
        </a>
    );
}

const TranscriptLine = memo(function TranscriptLine({ line, onSeek }) {
    const meta = SPEAKER[line.speaker] || SPEAKER.client;
    const isLeft = meta.side === 'left';
    return (
        <div className={`flex ${isLeft ? 'justify-start' : 'justify-end'}`}>
            <div className={`max-w-[88%] rounded-2xl px-3.5 py-2 text-[13.5px] leading-relaxed ${meta.cls}`}>
                <div className={`mb-0.5 flex items-center justify-between gap-3 text-[10.5px] font-semibold uppercase tracking-wide ${meta.head}`}>
                    <span>{meta.label}{line.author ? ` · ${line.author}` : ''}</span>
                    {line.start_ms != null ? (
                        <button type="button" onClick={() => onSeek?.(line.start_ms)}
                            className="rounded px-1 py-0.5 font-medium tabular-nums text-slate-500 hover:bg-white/70 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60"
                            aria-label={`Перейти к ${formatTimestamp(line.start_ms)} записи`}>
                            {formatTimestamp(line.start_ms)}
                        </button>
                    ) : line.ts ? (
                        <span className="font-medium tabular-nums text-slate-400">{line.ts}</span>
                    ) : null}
                </div>
                <span>
                    {(line.seg || []).map((s, i) => s.c != null && s.c < 0.5
                        ? <mark key={i} title={`распознано неуверенно · ${Math.round(s.c * 100)}%`}
                                className="rounded bg-amber-100 px-0.5 text-amber-800 decoration-amber-400 decoration-dotted underline">{s.t}</mark>
                        : <span key={i}>{s.t}</span>)}
                </span>
                <LineMedia media={line.media} />
            </div>
        </div>
    );
});

const SCORE_TEXT = { green: 'text-emerald-600', amber: 'text-amber-600', red: 'text-rose-600' };

/* Балл в шапке карточки — как колонки списка разговоров: подпись, крупное число в
 * цвет балла, под ним пояснение. Три пилюли столбиком читались как три кнопки. */
function ScoreStat({ label, value, sub = null, title }) {
    return (
        <div className="min-w-[3.5rem] text-right" title={title}>
            <div className="text-[11px] font-medium text-slate-500">{label}</div>
            <div className={`text-[22px] font-semibold leading-tight tabular-nums ${
                value == null ? 'text-slate-300' : SCORE_TEXT[scoreTone(value)] || 'text-slate-900'}`}>
                {value ?? '—'}
            </div>
            {sub && <div className="whitespace-nowrap text-[11px] text-slate-400">{sub}</div>}
        </div>
    );
}

const RETRIEVAL_LABEL = {
    ready: 'retrieval готов', ok: 'retrieval готов', complete: 'retrieval завершён', completed: 'retrieval завершён',
    degraded: 'retrieval ограничен', partial: 'retrieval частичный', stale: 'retrieval устарел',
    failed: 'ошибка retrieval', error: 'ошибка retrieval', unavailable: 'retrieval недоступен',
    disabled: 'retrieval отключён', skipped: 'retrieval пропущен',
};
const RETRIEVAL_PROBLEM = ['failed', 'error', 'unavailable', 'degraded', 'partial'];

/* Технические данные прогона (отпечаток, ревизия базы, retrieval) нужны при
 * разборе самой оценки, а не при проверке разговора — поэтому под «i», а не
 * рядом меток на каждой карточке. На виду остаётся только то, что меняет доверие
 * к оценке: устаревший снимок базы и сбой retrieval. */
const evaluationDetails = (evaluation) => {
    if (!evaluation) return null;
    const status = String(evaluation.retrieval_status || '').toLowerCase();
    const retrieved = evaluation.retrieved_count ?? evaluation.retrieved;
    const included = evaluation.included_count ?? evaluation.included;
    const parts = [
        evaluation.fingerprint_short && `Отпечаток оценки ${evaluation.fingerprint_short}`,
        evaluation.knowledge_revision != null && `база знаний r${evaluation.knowledge_revision}`,
        status && (RETRIEVAL_LABEL[status] || `retrieval: ${status}`),
        (retrieved != null || included != null) && `правил в промпте ${included ?? '—'} из ${retrieved ?? '—'}`,
        evaluation.retrieval_ms != null && `${Math.round(evaluation.retrieval_ms)} мс`,
    ].filter(Boolean);
    return parts.length ? `${parts.join(' · ')}.` : null;
};

/* Нижняя строка шапки: слева — о записи или переписке, справа — тревоги оценки и
 * «i» с техническими данными. */
function MetaRow({ evaluation, children }) {
    const details = evaluationDetails(evaluation);
    const status = String(evaluation?.retrieval_status || '').toLowerCase();
    return (
        <div className="flex items-center gap-3 px-4 py-2.5 text-[12px] text-slate-500 sm:px-5">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">{children}</div>
            {evaluation?.stale && (
                <IosBadge tone="amber" className="shrink-0 !px-2 !py-0.5"><AlertTriangle size={10} aria-hidden="true" />Устаревший снимок</IosBadge>
            )}
            {RETRIEVAL_PROBLEM.includes(status) && (
                <IosBadge tone="amber" className="shrink-0 !px-2 !py-0.5">{RETRIEVAL_LABEL[status]}</IosBadge>
            )}
            {details && <IosHint text={details} label="Технические данные оценки" align="right" />}
        </div>
    );
}

const LANGUAGE = { ru: 'русский', kk: 'казахский', en: 'английский', uz: 'узбекский', ky: 'киргизский', tr: 'турецкий' };

const CALL_END_LABEL = {
    operator: 'Завершил оператор',
    client: 'Завершил водитель',
    system: 'Завершено системой',
    transfer: 'Перевод звонка',
};

/* Звонок: кто положил трубку, на каком языке говорили и насколько уверенно
   распознано. Сторону завершения видит и ИИ при оценке; неизвестную не
   показываем — у исходящих ОП её нет никогда, и строка была бы на каждой карточке. */
function CallMeta({ call }) {
    const endLabel = CALL_END_LABEL[call.call_end_party];
    const langs = Object.entries(call.languages || {}).sort((a, b) => b[1] - a[1]);
    const text = langs.length === 1 ? LANGUAGE[langs[0][0]] || langs[0][0].toUpperCase()
        : langs.map(([code, pct]) => `${LANGUAGE[code] || code.toUpperCase()} ${pct}%`).join(', ');
    return (
        <MetaRow evaluation={call.evaluation}>
            {endLabel && (
                <span className="inline-flex items-center gap-1.5" title="По данным телефонии">
                    <PhoneOff size={13} className="shrink-0 text-slate-400" aria-hidden="true" />
                    {endLabel}
                </span>
            )}
            {text && (
                <span className="inline-flex min-w-0 items-center gap-1.5">
                    <Languages size={13} className="shrink-0 text-slate-400" aria-hidden="true" />
                    <span className="truncate">{text}</span>
                </span>
            )}
            {call.asr_mean_conf != null && <span>распознавание {Math.round(call.asr_mean_conf * 100)}%</span>}
        </MetaRow>
    );
}

/* Переписка вместо языков/уверенности ASR: проверяющему важны клиент, доля
 * ответов оцениваемого оператора (порог атрибуции) и судьба вложений. Цветом —
 * только то, что тревожит: доля ниже порога и непрочитанные вложения. */
function ChatMeta({ call }) {
    const chat = call.chat || {};
    const media = call.media || {};
    const share = chat.operator_share != null ? Math.round(Number(chat.operator_share) * 100) : null;
    const expired = media.source === 'expired';
    return (
        <>
            <MetaRow evaluation={call.evaluation}>
                <span className="inline-flex min-w-0 items-center gap-1.5">
                    <MessageSquare size={13} className="shrink-0 text-slate-400" aria-hidden="true" />
                    <span className="truncate">{chat.contact_name || chat.contact_phone || 'клиент без имени'}</span>
                </span>
                {chat.messages_count != null && <span>{chat.messages_count} сообщений</span>}
                {share != null && (
                    <span className={share >= 90 ? '' : 'font-medium text-amber-700'}
                          title="Доля ответов оцениваемого оператора среди всех ответов сотрудников в эпизоде">
                        ответы оператора {share}%
                    </span>
                )}
                {media.total ? (
                    <span className="inline-flex items-center gap-1">
                        <Paperclip size={12} className="text-slate-400" aria-hidden="true" />
                        вложений {media.total}, прочитано {media.ready || 0}
                        {media.failed ? <span className="font-medium text-amber-700">, не прочитано {media.failed}</span> : null}
                    </span>
                ) : null}
            </MetaRow>
            {expired && (
                <p className="bg-amber-50/70 px-4 py-2.5 text-[12px] text-amber-800 sm:px-5">
                    Сырые сообщения этого чата уже удалены ретеншном (45 дней): содержимое
                    вложений недоступно, оценка сделана по тексту переписки. Не штрафуйте
                    оператора за то, чего не видно.
                </p>
            )}
        </>
    );
}

export default function CallReviewCard({ call, onSave, onSkip, onRefine, onInteractionChange,
                                         canCorrectJournal = false }) {
    const audioRef = useRef(null);

    const seekAudio = useCallback((startMs) => {
        if (!audioRef.current) return;
        audioRef.current.currentTime = Math.max(0, Number(startMs || 0) / 1000);
        audioRef.current.focus();
    }, []);

    // Все хуки — до раннего return: иначе появление call между рендерами меняет
    // количество хуков и React падает («Rendered more hooks…»).
    // Текст для предпроверки цитаты обязан совпадать с авторитетным транскриптом,
    // по которому сервер валидирует разбор. У строк чата время входит в строку
    // («[26.07 21:17] Оператор (…): …»), и модель цитирует её вместе с ним —
    // без префикса предпроверка отвергала бы цитату, которую сервер принимает.
    const transcriptText = useMemo(
        () => (call?.transcript || [])
            .map((line) => {
                const body = (line.seg || []).map((seg) => seg.t || '').join('');
                return line.ts ? `[${line.ts}] ${body}` : body;
            })
            .join('\n'),
        [call],
    );
    const chatSnapshot = useMemo(
        () => (isChatSubject(call?.subject_kind) && call?.transcript?.length
            ? chatLinesToSnapshot(call.transcript, call.operator) : null),
        [call],
    );
    if (!call) return null;

    const isChat = isChatSubject(call.subject_kind);
    const journal = call.human_review || null;
    const myReview = call.my_review || null;
    // «Моя» показывается отдельно от «Человек» только когда это разные оценки:
    // если строку журнала сделал сам проверяющий, второй бейдж дублировал бы первый.
    const showMyBadge = myReview?.score != null && !(journal && journal.is_mine);

    return (
        /* На широком экране карточка занимает всю высоту, отданную контейнером
           (CallQaView меряет её до низа видимой области), и две колонки
           прокручиваются НЕЗАВИСИМО: слева запись с транскриптом, справа —
           оценка. Раньше скроллилась вся страница, и, дойдя до последних
           критериев, человек терял и плеер, и начало транскрипта. Скролл у
           колонок свой, тонкий (thin-scroll) и без рамок: рамки есть у
           карточек внутри, вторая вокруг области прокрутки была бы шумом. На
           телефоне колонка одна, и страница едет как прежде — там `lg:`-классы
           не действуют, а sticky-панели держатся за прокрутчик страницы. */
        <div style={{ fontFamily: APPLE_FONT }} className="grid grid-cols-1 gap-4 lg:h-full lg:min-h-0 lg:grid-cols-[1.05fr_1fr]">
            {/* Прокручивается только транскрипт: собственный скролл у колонки
                давал второй, вложенный. Место транскрипту даёт низкая шапка. */}
            <div className="flex min-w-0 flex-col gap-3 lg:min-h-0">
                {/* Шапка: кто и когда, баллы справа, запись; ниже тонкими строками
                    через волосяные линии, как ячейки настроек iOS, — сделка
                    (свёрнута в строку) и сведения о записи. Технические данные
                    оценки — под «i». Шапка низкая намеренно: всё, что она не
                    занимает, достаётся транскрипту. */}
                <div className={`${iosCard} shrink-0 overflow-hidden`}>
                    <div className="px-4 pb-3.5 pt-4 sm:px-5">
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 pt-0.5">
                                <div className="flex items-center gap-2">
                                    {isChat && <MessageSquare size={16} className="shrink-0 text-blue-500" aria-hidden="true" />}
                                    <h2 className="truncate text-[18px] font-semibold leading-tight text-slate-900">
                                        {isChat ? `Чат #${call.id}` : `Звонок #${call.id}`}
                                    </h2>
                                </div>
                                <p className="mt-1 text-[13px] text-slate-500 sm:truncate">
                                    {[call.operator, call.direction, call.datetime].filter(Boolean).join(' · ')}
                                </p>
                            </div>
                            <div className="flex shrink-0 items-start gap-4 sm:gap-6">
                                {/* Балл намеренно зачитывает непроверяемые критерии; без
                                    пометки «ИИ 85» выглядит как полноценная оценка, хотя
                                    часть веса ИИ не проверял (у Верификаторов это
                                    «Регистрация», 30). */}
                                <ScoreStat label="ИИ" value={call.ai_score != null ? Math.round(call.ai_score) : null}
                                           sub={call.score_breakdown?.unchecked_weight > 0
                                               ? `${call.score_breakdown.unchecked_weight} не проверено` : null}
                                           title={call.score_breakdown?.unchecked_weight > 0
                                               ? 'ИИ не проверял: '
                                                 + (call.score_breakdown.unchecked || []).map((c) => `${c.name} (${c.weight})`).join(', ')
                                                 + '. Эти баллы зачтены по умолчанию — проверьте их вручную.'
                                               : 'Балл ИИ — все критерии проверены по транскрипту'} />
                                {call.human_score != null && (
                                    <ScoreStat label="Человек" value={Math.round(call.human_score)}
                                               title={journal ? `Оценка в журнале: ${journal.evaluator || '—'}${journal.datetime ? `, ${journal.datetime}` : ''}` : 'Оценка человека в журнале'} />
                                )}
                                {showMyBadge && (
                                    <ScoreStat label="Моя" value={Math.round(myReview.score)}
                                               title={myReview.counted_in_quality ? 'Моя оценка — учтена в журнале' : 'Моя оценка — калибровочная, в качество не идёт'} />
                                )}
                            </div>
                        </div>
                        {call.audio_url && (
                            <audio ref={audioRef} controls preload="none" src={call.audio_url} className="mt-3.5 h-10 w-full"
                                   aria-label={`Запись звонка ${call.id}`} />
                        )}
                    </div>
                    <div className="divide-y divide-slate-100 border-t border-slate-100">
                        {/* Сделка amoCRM этого разговора (ТЗ #317) — под строкой
                            заголовка, а не рядом с баллами: там её сжимало. Только
                            когда связь есть: пустая «сделка не найдена» на каждой
                            карточке СЗоВ — шум для отдела без сделок. */}
                        {call.deal && <DealBadge deal={call.deal} full />}
                        {isChat ? <ChatMeta call={call} /> : <CallMeta call={call} />}
                    </div>
                </div>

                {/* Транскрипт без карточки-рамки: реплики лежат прямо на фоне, как в
                    мессенджере, а прокручивается только их область. На широком
                    экране она добирает всю оставшуюся высоту колонки, на телефоне
                    ограничена 60vh, чтобы страница не превращалась в один транскрипт. */}
                <div className="flex min-h-0 flex-1 flex-col">
                    {/* Пояснение к подсветке — под «i» у заголовка: строкой под
                        транскриптом оно занимало место всё время, а нужно один раз. */}
                    <div className="flex items-center gap-1.5 px-1 pb-1.5">
                        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                            {isChat ? 'Переписка · эпизод' : 'Транскрипт · диаризация'}
                        </span>
                        <IosHint label={isChat ? 'Как читать переписку' : 'Что значит жёлтая подсветка'}
                                 text={isChat
                                     ? 'Фото открываются в лайтбоксе, голосовые — плеером; под ними серым — транскрипт или описание, которые видела модель.'
                                     : 'Жёлтым подсвечены слова, которые распознаны неуверенно: там ИИ не уверен в распознавании, и против оператора это не учитывается. Время у реплики — переход к этому месту записи.'} />
                    </div>
                    {chatSnapshot ? (
                        <ChatThread snapshot={chatSnapshot} quotes={[]}
                                    className="thin-scroll max-h-[60vh] rounded-2xl lg:max-h-none" />
                    ) : (
                        <div className="thin-scroll max-h-[60vh] min-h-0 flex-1 space-y-2 overflow-y-auto px-1 py-1 lg:max-h-none lg:pr-2" tabIndex={0}
                             aria-label={isChat ? 'Переписка эпизода' : 'Транскрипт звонка'}>
                            {(call.transcript || []).length > 0
                                ? call.transcript.map((l, i) => <TranscriptLine key={i} line={l} onSeek={call.audio_url ? seekAudio : undefined} />)
                                : <div className="flex min-h-32 items-center justify-center text-center text-[13px] text-slate-500">
                                    {isChat
                                        ? 'Переписка недоступна. Не подтверждайте оценку, пока данные не будут загружены.'
                                        : 'Транскрипт отсутствует. Не подтверждайте оценку, пока данные не будут загружены.'}
                                  </div>}
                        </div>
                    )}
                </div>
            </div>

            {/* Правая колонка — свой прокрутчик на широком экране: липкие панели
                (итог сверху, «Учитывать в качестве» и сохранение снизу) держатся за
                него, а не за страницу. overflow-x спрятан: подсказки «i» у правого
                края иначе дали бы горизонтальную полосу. */}
            <div className="thin-scroll flex min-w-0 flex-col lg:min-h-0 lg:overflow-y-auto lg:overflow-x-hidden lg:pr-1.5">
                <CriteriaReviewPanel call={call} transcriptText={transcriptText}
                                     canCorrectJournal={canCorrectJournal}
                                     onSave={onSave} onSkip={onSkip} onRefine={onRefine}
                                     onInteractionChange={onInteractionChange} />
            </div>
        </div>
    );
}
