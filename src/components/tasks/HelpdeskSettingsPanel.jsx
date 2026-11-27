import React, { memo, useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { ChevronDown, Plus, Trash2, X } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import { HELPDESK_PROFILES, helpdeskProfileLabel } from './helpdeskTicket';

/*
 * Настройки заявок в IT — второй экран окна заявки (как «Настройки» в iOS:
 * шеврон назад, а не второе окно поверх первого). Перенесены из окна «Тикет в
 * IT-отдел» раздела «Тех причины» с теми же правами, их проверяет сервер:
 *   Темы — категории и типы проблем, из которых ИИ выбирает тему заявки. Админ
 *          правит оба отдела, глава ОП или СЗоВ — свой.
 *   Инструкции для ИИ — заметки, которые важнее базовых правил промпта (новый
 *          софт, переименованные системы, новые РМ). «Общие» — только админ.
 * Каналы Telegram здесь больше не настраиваются: заявка уходит не в чат, а
 * задачей на доску IT Helpdesk, исполнителям — обычное уведомление о задаче.
 */

const TABS = [
  { value: 'topics', label: 'Темы заявок' },
  { value: 'ai', label: 'Инструкции для ИИ' },
];

const INSTRUCTION_LABELS = { common: 'Общие', op: 'ОП', szov: 'СЗоВ' };
const INSTRUCTIONS_MAX = 8000;

const deepCopy = (value) => JSON.parse(JSON.stringify(value ?? null));

const Segmented = ({ value, options, onChange, ariaLabel, disabled = false }) => (
  <div className="tv-composer-segmented" role="group" aria-label={ariaLabel}>
    {options.map((option) => (
      <button
        key={option.value}
        type="button"
        className={value === option.value ? 'is-active' : ''}
        aria-pressed={value === option.value}
        disabled={disabled}
        onClick={() => onChange(option.value)}
      >
        {option.label}
      </button>
    ))}
  </div>
);

/* ─── Темы заявок ─── */

const TopicsEditor = ({ draft, setDraft, profile, disabled }) => {
  const [expanded, setExpanded] = useState(null);
  useEffect(() => { setExpanded(null); }, [profile]);
  const categories = draft?.[profile]?.categories || [];

  const update = (mutate) => setDraft((prev) => {
    const next = deepCopy(prev);
    mutate(next[profile].categories);
    return next;
  });

  const addCategory = () => {
    update((list) => { list.push({ name: '', items: [''] }); });
    setExpanded(categories.length);
  };

  return (
    <div className="tv-hd-topics">
      {categories.length === 0 && <p className="tv-hd-empty">Тем пока нет — добавьте первую.</p>}
      {categories.map((category, index) => {
        const open = expanded === index;
        const items = Array.isArray(category.items) ? category.items : [];
        const filled = items.filter((item) => String(item || '').trim()).length;
        return (
          <div key={index} className={`tv-hd-topic-card${open ? ' is-open' : ''}`}>
            <div className="tv-hd-topic-head">
              <button
                type="button"
                className="tv-hd-topic-toggle"
                aria-expanded={open}
                onClick={() => setExpanded(open ? null : index)}
              >
                <ChevronDown size={15} className="tv-hd-topic-chevron" aria-hidden="true" />
                <span className={`tv-hd-topic-name${category.name ? '' : ' is-empty'}`}>{category.name || 'Без названия'}</span>
                <span className="tv-hd-topic-count">{filled}</span>
              </button>
              <button
                type="button"
                className="tv-hd-icon-btn is-danger"
                title="Убрать тему"
                aria-label={`Убрать тему «${category.name || 'без названия'}»`}
                disabled={disabled}
                onClick={() => { update((list) => { list.splice(index, 1); }); setExpanded(null); }}
              >
                <Trash2 size={14} />
              </button>
            </div>
            {open && (
              <div className="tv-hd-topic-body">
                <input
                  className="tv-input"
                  value={category.name}
                  disabled={disabled}
                  maxLength={200}
                  placeholder="Название темы, например «Телефония»"
                  aria-label="Название темы"
                  autoFocus={!category.name}
                  onChange={(event) => update((list) => { list[index].name = event.target.value; })}
                />
                <div className="tv-hd-topic-items">
                  {items.map((item, itemIndex) => (
                    <div key={itemIndex} className="tv-hd-topic-item">
                      <input
                        className="tv-input"
                        value={item}
                        disabled={disabled}
                        maxLength={300}
                        placeholder="Тип проблемы"
                        aria-label="Тип проблемы"
                        onChange={(event) => update((list) => { list[index].items[itemIndex] = event.target.value; })}
                      />
                      <button
                        type="button"
                        className="tv-hd-icon-btn"
                        title="Убрать тип"
                        aria-label="Убрать тип"
                        disabled={disabled}
                        onClick={() => update((list) => { list[index].items.splice(itemIndex, 1); })}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="tv-hd-add"
                    disabled={disabled}
                    onClick={() => update((list) => { (list[index].items = list[index].items || []).push(''); })}
                  >
                    <Plus size={13} /> Тип проблемы
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })}
      <button type="button" className="tv-hd-add is-block" disabled={disabled} onClick={addCategory}>
        <Plus size={14} /> Тема
      </button>
    </div>
  );
};

/* ─── Экран настроек ─── */

const HelpdeskSettingsPanel = ({ apiBaseUrl, buildHeaders, notify, onBack, onSaved }) => {
  const [tab, setTab] = useState('topics');
  const [loading, setLoading] = useState(true);
  const [catalog, setCatalog] = useState(null);
  const [draft, setDraft] = useState(null);
  const [catalogProfiles, setCatalogProfiles] = useState([]);
  const [catalogProfile, setCatalogProfile] = useState('');
  const [instructions, setInstructions] = useState([]);
  const [instructionProfiles, setInstructionProfiles] = useState([]);
  const [instructionProfile, setInstructionProfile] = useState('');
  const [instructionDraft, setInstructionDraft] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const [catalogRes, instructionsRes] = await Promise.all([
          axios.get(`${apiBaseUrl}/api/it_tickets/catalog`, { headers: buildHeaders() }),
          axios.get(`${apiBaseUrl}/api/it_tickets/instructions`, { headers: buildHeaders() })
            .catch(() => ({ data: { items: [], editable_profiles: [] } })),
        ]);
        if (!alive) return;
        const nextCatalog = catalogRes?.data?.catalog || null;
        const editable = (catalogRes?.data?.catalog_editable_profiles || []).filter((profile) => HELPDESK_PROFILES.includes(profile));
        setCatalog(nextCatalog);
        setDraft(deepCopy(nextCatalog));
        setCatalogProfiles(editable);
        const defaultProfile = catalogRes?.data?.default_profile;
        setCatalogProfile(editable.includes(defaultProfile) ? defaultProfile : (editable[0] || ''));
        const items = Array.isArray(instructionsRes?.data?.items) ? instructionsRes.data.items : [];
        const instructionEditable = Array.isArray(instructionsRes?.data?.editable_profiles) ? instructionsRes.data.editable_profiles : [];
        setInstructions(items);
        setInstructionProfiles(instructionEditable);
        setInstructionProfile(instructionEditable.includes(defaultProfile) ? defaultProfile : (instructionEditable[0] || ''));
      } catch (error) {
        if (alive) notify(error?.response?.data?.error || 'Не удалось загрузить настройки заявок', 'error');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [apiBaseUrl, buildHeaders, notify]);

  const instructionItem = useMemo(
    () => instructions.find((item) => item.profile === instructionProfile) || null,
    [instructions, instructionProfile]
  );
  useEffect(() => { setInstructionDraft(instructionItem?.instructions || ''); }, [instructionItem]);

  const catalogDirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(catalog), [draft, catalog]);
  const instructionsDirty = (instructionItem?.instructions || '') !== instructionDraft;
  const dirty = tab === 'topics' ? catalogDirty : instructionsDirty;

  const saveCatalog = useCallback(async () => {
    setSaving(true);
    try {
      const res = await axios.put(`${apiBaseUrl}/api/it_tickets/catalog`, { catalog: draft }, { headers: buildHeaders() });
      const saved = res?.data?.catalog || draft;
      setCatalog(saved);
      setDraft(deepCopy(saved));
      notify('Темы заявок сохранены');
      onSaved?.();
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить темы', 'error');
    } finally {
      setSaving(false);
    }
  }, [apiBaseUrl, buildHeaders, draft, notify, onSaved]);

  const saveInstructions = useCallback(async () => {
    setSaving(true);
    try {
      const res = await axios.put(
        `${apiBaseUrl}/api/it_tickets/instructions`,
        { profile: instructionProfile, instructions: instructionDraft },
        { headers: buildHeaders() }
      );
      const item = res?.data?.item;
      if (item) setInstructions((prev) => [...prev.filter((entry) => entry.profile !== item.profile), item]);
      notify('Инструкции для ИИ сохранены');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить инструкции', 'error');
    } finally {
      setSaving(false);
    }
  }, [apiBaseUrl, buildHeaders, instructionDraft, instructionProfile, notify]);

  const save = tab === 'topics' ? saveCatalog : saveInstructions;

  return (
    <>
      <div className="tv-modal-body tv-hd-settings">
        <div className="tv-hd-settings-tabs">
          <Segmented value={tab} options={TABS} onChange={setTab} ariaLabel="Что настроить" disabled={saving} />
        </div>
        {loading ? (
          <p className="tv-hd-empty">Загружаю настройки…</p>
        ) : tab === 'topics' ? (
          catalogProfiles.length === 0 ? (
            <p className="tv-hd-empty">Темы правят администраторы и главы ОП и СЗоВ.</p>
          ) : (
            <>
              <div className="tv-composer-row tv-hd-settings-row">
                <span className="tv-composer-row-label">
                  Отдел
                  <InfoHint side="left" text="У каждого отдела свой список тем: из него ИИ выбирает тему заявки, его же видят в форме. Пустые названия не сохраняются." />
                </span>
                {catalogProfiles.length > 1 ? (
                  <Segmented
                    value={catalogProfile}
                    options={catalogProfiles.map((profile) => ({ value: profile, label: helpdeskProfileLabel(profile) }))}
                    onChange={setCatalogProfile}
                    ariaLabel="Отдел"
                    disabled={saving}
                  />
                ) : (
                  <span className="tv-hd-settings-value">{helpdeskProfileLabel(catalogProfile)}</span>
                )}
              </div>
              {draft?.[catalogProfile] && (
                <TopicsEditor draft={draft} setDraft={setDraft} profile={catalogProfile} disabled={saving} />
              )}
            </>
          )
        ) : instructionProfiles.length === 0 ? (
          <p className="tv-hd-empty">Инструкции для ИИ правят администраторы и главы ОП и СЗоВ.</p>
        ) : (
          <>
            <div className="tv-composer-row tv-hd-settings-row">
              <span className="tv-composer-row-label">
                Для кого
                <InfoHint side="left" text="Заметки добавляются к заданию для ИИ и важнее базовых правил. Сюда пишут недавние изменения: новый софт, переименованные системы, новые рабочие места. «Общие» действуют для всех отделов." />
              </span>
              {instructionProfiles.length > 1 ? (
                <Segmented
                  value={instructionProfile}
                  options={instructionProfiles.map((profile) => ({ value: profile, label: INSTRUCTION_LABELS[profile] || profile }))}
                  onChange={setInstructionProfile}
                  ariaLabel="Для кого инструкции"
                  disabled={saving}
                />
              ) : (
                <span className="tv-hd-settings-value">{INSTRUCTION_LABELS[instructionProfile] || instructionProfile}</span>
              )}
            </div>
            <textarea
              className="tv-textarea tv-hd-instructions"
              value={instructionDraft}
              maxLength={INSTRUCTIONS_MAX}
              disabled={saving}
              rows={9}
              aria-label="Инструкции для ИИ"
              placeholder="Например: «С 01.12 телефония Oktell заменена на iCORE Phone — заявки по звонкам относить к теме „Телефония“. Добавлены РМ 31–35»."
              onChange={(event) => setInstructionDraft(event.target.value)}
            />
            <p className="tv-hd-settings-meta">
              {instructionItem?.updated_at
                ? `Изменил ${instructionItem.updated_by_name || '—'} · ${instructionItem.updated_at}`
                : 'Ещё не заполнялись'}
              <span>{instructionDraft.length}/{INSTRUCTIONS_MAX}</span>
            </p>
          </>
        )}
      </div>
      <div className="tv-modal-footer">
        <button type="button" className="tv-btn tv-btn-ghost tv-modal-cancel" disabled={saving} onClick={onBack}>
          К заявке
        </button>
        <button type="button" className="tv-btn tv-btn-primary" disabled={saving || loading || !dirty} onClick={save}>
          {saving ? 'Сохраняю…' : 'Сохранить'}
        </button>
      </div>
    </>
  );
};

export default memo(HelpdeskSettingsPanel);
