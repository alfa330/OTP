/* Настройки раздела «Карточка клиента» — руководителю отдела (задача #335, п. 3, 5, 6, 9).
 *
 *   Карточка в телефоне   включить операторам, каким группам, время постобработки
 *   Причины обращений     дерево «основная причина → подтема → подтема подтемы…», которое видит оператор;
 *                         у водителей, клиентов и третьих лиц оно своё
 *   Отделы для запросов   куда уходит запрос из карточки: название и Telegram-группа
 *   Алерты                когда писать руководителю о росте обращений и куда
 *
 * Каждая правка сохраняется сразу — отдельной кнопки «Сохранить» на весь экран нет: настройки
 * независимы, и общая кнопка заставляла бы помнить, что именно ещё не записано.
 * Пояснения — под «i» у подписи, а не строкой под полем: они нужны один раз.
 */
import React, { useState } from 'react';
import {
    ArrowDown, ArrowUp, ChevronDown, Eye, EyeOff, Loader2, Pencil, Plus, Send, Trash2,
} from 'lucide-react';

import CustomSelect from '../ui/CustomSelect';
import { IosBadge, IosHint, IosSegmented, IosToggle, iosBtnGhost, iosBtnSecondary, iosCard, iosGroupLabel, iosInput } from '../ui/ios';
import { AUDIENCES, audienceLabel, errorText, fmtWhen, reasonsOfAudience } from './cardRules';

const Section = ({ title, hint = '', right = null, children }) => (
    <section className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
                <div className={iosGroupLabel}>{title}</div>
                {hint && <IosHint text={hint} label={`Подробнее: ${title}`} />}
            </div>
            {right}
        </div>
        <div className={`${iosCard} divide-y divide-slate-100`}>{children}</div>
    </section>
);

const Line = ({ label, hint = '', children }) => (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
        <div className="flex min-w-0 items-center gap-1.5 text-[13.5px] text-slate-800">
            <span>{label}</span>
            {hint && <IosHint text={hint} label={`Подробнее: ${label}`} />}
        </div>
        <div className="flex min-w-0 items-center gap-2">{children}</div>
    </div>
);

/* Число, которое сохраняется, когда поле покинули или нажали Enter: запрос на каждую цифру ни к чему. */
function NumberField({ value, onCommit, min, max, step = 1, suffix = '', width = 'w-20', ariaLabel }) {
    const [text, setText] = useState(String(value));
    const [seen, setSeen] = useState(value);
    if (seen !== value) { setSeen(value); setText(String(value)); }
    const commit = () => {
        const number = Number(String(text).replace(',', '.'));
        if (!Number.isFinite(number)) { setText(String(value)); return; }
        const clamped = Math.max(min, Math.min(max, number));
        setText(String(clamped));
        if (clamped !== value) onCommit(clamped);
    };
    return (
        <span className="flex items-center gap-1.5">
            <input value={text} onChange={(event) => setText(event.target.value)} onBlur={commit} inputMode="decimal"
                   onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
                   step={step} aria-label={ariaLabel} className={`${iosInput} ${width} py-1.5 text-right tabular-nums`} />
            {suffix && <span className="text-[12.5px] text-slate-500">{suffix}</span>}
        </span>
    );
}

// Отступ уровня в дереве причин: подтема стоит под своей причиной.
const LEVEL_INDENT = 24;
const levelPad = (depth) => ({ paddingLeft: 12 + depth * LEVEL_INDENT });

/* Строка справочника: название (правится на месте), порядок, скрыть, удалить.
   depth — уровень в дереве причин: 0 — основная причина. */
