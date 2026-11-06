import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check } from 'lucide-react';
import { IosModal, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';

/*
 * Наблюдатели задачи — прямо из карточки (задача #401).
 *
 * По постановке человек не из проекта видит конкретную задачу, только если у
 * него есть доступ к доске И его добавили наблюдателем. Звать наблюдателей
 * через полную форму правки неудобно, поэтому постановщик делает это здесь:
 * список людей с доступом к доске, выбор — касанием строки.
 *
 * Выбранное — мягкой голубой заливкой с галочкой, наведение — лёгким фоном,
 * как в остальном разделе (выбор не чернеет). Сохраняется одним запросом.
 */

const initials = (name) => String(name || '?')
  .split(/\s+/)
  .filter(Boolean)
  .slice(0, 2)
  .map((part) => part[0])
  .join('')
  .toUpperCase();

const TaskWatchersSheet = ({
  open,
  onClose,
  taskKey = '',
  projectName = '',
  candidates = [],
  selectedIds = [],
  onSave,
}) => {
  const [picked, setPicked] = useState(() => new Set());
  const [saving, setSaving] = useState(false);

  /* Открыли — начинаем с того, что сейчас у задачи. Только по открытию: карточка
     под окном перерисовывается (минутный таймер, обновление списка), и зависеть
     от её массива значило бы сбрасывать отметки посреди выбора. */
  const selectedRef = useRef(selectedIds);
  selectedRef.current = selectedIds;
  useEffect(() => {
    if (open) setPicked(new Set((selectedRef.current || []).map(String)));
  }, [open]);

  const unchanged = useMemo(() => {
    const current = new Set((selectedIds || []).map(String));
    return current.size === picked.size && [...picked].every((id) => current.has(id));
  }, [selectedIds, picked]);

  const toggle = (personId) => setPicked((prev) => {
    const next = new Set(prev);
    const key = String(personId);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });

  const save = async () => {
    if (unchanged) {
      onClose?.();
      return;
    }
    setSaving(true);
    try {
      const ok = await onSave?.([...picked].map(Number));
      if (ok !== false) onClose?.();
    } finally {
      setSaving(false);
    }
  };

  const footer = (
    <div className="flex w-full items-center justify-end gap-2">
      <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={saving}>Отмена</button>
      <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving}>
        {saving ? 'Сохраняю…' : 'Готово'}
      </button>
    </div>
  );

  return (
    <IosModal
      open={open}
      onClose={onClose}
      title="Наблюдатели"
      subtitle={[taskKey, projectName && `видят задачу на доске «${projectName}»`].filter(Boolean).join(' · ')}
      maxWidth="max-w-md"
      footer={footer}
    >
      {candidates.length ? (
        <ul className={`${iosCard} divide-y divide-slate-100 overflow-hidden`} role="listbox" aria-multiselectable="true">
          {candidates.map((person) => {
            const active = picked.has(String(person.id));
            return (
              <li key={person.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={active}
                  disabled={saving}
                  onClick={() => toggle(person.id)}
                  className={`flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition active:scale-[0.99] ${
                    active ? 'bg-[#eef4ff]' : 'hover:bg-[#f6f6f4]'
                  }`}
                >
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-500">
                    {initials(person.name)}
                  </span>
                  <span className="min-w-0 grow basis-0">
                    <span className={`block truncate text-[13.5px] font-medium ${active ? 'text-[#1d4ed8]' : 'text-slate-800'}`}>
                      {person.name}
                    </span>
                    {person.department && (
                      <span className="block truncate text-[12px] text-slate-500">{person.department}</span>
                    )}
                  </span>
                  <span className="grid h-5 w-5 shrink-0 place-items-center">
                    {active && <Check size={17} strokeWidth={2.4} className="text-[#2563eb]" />}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="px-1 py-6 text-center text-[13px] text-slate-500">
          У доски пока нет людей с доступом «доска» — их добавляют в настройках проекта.
        </p>
      )}
    </IosModal>
  );
};

export default TaskWatchersSheet;
