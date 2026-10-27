import React, { useCallback, useEffect, useState } from 'react';
import { History, Loader2, Pencil, Plus, Trash2 } from 'lucide-react';
import { IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput } from '../ui/ios';
import { errorOf, shortTime } from './rentalMeta';
import { ICON_BTN, Loading, TOOLBAR_BTN } from './rentalUi';

/*
 * Вкладка «Условия работы» (#391, ТЗ 7 и 10.3) — единая памятка для консультации.
 *
 * Текст ведут сотрудники аренды и руководители прямо здесь; операторы ОП только читают.
 * Каждое сохранение — новая версия целиком, прежние не правятся и не удаляются, любую можно
 * открыть. Первая версия — из макета Зарины. Блок — заголовок и текст; «Список» — каждая
 * строка отдельным пунктом, «Текст» — абзацы через пустую строку.
 */

const KIND_OPTIONS = [{ value: 'list', label: 'Список' }, { value: 'text', label: 'Текст' }];

const Block = ({ block }) => {
    const lines = block.text.split('\n').map((line) => line.trim()).filter(Boolean);
    return (
        <section className={`${iosCard} p-4`}>
            <h3 className="mb-2.5 text-[15px] font-semibold text-slate-900">{block.title}</h3>
            {block.kind === 'list' ? (
                <ul className="space-y-1.5 text-[13.5px] leading-snug text-slate-700">
                    {lines.map((line, index) => (
                        <li key={index} className="flex gap-2">
                            <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
                            <span>{line}</span>
                        </li>
                    ))}
                </ul>
            ) : (
                <div className="space-y-2 text-[13.5px] leading-relaxed text-slate-700">
                    {block.text.split(/\n\s*\n/).map((paragraph, index) => <p key={index}>{paragraph.trim()}</p>)}
                </div>
            )}
        </section>
    );
};

const EditorModal = ({ blocks, onClose, onSave }) => {
    const [draft, setDraft] = useState(() => blocks.map((block) => ({ ...block })));
    const [saving, setSaving] = useState(false);
    const change = (index, patch) => setDraft((prev) => prev.map((block, i) => (i === index ? { ...block, ...patch } : block)));
    const save = async () => {
        setSaving(true);
        try {
            await onSave(draft);
        } finally {
            setSaving(false);
        }
    };
    return (
        <IosModal
            open
            onClose={onClose}
            title="Изменить условия работы"
            subtitle="Сохранится новой версией — прежняя останется в истории"
            maxWidth="max-w-2xl"
            footer={(
                <div className="flex justify-end gap-2">
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                    <button type="button" className={iosBtnPrimary} disabled={saving} onClick={save}>
                        {saving ? <Loader2 size={15} className="animate-spin" /> : null} Сохранить
                    </button>
                </div>
            )}
        >
            <div className="space-y-3">
                {draft.map((block, index) => (
                    <div key={index} className="space-y-2 rounded-2xl bg-slate-50 p-3">
                        <div className="flex items-center gap-2">
                            <input className={`${iosInput} !bg-white font-medium`} value={block.title} placeholder="Заголовок блока"
                                   onChange={(event) => change(index, { title: event.target.value })} />
                            <IosSegmented value={block.kind} onChange={(kind) => change(index, { kind })} options={KIND_OPTIONS}
                                          size="xs" ariaLabel="Вид блока" />
                            <button type="button" className={ICON_BTN} aria-label="Убрать блок"
                                    onClick={() => setDraft((prev) => prev.filter((_, i) => i !== index))}>
                                <Trash2 size={15} />
                            </button>
                        </div>
                        <textarea className={`${iosInput} min-h-[120px] resize-y !bg-white`} value={block.text}
                                  placeholder={block.kind === 'list' ? 'Каждая строка — отдельный пункт' : 'Абзацы — через пустую строку'}
                                  onChange={(event) => change(index, { text: event.target.value })} />
                    </div>
                ))}
                <button type="button" className={TOOLBAR_BTN}
                        onClick={() => setDraft((prev) => [...prev, { title: '', kind: 'list', text: '' }])}>
                    <Plus size={15} /> Блок
                </button>
            </div>
        </IosModal>
    );
};

const ConditionsTab = ({ api, toast }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [viewing, setViewing] = useState(null);
    const [versionsOpen, setVersionsOpen] = useState(false);
    const [editing, setEditing] = useState(false);

    const load = useCallback(async () => {
        try {
            const { data: conditions } = await api.get('/conditions');
            setData(conditions);
            setError('');
        } catch (requestError) {
            setError(errorOf(requestError, 'Условия не открылись — попробуйте обновить страницу'));
        }
    }, [api]);

    useEffect(() => { load(); }, [load]);

    const openVersion = async (version) => {
        setVersionsOpen(false);
        if (data?.current && version.id === data.current.id) {
            setViewing(null);
            return;
        }
        try {
            const { data: result } = await api.get(`/conditions/${version.id}`);
            setViewing(result.version);
        } catch (requestError) {
            toast(errorOf(requestError, 'Версия не открылась'), 'error');
        }
    };

    const save = async (blocks) => {
        try {
            await api.post('/conditions', { blocks });
            toast('Условия сохранены', 'success');
            setEditing(false);
            setViewing(null);
            load();
        } catch (requestError) {
            toast(errorOf(requestError, 'Не сохранилось'), 'error');
        }
    };

    if (error && !data) return <div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>;
    if (!data) return <Loading text="Открываем условия…" />;
    const shown = viewing || data.current;
    if (!shown) return <div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>Условия ещё не заполнены</div>;

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="text-[13px] text-slate-600">
                    {viewing
                        ? <>Версия от {shortTime(viewing.at)}{viewing.by ? `, ${viewing.by}` : ''} — <button type="button" className="font-medium text-blue-600 hover:underline" onClick={() => setViewing(null)}>вернуться к текущей</button></>
                        : <>Обновлено {shortTime(data.current.at)}{data.current.by ? `, ${data.current.by}` : ''}</>}
                </div>
                <div className="flex items-center gap-2">
                    {data.versions.length > 1 && (
                        <button type="button" className={TOOLBAR_BTN} onClick={() => setVersionsOpen(true)}>
                            <History size={15} /> Версии
                        </button>
                    )}
                    {data.can_edit && !viewing && (
                        <button type="button" className={TOOLBAR_BTN} onClick={() => setEditing(true)}>
                            <Pencil size={15} /> Изменить
                        </button>
                    )}
                </div>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
                {shown.blocks.map((block, index) => <Block key={`${shown.id}-${index}`} block={block} />)}
            </div>

            {versionsOpen && (
                <IosModal open onClose={() => setVersionsOpen(false)} title="Версии условий" subtitle="Прежние версии не правятся и не удаляются">
                    <ul className="divide-y divide-slate-100">
                        {data.versions.map((version, index) => (
                            <li key={version.id}>
                                <button type="button" onClick={() => openVersion(version)}
                                        className="flex w-full items-center justify-between gap-3 rounded-xl px-2 py-2.5 text-left hover:bg-slate-50">
                                    <span className="text-[13.5px] text-slate-800">{version.by || 'Без имени'}</span>
                                    <span className="text-[12.5px] tabular-nums text-slate-500">
                                        {shortTime(version.at)}{index === 0 ? ' · текущая' : ''}
                                    </span>
                                </button>
                            </li>
                        ))}
                    </ul>
                </IosModal>
            )}
            {editing && <EditorModal blocks={data.current.blocks} onClose={() => setEditing(false)} onSave={save} />}
        </div>
    );
};

export default ConditionsTab;