function NameRow({ item, busy, depth = 0, first, last, onRename, onMove, onToggle, onDelete, children = null }) {
    const [editing, setEditing] = useState(false);
    const [text, setText] = useState(item.title);
    const save = () => {
        const next = text.trim();
        setEditing(false);
        if (next && next !== item.title) onRename(next); else setText(item.title);
    };
    const iconBtn = 'grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30';
    return (
        <div className="flex items-center gap-1.5 py-2 pr-2" style={levelPad(depth)}>
            {children}
            {editing ? (
                <input value={text} autoFocus onChange={(event) => setText(event.target.value)} onBlur={save}
                       onKeyDown={(event) => {
                           if (event.key === 'Enter') save();
                           if (event.key === 'Escape') { setText(item.title); setEditing(false); }
                       }}
                       className={`${iosInput} min-w-0 flex-1 py-1.5`} aria-label="Название" />
            ) : (
                <span className={`min-w-0 flex-1 truncate text-[13.5px] ${item.is_active ? (depth ? 'text-slate-700' : 'font-medium text-slate-900') : 'text-slate-400 line-through'}`}>
                    {item.title}
                </span>
            )}
            {!item.is_active && <IosBadge>Скрыта</IosBadge>}
            <button type="button" className={iconBtn} disabled={busy} onClick={() => setEditing(true)} aria-label={`Переименовать «${item.title}»`} title="Переименовать">
                <Pencil size={13} />
            </button>
            <button type="button" className={iconBtn} disabled={busy || first} onClick={() => onMove(-1)} aria-label="Выше" title="Выше"><ArrowUp size={14} /></button>
            <button type="button" className={iconBtn} disabled={busy || last} onClick={() => onMove(1)} aria-label="Ниже" title="Ниже"><ArrowDown size={14} /></button>
            <button type="button" className={iconBtn} disabled={busy} onClick={onToggle}
                    aria-label={item.is_active ? 'Скрыть от операторов' : 'Показать операторам'}
                    title={item.is_active ? 'Скрыть от операторов' : 'Показать операторам'}>
                {item.is_active ? <Eye size={14} /> : <EyeOff size={14} />}
            </button>
            {/* С обращениями причину удалить нельзя — только скрыть: отчёт за прошлые месяцы потерял бы строки. */}
            <button type="button" className={`${iconBtn} hover:!bg-rose-50 hover:!text-rose-600`} disabled={busy || item.used > 0}
                    onClick={onDelete} aria-label={`Удалить «${item.title}»`}
                    title={item.used > 0 ? `По причине или её подтемам есть обращения (${item.used}) — её можно только скрыть`
                        : item.children?.length ? 'Удалить вместе с подтемами' : 'Удалить'}>
                <Trash2 size={13} />
            </button>
        </div>
    );
}

function AddLine({ placeholder, busy, onAdd, depth = 0 }) {
    const [text, setText] = useState('');
    const submit = () => {
        // Вставленный список — по строке на название.
        const titles = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
        if (!titles.length) return;
        onAdd(titles).then((ok) => { if (ok) setText(''); });
    };
    return (
        <div className="flex items-start gap-2 py-2 pr-3" style={levelPad(depth)}>
            <textarea value={text} rows={1} onChange={(event) => setText(event.target.value)} placeholder={placeholder}
                      onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit(); } }}
                      className={`${iosInput} min-h-[36px] flex-1 resize-none py-1.5`} />
            <button type="button" onClick={submit} disabled={busy || !text.trim()} className={`${iosBtnSecondary} py-1.5`}>
                <Plus size={14} /> Добавить
            </button>
        </div>
    );
}

/* Причина со своей веткой в редакторе: строка, под ней — подтемы и поле «новая подтема».
   Подтема добавляется под причину любого уровня, пока не достигнут потолок глубины
   (maxDepth уровней; на последнем раскрывать нечего). */
function ReasonNode({ node, siblings, index, depth, parentId, maxDepth, busy, opened, onOpen, actions }) {
    const children = node.children || [];
    const canNest = depth + 1 < maxDepth;
    const open = canNest && opened.has(node.id);
    return (
        <div>
            <NameRow item={node} depth={depth} busy={busy} first={index === 0} last={index === siblings.length - 1}
                     onRename={(title) => actions.update(node.id, { title })}
                     onMove={(delta) => actions.move(siblings, index, delta, parentId)}
                     onToggle={() => actions.update(node.id, { is_active: !node.is_active })}
                     onDelete={() => actions.remove(node.id)}>
                {canNest && (
                    <button type="button" onClick={() => onOpen(node.id)} aria-expanded={open}
                            aria-label={`Подтемы причины «${node.title}»`}
                            className="flex h-7 shrink-0 items-center gap-1 rounded-lg px-1.5 text-[12px] tabular-nums text-slate-500 hover:bg-slate-100">
                        <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
                        {children.length}
                    </button>
                )}
            </NameRow>
            {open && (
                <div className="border-t border-slate-100 bg-slate-50/60">
                    {children.map((child, childIndex) => (
                        <ReasonNode key={child.id} node={child} siblings={children} index={childIndex} depth={depth + 1}
                                    parentId={node.id} maxDepth={maxDepth} busy={busy} opened={opened} onOpen={onOpen}
                                    actions={actions} />
                    ))}
                    <AddLine depth={depth + 1} placeholder={`Новая подтема в «${node.title}»`} busy={busy}
                             onAdd={(titles) => actions.add(node.id, titles)} />
                </div>
            )}
        </div>
    );
}

const WINDOW_OPTIONS = [
    { value: 30, label: '30 мин' }, { value: 60, label: '1 час' }, { value: 120, label: '2 часа' }, { value: 180, label: '3 часа' },
];

export default function SettingsTab({ api, boot, toast, onBoot, onReload }) {
    const departmentId = boot.department_id;
    const settings = boot.settings;
    const [busy, setBusy] = useState('');
    // Раскрытые ветки дерева причин: в редакторе их может быть открыто несколько сразу.
    const [opened, setOpened] = useState(() => new Set());
    // Чьи причины открыты в редакторе: у водителей, клиентов и третьих лиц свои ветки.
    const [audience, setAudience] = useState(AUDIENCES[0].value);
    const [provideId, setProvideId] = useState('');

    const run = async (name, work, apply) => {
        if (busy) return false;
        setBusy(name);
        try {
            const { data } = await work();
            if (apply) onBoot(apply(data));
            return data || true;
        } catch (error) {
            toast(errorText(error), 'error');
            return false;
        } finally {
            setBusy('');
        }
    };
    const patch = (body) => run('settings', () => api.saveSettings({ department_id: departmentId, ...body }),
        (data) => ({ settings: data.settings }));
    const categoriesApply = (data) => ({ categories: data.categories });
    const destinationsApply = (data) => ({ destinations: data.destinations });

    const move = (list, index, delta, parentId) => {
        const next = [...list];
        const target = index + delta;
        if (target < 0 || target >= next.length) return;
        [next[index], next[target]] = [next[target], next[index]];
        run('reorder', () => api.reorderCategories({ department_id: departmentId, parent_id: parentId, ids: next.map((item) => item.id) }),
            categoriesApply);
    };

    const toggleOpened = (id) => setOpened((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });
    const reasonActions = {
        update: (id, body) => run('category', () => api.updateCategory(id, body), categoriesApply),
        remove: (id) => run('category', () => api.deleteCategory(id), categoriesApply),
        add: (parentId, titles) => run('category', () => api.createCategory(
            { department_id: departmentId, parent_id: parentId, titles }), categoriesApply),
        move,
    };
    const reasonDepth = boot.limits?.category_depth || 5;
    const reasons = reasonsOfAudience(boot.categories, audience);

    const chatOptions = [{ value: '', label: 'Группа не выбрана' },
        ...(boot.chats || []).map((chat) => ({ value: String(chat.chat_id), label: chat.title }))];
    const groupOptions = (boot.groups || []).map((group) => ({ value: String(group.id), label: group.name }));
    const alerts = settings.alerts;
    const head = boot.head;
    const provideOptions = (boot.all_departments || []).filter((item) => !item.provided)
        .map((item) => ({ value: String(item.id), label: item.name }));

    return (
        // На всю ширину (владелец, 13.11.2026). Одна колонка растянула бы подпись и её поле по
        // разным краям экрана, поэтому на широком экране колонок две: слева — что видит оператор
        // (карточка и причины), справа — куда уходят запросы и алерты.
        <div className="grid w-full grid-cols-1 items-start gap-5 xl:grid-cols-2">
            <div className="min-w-0 space-y-5">
            {/* ── Карточка в телефоне ───────────────────────────────────────── */}
            <Section title="Карточка в iCORE Phone"
                     hint="Когда включено, телефон оператора сам открывает карточку на каждый входящий и исходящий звонок: номер уже подставлен, аккаунты найдены.">
                <Line label="Открывать карточку операторам"
                      hint="Сначала заведите причины обращений и отделы для запросов — оператор увидит их сразу после включения.">
                    <IosToggle checked={settings.enabled} disabled={Boolean(busy)} onChange={(enabled) => patch({ enabled })} />
                </Line>
                <Line label="Кому" hint="Группы не выбраны — карточка открывается всем операторам отдела. Руководителю она открывается всегда.">
                    <CustomSelect variant="ios" multiple value={settings.group_ids.map(String)} options={groupOptions}
                                  onChange={(ids) => patch({ group_ids: ids.map(Number) })} placeholder="Всему отделу"
                                  className="w-64 max-w-full" ariaLabel="Группы, которым открывается карточка" disabled={Boolean(busy)} />
                </Line>
                <Line label="Постобработка после звонка"
                      hint="Сколько секунд после конца разговора даётся оператору, чтобы дозаполнить карточку и отправить запрос. В это время новые звонки ему не поступают; сохранил карточку раньше — постобработка кончается сразу. 0 — без постобработки.">
                    <NumberField value={settings.wrapup_sec} min={0} max={boot.limits?.wrapup_max_sec || 900} suffix="с"
                                 ariaLabel="Постобработка, секунд" onCommit={(wrapup_sec) => patch({ wrapup_sec })} />
                </Line>
            </Section>

            {/* ── Причины обращений ─────────────────────────────────────────── */}
            <Section title="Причины обращений"
                     hint={`У водителей, клиентов и третьих лиц свои причины — переключатель справа. Оператор видит причины того, чей аккаунт выбран в карточке; если звонит не сам водитель или клиент, переключает на «Третье лицо»; если отмечено «Аккаунта нет», сам называет, кто звонит. Причины — дерево: основная причина (например, «Запрос», «Консультация», «Жалоба», «Предложение») → подтема → подтема подтемы, до ${reasonDepth} уровней; оператор выбирает последний уровень своей ветки. По причинам строятся отчёт и алерты. Скрытую причину оператор не видит вместе с её подтемами, но в прошлых обращениях она остаётся.`}
                     right={<IosSegmented size="xs" value={audience} options={AUDIENCES} onChange={setAudience} ariaLabel="Чьи причины" />}>
                {reasons.map((category, index) => (
                    <ReasonNode key={category.id} node={category} siblings={reasons} index={index} depth={0}
                                parentId={null} maxDepth={reasonDepth} busy={Boolean(busy)} opened={opened}
                                onOpen={toggleOpened} actions={reasonActions} />
                ))}
                <AddLine key={audience} busy={Boolean(busy)}
                         placeholder={`Новая основная причина ${AUDIENCES.find((item) => item.value === audience)?.whose} — или вставьте список, по строке на причину`}
                         onAdd={(titles) => run('category', () => api.createCategory({ department_id: departmentId, audience, titles }), categoriesApply)} />
            </Section>
            </div>

            <div className="min-w-0 space-y-5">
            {/* ── Отделы для запросов ───────────────────────────────────────── */}
            <Section title="Отделы для запросов"
                     hint="Куда оператор отправляет запрос из карточки. Запрос уходит сообщением бота в Telegram-группу отдела; ответ реплаем на него возвращается в карточку. Группы нет в списке — добавьте в неё бота портала.">
                {boot.destinations.map((item) => (
                    <DestinationRow key={item.id} item={item} busy={Boolean(busy)} chatOptions={chatOptions}
                                    onPatch={(body) => run('destination', () => api.updateDestination(item.id, body), destinationsApply)}
                                    onDelete={() => run('destination', () => api.deleteDestination(item.id), destinationsApply)} />
                ))}
                <AddLine placeholder="Название отдела, например «Финансы»" busy={Boolean(busy)}
                         onAdd={async (titles) => {
                             let ok = true;
                             for (const title of titles) {
                                 // По одному: у адресата, в отличие от причины, дальше выбирают группу.
                                 // eslint-disable-next-line no-await-in-loop
                                 ok = Boolean(await run('destination', () => api.createDestination({ department_id: departmentId, title }), destinationsApply)) && ok;
                             }
                             return ok;
                         }} />
            </Section>

            {/* ── Алерты ────────────────────────────────────────────────────── */}
            <Section title="Алерты о росте обращений"
                     hint="Когда по одной причине за короткое время обращается заметно больше людей, чем обычно, портал пишет в Telegram. Считаются разные люди, а не звонки; о той же причине сообщение не повторяется, пока обращений не станет ещё заметно больше или не пройдёт пауза."
                     right={(
                         <button type="button" disabled={Boolean(busy)} className={iosBtnGhost}
                                 onClick={async () => {
                                     const data = await run('test', () => api.testAlert(departmentId));
                                     if (!data) return;
                                     if (data.errors?.length) toast(`Не дошло: ${data.errors.join('; ')}`, 'error');
                                     else toast(`Проверочное сообщение отправлено: ${data.sent.join(', ')}`, 'success');
                                 }}>
                             {busy === 'test' ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />} Проверить
                         </button>
                     )}>
                <Line label="Присылать алерты">
                    <IosToggle checked={alerts.enabled} disabled={Boolean(busy)} onChange={(enabled) => patch({ alerts: { ...alerts, enabled } })} />
                </Line>
                <Line label="Главе отдела в личные сообщения"
                      hint={head ? (head.has_telegram ? `${head.name}: Telegram подключён.` : `${head.name}: Telegram не подключён к порталу — сообщение не дойдёт.`) : 'У отдела не назначен глава.'}>
                    {head && !head.has_telegram && settings.alert_head && <IosBadge tone="amber">Telegram не подключён</IosBadge>}
                    <IosToggle checked={settings.alert_head} disabled={Boolean(busy)} onChange={(alert_head) => patch({ alert_head })} />
                </Line>
                <Line label="В Telegram-группу" hint="Группа, где состоит бот портала. Можно оставить пустой — тогда алерт уходит только главе.">
                    <CustomSelect variant="ios" searchable value={settings.alert_chat_id ? String(settings.alert_chat_id) : ''}
                                  options={chatOptions} onChange={(value) => patch({ alert_chat_id: value ? Number(value) : null })}
                                  className="w-64 max-w-full" ariaLabel="Группа для алертов" disabled={Boolean(busy)} />
                </Line>
                <Line label="Считать обращения за" hint="Окно, в котором считаются обращения по причине. «Обычно» — среднее за то же окно часов в прошлые 14 дней.">
                    <IosSegmented value={alerts.window_min} options={WINDOW_OPTIONS} ariaLabel="Окно подсчёта"
                                  onChange={(window_min) => patch({ alerts: { ...alerts, window_min } })} />
                </Line>
                <Line label="Не меньше обращений" hint="Нижняя планка: меньше стольких человек за окно — не алерт, как бы ни выросло. Защищает от ложных тревог на редких причинах.">
                    <NumberField value={alerts.min_count} min={2} max={500} suffix="чел." ariaLabel="Минимум обращений"
                                 onCommit={(min_count) => patch({ alerts: { ...alerts, min_count } })} />
                </Line>
                <Line label="Во сколько раз больше обычного" hint="Алерт уходит, когда обращений не меньше планки И не меньше обычного, умноженного на это число.">
                    <NumberField value={alerts.factor} min={1.2} max={10} step={0.1} suffix="×" ariaLabel="Рост к обычному"
                                 onCommit={(factor) => patch({ alerts: { ...alerts, factor } })} />
                </Line>
                <Line label="Пауза между алертами по одной причине" hint="Раньше этого срока сообщение по той же причине придёт, только если обращений стало ещё в полтора раза больше.">
                    <NumberField value={alerts.cooldown_min} min={15} max={1440} suffix="мин" ariaLabel="Пауза, минут"
                                 onCommit={(cooldown_min) => patch({ alerts: { ...alerts, cooldown_min } })} />
                </Line>
                <Line label="Не больше алертов по причине за сутки">
                    <NumberField value={alerts.daily_limit} min={1} max={24} ariaLabel="Предел за сутки"
                                 onCommit={(daily_limit) => patch({ alerts: { ...alerts, daily_limit } })} />
                </Line>
                {boot.alerts_log?.length > 0 && (
                    <div className="px-4 py-3">
                        <div className="mb-1.5 text-[12px] font-medium text-slate-500">Последние алерты</div>
                        <ul className="space-y-1 text-[12.5px] text-slate-700">
                            {boot.alerts_log.slice(0, 8).map((item) => (
                                <li key={item.id} className="flex items-center justify-between gap-3">
                                    <span className="min-w-0 truncate">
                                        {item.audience ? `${audienceLabel(item.audience)} · ` : ''}{item.parent_title ? `${item.parent_title} → ` : ''}{item.title}
                                        <span className="text-slate-400"> · {item.count} чел., обычно {Math.round(item.baseline)}</span>
                                    </span>
                                    <span className="flex shrink-0 items-center gap-2 tabular-nums text-slate-400">
                                        {!item.delivered && <IosBadge tone="red">Не доставлен</IosBadge>}
                                        {fmtWhen(item.sent_at)}
                                    </span>
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </Section>

            {/* ── Выдать раздел отделу — администратору компании ────────────── */}
            {boot.all_departments && provideOptions.length > 0 && (
                <Section title="Выдать раздел отделу"
                         hint="Раздел появится у руководителя отдела выключенным: он заведёт причины и адресатов и сам включит карточку операторам.">
                    <div className="flex flex-wrap items-center gap-2 px-4 py-3">
                        <CustomSelect variant="ios" searchable value={provideId} onChange={setProvideId} options={provideOptions}
                                      placeholder="Выберите отдел" className="w-64 max-w-full" ariaLabel="Отдел" />
                        <button type="button" disabled={Boolean(busy) || !provideId} className={iosBtnSecondary}
                                onClick={async () => {
                                    const ok = await run('provide', () => api.provideDepartment(Number(provideId)));
                                    if (ok) { setProvideId(''); toast('Раздел выдан отделу', 'success'); onReload(); }
                                }}>
                            Выдать
                        </button>
                    </div>
                </Section>
            )}
            </div>
        </div>
    );
}

function DestinationRow({ item, busy, chatOptions, onPatch, onDelete }) {
    const [editing, setEditing] = useState(false);
    const [title, setTitle] = useState(item.title);
    const [mentions, setMentions] = useState((item.mention_usernames || []).map((name) => `@${name}`).join(' '));
    const [seen, setSeen] = useState(item);
    if (seen !== item) {
        setSeen(item);
        setTitle(item.title);
        setMentions((item.mention_usernames || []).map((name) => `@${name}`).join(' '));
    }
    const saveTitle = () => {
        setEditing(false);
        const next = title.trim();
        if (next && next !== item.title) onPatch({ title: next }); else setTitle(item.title);
    };
    const saveMentions = () => {
        const current = (item.mention_usernames || []).map((name) => `@${name}`).join(' ');
        if (mentions.trim() !== current) onPatch({ mention_usernames: mentions });
    };
    const iconBtn = 'grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30';
    return (
        <div className="space-y-2 px-3 py-3">
            <div className="flex items-center gap-1.5">
                {editing ? (
                    <input value={title} autoFocus onChange={(event) => setTitle(event.target.value)} onBlur={saveTitle}
                           onKeyDown={(event) => {
                               if (event.key === 'Enter') saveTitle();
                               if (event.key === 'Escape') { setTitle(item.title); setEditing(false); }
                           }}
                           className={`${iosInput} min-w-0 flex-1 py-1.5`} aria-label="Название отдела" />
                ) : (
                    <span className={`min-w-0 flex-1 truncate px-1 text-[13.5px] font-medium ${item.is_active ? 'text-slate-900' : 'text-slate-400 line-through'}`}>
                        {item.title}
                    </span>
                )}
                {!item.is_ready && item.is_active && <IosBadge tone="amber">Нет группы</IosBadge>}
                {!item.is_active && <IosBadge>Скрыт</IosBadge>}
                <button type="button" className={iconBtn} disabled={busy} onClick={() => setEditing(true)} aria-label={`Переименовать «${item.title}»`} title="Переименовать">
                    <Pencil size={13} />
                </button>
                <button type="button" className={iconBtn} disabled={busy} onClick={() => onPatch({ is_active: !item.is_active })}
                        aria-label={item.is_active ? 'Скрыть от операторов' : 'Показать операторам'}
                        title={item.is_active ? 'Скрыть от операторов' : 'Показать операторам'}>
                    {item.is_active ? <Eye size={14} /> : <EyeOff size={14} />}
                </button>
                <button type="button" className={`${iconBtn} hover:!bg-rose-50 hover:!text-rose-600`} disabled={busy} onClick={onDelete}
                        aria-label={`Удалить «${item.title}»`} title="Удалить — если в отдел ещё не уходили запросы">
                    <Trash2 size={13} />
                </button>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <CustomSelect variant="ios" searchable value={item.chat_id ? String(item.chat_id) : ''} options={chatOptions}
                              onChange={(value) => onPatch({ chat_id: value ? Number(value) : null })}
                              ariaLabel={`Telegram-группа отдела «${item.title}»`} disabled={busy} />
                <input value={mentions} onChange={(event) => setMentions(event.target.value)} onBlur={saveMentions}
                       onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
                       placeholder="Кого отметить: @ник @ник" className={`${iosInput} py-2`}
                       aria-label={`Кого бот отмечает в группе отдела «${item.title}»`} />
            </div>
        </div>
    );
}
