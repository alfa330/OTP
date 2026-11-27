import React, { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Check, LoaderCircle, Settings, Sparkles } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import { parseTelegramText, stripTelegramTags } from '../technical/telegramText';
import { WORKPLACE_CABINETS } from '../technical/workplaceCabinets';
import HelpdeskWorkplacePicker from './HelpdeskWorkplacePicker';
import {
  HELPDESK_PROFILES,
  aiErrorText,
  applyTicketAiResult,
  cabinetsForProfile,
  catalogCategories,
  catalogItems,
  clipboardFiles,
  helpdeskProfileLabel,
  isTicketTextFresh,
  ticketAiRequest,
  ticketExtraLines,
  ticketSignature,
  toggleTicketCheck,
} from './helpdeskTicket';
import './helpdesk-ticket.css';

/*
 * Заявка в IT — форма задачи на доске IT Helpdesk (владелец 27.11.2026: перенос
 * «Тикета в IT-отдел» из «Тех причин» в «Задачи», «в стилистике раздела и
 * намного лучше визуально… с таким же функционалом»).
 *
 * Тот же порядок, что у обычной задачи: сверху — описание (на месте «Что нужно
 * сделать?»), потом проект и исполнители, потом капсулы «+ Тема», «+ Срочность»,
 * «+ Файлы» с серой панелью раскрытого. Что умел «Тикет в IT-отдел», умеет и
 * она: ИИ разбирает описание, подбирает тему из каталога отдела, срочность по
 * массовости, поля «что уточнить» (номер РМ — кликом по схеме кабинета) и советы
 * «попробуйте сначала»; собирает короткий текст, его можно поправить руками.
 * Вложения — как у задачи (несколько файлов), скриншот вставляется в описание
 * прямо из буфера. Уходит заявка задачей на доску ITH — её ставит TasksView.
 *
 * Состояние заявки живёт в форме задачи (values.ticket) и меняется через
 * onChange(patch); правила — в helpdeskTicket.js.
 */

/* Ответ ИИ — разметка Telegram (<b>, <i>, <code>). HTML не вставляем: разбор в
   дерево (telegramText.js), неизвестные теги остаются текстом. */
const renderTelegram = (nodes) => nodes.map((node, index) => {
  if (typeof node === 'string') return node;
  return React.createElement(
    node.tag,
    { key: index, className: node.tag === 'code' || node.tag === 'pre' ? 'tv-hd-code' : undefined },
    ...renderTelegram(node.children),
  );
});

const escapeTelegram = (value) => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;');

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

/* Поле, которое подобрал ИИ. Обёртка одна при любой ошибке — меняется только
   класс: иначе первая же буква, снявшая ошибку, пересоздавала бы поле ввода. */
const TicketField = memo(function TicketField({ field, value, onChange, cabinets, disabled, issue }) {
  const labelId = useId();
  const type = field.type;
  const wide = type === 'workplace' || type === 'textarea';
  const change = (next) => onChange(field.key, next);
  let control;
  if (type === 'workplace') {
    control = (
      <HelpdeskWorkplacePicker
        cabinets={cabinets}
        value={value}
        onChange={change}
        disabled={disabled}
        invalid={Boolean(issue)}
        labelId={labelId}
      />
    );
  } else if (type === 'textarea') {
    control = (
      <textarea
        className="tv-textarea"
        aria-labelledby={labelId}
        value={value ?? ''}
        rows={3}
        disabled={disabled}
        placeholder={field.placeholder}
        onChange={(event) => change(event.target.value)}
      />
    );
  } else if (type === 'select' && field.options.length) {
    control = (
      <CustomSelect
        className="tv-person-select"
        variant="ios"
        ariaLabel={field.label}
        value={value ?? ''}
        options={field.options.map((option) => ({ value: option, label: option }))}
        placeholder={field.placeholder || 'Выберите…'}
        disabled={disabled}
        onChange={(next) => change(String(next ?? ''))}
      />
    );
  } else {
    const inputType = ['date', 'time', 'number'].includes(type) ? type : 'text';
    control = (
      <input
        className="tv-input"
        type={inputType}
        aria-labelledby={labelId}
        value={value ?? ''}
        disabled={disabled}
        placeholder={field.placeholder}
        onChange={(event) => change(event.target.value)}
      />
    );
  }
  return (
    <div
      className={`tv-hd-field${wide ? ' is-wide' : ''}${issue ? ' is-invalid' : ''}`}
      data-issue-anchor={`ticket-field-${field.key}`}
    >
      <div className="tv-hd-field-label">
        <span id={labelId}>{field.label}</span>
        {field.required && <span className="tv-hd-required" title="Обязательное поле">*</span>}
        {field.hint && <InfoHint side="left" text={field.hint} />}
      </div>
      {control}
      {issue && <p className="tv-brief-issue">{issue}</p>}
    </div>
  );
});

const SECTION_IDS = ['topic', 'priority', 'files'];

const HelpdeskTicketForm = ({
  ticket,
  onChange,
  disabled = false,
  apiBaseUrl,
  buildHeaders,
  notify,
  boardRows = null,
  priorityOptions = [],
  files = [],
  onAddFiles,
  onClearFiles,
  renderFiles,
  issueFor = () => '',
  onOpenSettings,
  settingsVersion = 0,
}) => {
  const [meta, setMeta] = useState({ loading: true, catalog: null, defaultProfile: '', editable: [] });
  const [aiMode, setAiMode] = useState(null);
  const [openIds, setOpenIds] = useState(() => new Set());
  const [editingText, setEditingText] = useState(false);
  // Спросили недостающее (need_more_info) — пустые обязательные поля видны сразу, без попытки отправить.
  const [askedMissing, setAskedMissing] = useState(false);
  const ticketRef = useRef(ticket);
  ticketRef.current = ticket;
  const notifyRef = useRef(notify);
  notifyRef.current = notify;
  const toast = useCallback((message, type) => notifyRef.current?.(message, type), []);

  /* Каталог тем, отдел по умолчанию и право настраивать — с сервера, при каждом
     открытии формы и после сохранения настроек. */
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await axios.get(`${apiBaseUrl}/api/it_tickets/catalog`, { headers: buildHeaders() });
        if (!alive) return;
        setMeta({
          loading: false,
          catalog: res?.data?.catalog || null,
          defaultProfile: res?.data?.default_profile || '',
          editable: (res?.data?.catalog_editable_profiles || []).filter((profile) => HELPDESK_PROFILES.includes(profile)),
        });
      } catch (error) {
        if (!alive) return;
        setMeta((prev) => ({ ...prev, loading: false }));
        toast(error?.response?.data?.error || 'Не удалось загрузить темы заявок', 'error');
      }
    })();
    return () => { alive = false; };
  }, [apiBaseUrl, buildHeaders, toast, settingsVersion]);

  // Отдел заявки — по отделу заявителя, пока человек не выбрал другой.
  useEffect(() => {
    if (meta.loading || ticket.profile) return;
    onChange({ profile: HELPDESK_PROFILES.includes(meta.defaultProfile) ? meta.defaultProfile : HELPDESK_PROFILES[0] });
  }, [meta.loading, meta.defaultProfile, ticket.profile, onChange]);

  const cabinets = useMemo(() => cabinetsForProfile(ticket.profile, WORKPLACE_CABINETS), [ticket.profile]);
  const aiBusy = aiMode !== null;
  const locked = disabled || aiBusy;
  const hasAiResult = ticket.fields.length > 0 || ticket.checks.length > 0 || Boolean(ticket.text);

  const runAi = useCallback(async (mode) => {
    const base = ticketRef.current;
    if (!base.description.trim() && !base.category) {
      toast('Опишите, что случилось', 'error');
      return;
    }
    setAiMode(mode);
    try {
      const res = await axios.post(
        `${apiBaseUrl}/api/it_tickets/ai`,
        ticketAiRequest(base, mode, cabinets),
        { headers: buildHeaders() }
      );
      if (res?.data?.status !== 'success') {
        toast(aiErrorText(res?.data?.error), 'error');
        return;
      }
      const { patch, status } = applyTicketAiResult(base, res.data.result || {}, meta.catalog);
      onChange(patch);
      setAskedMissing(status === 'need_more_info');
      if (status === 'ready') setEditingText(false);
    } catch (error) {
      toast(aiErrorText(error?.response?.data?.error), 'error');
    } finally {
      setAiMode(null);
    }
  }, [apiBaseUrl, buildHeaders, cabinets, meta.catalog, onChange, toast]);

  /* ─── Капсулы «+ Тема», «+ Срочность», «+ Файлы» ───
     Раскрыто то, что открыли руками, и то, у чего есть значение (тему и
     срочность ставит ИИ). Закрыть капсулу — снять значение, как у задачи. */
  const sectionMeta = useMemo(() => ({
    topic: {
      label: 'Тема',
      hasValue: Boolean(ticket.category),
      // Тема с типом длинная, а раскрытая панель и так показывает её списками —
      // на капсуле был бы дубль, выталкивающий соседей на вторую строку.
      summary: '',
      clear: () => onChange({ category: '', subcategory: '', note: '' }),
    },
    priority: {
      label: 'Срочность',
      hasValue: (ticket.priority || 'normal') !== 'normal',
      summary: priorityOptions.find((option) => option.value === ticket.priority)?.label || '',
      clear: () => onChange({ priority: 'normal' }),
    },
    files: {
      label: 'Файлы',
      hasValue: files.length > 0,
      summary: files.length ? String(files.length) : '',
      clear: () => onClearFiles?.(),
    },
  }), [ticket.category, ticket.subcategory, ticket.priority, priorityOptions, files.length, onChange, onClearFiles]);
  const isOpen = (id) => openIds.has(id) || sectionMeta[id].hasValue;
  const toggleSection = (id) => {
    const open = isOpen(id);
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (open) next.delete(id); else next.add(id);
      return next;
    });
    if (open) sectionMeta[id].clear();
  };

  /* Скриншот из буфера (Ctrl+V в описании) — сразу во вложения. Текст из буфера
     вставляется как обычно: глушим вставку, только если в буфере одни файлы. */
  const handlePaste = (event) => {
    const pasted = clipboardFiles(event.clipboardData);
    if (!pasted.length || typeof onAddFiles !== 'function') return;
    if (!event.clipboardData?.getData?.('text/plain')) event.preventDefault();
    onAddFiles(pasted);
    setOpenIds((prev) => new Set(prev).add('files'));
  };

  const categoryOptions = useMemo(
    () => catalogCategories(meta.catalog, ticket.profile).map((category) => ({ value: category.name, label: category.name })),
    [meta.catalog, ticket.profile]
  );
  const itemOptions = useMemo(
    () => catalogItems(meta.catalog, ticket.profile, ticket.category).map((item) => ({ value: item, label: item })),
    [meta.catalog, ticket.profile, ticket.category]
  );

  const setFieldValue = useCallback((key, value) => {
    onChange({ values: { ...(ticketRef.current.values || {}), [key]: value } });
  }, [onChange]);

  const fresh = isTicketTextFresh(ticket);
  const extraLines = ticketExtraLines(ticket);
  const plainText = useMemo(() => stripTelegramTags(ticket.text || ''), [ticket.text]);
  const describeIssue = issueFor('ticket-description');
  const fieldIssue = (field) => issueFor(`ticket-field-${field.key}`)
    || (askedMissing && field.required && !String(ticket.values?.[field.key] ?? '').trim() ? 'Без этого заявку не возьмут в работу' : '');
  const canConfigure = meta.editable.length > 0 && typeof onOpenSettings === 'function';

  return (
    <>
      {/* Описание — на месте «Что нужно сделать?» обычной задачи. */}
      <div className={`tv-hd-describe${describeIssue ? ' is-invalid' : ''}`} data-issue-anchor="ticket-description">
        <textarea
          className="tv-hd-description"
          value={ticket.description}
          readOnly={aiBusy}
          disabled={disabled}
          autoFocus
          aria-label="Что случилось"
          placeholder="Что случилось? Где, что не работает и с какого времени — своими словами"
          onChange={(event) => onChange({ description: event.target.value })}
          onPaste={handlePaste}
        />
        <div className="tv-hd-describe-bar">
          <button
            type="button"
            className="tv-btn tv-hd-ai-btn"
            disabled={locked || (!ticket.description.trim() && !ticket.category)}
            onClick={() => runAi('draft')}
          >
            {aiMode === 'draft'
              ? <LoaderCircle size={15} className="tv-hd-spin" aria-hidden="true" />
              : <Sparkles size={15} aria-hidden="true" />}
            {aiMode === 'draft' ? 'Разбираю…' : (hasAiResult ? 'Разобрать заново' : 'Разобрать с ИИ')}
          </button>
          <InfoHint
            side="left"
            text="ИИ подберёт тему и срочность, спросит недостающее (например, номер рабочего места) и соберёт короткий текст заявки. Можно и без него — заявка уйдёт с вашим описанием. Скриншот вставляется сюда же: Ctrl+V."
          />
        </div>
        {describeIssue && <p className="tv-brief-issue">{describeIssue}</p>}
      </div>

      {boardRows}

      <div className="tv-composer-chips">
        {SECTION_IDS.map((id) => {
          const section = sectionMeta[id];
          const active = isOpen(id);
          return (
            <button
              key={id}
              type="button"
              className={`tv-composer-chip ${active ? 'is-active' : ''}`}
              disabled={locked}
              onClick={() => toggleSection(id)}
            >
              <span className="tv-composer-chip-sign">{active ? '×' : '+'}</span>
              {section.label}
              {section.hasValue && section.summary && <span className="tv-composer-chip-value">{section.summary}</span>}
            </button>
          );
        })}
        {canConfigure && (
          <button
            type="button"
            className="tv-hd-settings-btn"
            disabled={aiBusy}
            title="Темы заявок и инструкции для ИИ"
            onClick={onOpenSettings}
          >
            <Settings size={14} aria-hidden="true" />
            Настройки
          </button>
        )}
      </div>

      {(isOpen('topic') || isOpen('priority')) && (
        <div className="tv-composer-details">
          {isOpen('topic') && (
            <>
              <div className="tv-composer-row">
                <span className="tv-composer-row-label">
                  Отдел
                  <InfoHint side="left" text="У ОП и СЗоВ свои темы, своя схема кабинетов и свои подсказки для ИИ. Сначала выбран ваш отдел." />
                </span>
                <Segmented
                  value={ticket.profile}
                  options={HELPDESK_PROFILES.map((profile) => ({ value: profile, label: helpdeskProfileLabel(profile) }))}
                  ariaLabel="Отдел заявки"
                  disabled={locked}
                  onChange={(profile) => {
                    if (profile !== ticket.profile) onChange({ profile, category: '', subcategory: '', note: '' });
                  }}
                />
              </div>
              <div className="tv-composer-row tv-hd-topic-row">
                <span className="tv-composer-row-label">Тема</span>
                <div className="tv-composer-row-body">
                  <div className="tv-hd-topic">
                    <CustomSelect
                      className="tv-person-select"
                      variant="ios"
                      ariaLabel="Категория"
                      value={ticket.category}
                      options={categoryOptions}
                      placeholder={meta.loading ? 'Загрузка…' : 'Категория'}
                      disabled={locked || meta.loading}
                      searchable={categoryOptions.length > 8}
                      searchPlaceholder="Найти тему…"
                      onChange={(next) => onChange({ category: String(next ?? ''), subcategory: '', note: '' })}
                    />
                    <CustomSelect
                      className="tv-person-select"
                      variant="ios"
                      ariaLabel="Тип проблемы"
                      value={ticket.subcategory}
                      options={itemOptions}
                      placeholder="Тип проблемы"
                      disabled={locked || !ticket.category || !itemOptions.length}
                      searchable={itemOptions.length > 8}
                      searchPlaceholder="Найти тип…"
                      onChange={(next) => onChange({ subcategory: String(next ?? ''), note: '' })}
                    />
                  </div>
                  {ticket.note && (
                    <p className="tv-hd-note">
                      <Sparkles size={13} aria-hidden="true" />
                      <span>{ticket.note}</span>
                    </p>
                  )}
                </div>
              </div>
            </>
          )}
          {isOpen('priority') && (
            <div className="tv-composer-row">
              <span className="tv-composer-row-label">
                Срочность
                <InfoHint side="left" text="ИИ ставит срочность по массовости: сколько рабочих мест затронуто и встала ли работа. Половина кабинета и больше — критично. Можно поправить." />
              </span>
              <Segmented
                value={ticket.priority}
                options={priorityOptions}
                ariaLabel="Срочность заявки"
                disabled={locked}
                onChange={(priority) => onChange({ priority })}
              />
            </div>
          )}
        </div>
      )}

      {ticket.checks.length > 0 && (
        <section className="tv-hd-group" aria-label="Попробуйте сначала">
          <div className="tv-hd-group-head">
            <span className="tv-hd-group-title">Попробуйте сначала</span>
            <InfoHint
              side="left"
              text="Это первое, о чём спросит сисадмин. Отметьте, что уже сделали, — ответ на «что уже пробовали» попадёт в заявку сам. Если что-то помогло, заявка не нужна."
            />
          </div>
          <div className="tv-hd-checks">
            {ticket.checks.map((check, index) => {
              const done = ticket.done.includes(index);
              return (
                <button
                  key={`${index}-${check.slice(0, 24)}`}
                  type="button"
                  role="checkbox"
                  aria-checked={done}
                  className={`tv-hd-check${done ? ' is-done' : ''}`}
                  disabled={locked}
                  onClick={() => onChange(toggleTicketCheck(ticketRef.current, index))}
                >
                  <span className="tv-hd-check-box" aria-hidden="true">{done && <Check size={11} strokeWidth={3} />}</span>
                  <span className="tv-hd-check-text">{check}</span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {ticket.fields.length > 0 && (
        <section className="tv-hd-group" aria-label="Детали заявки">
          <div className="tv-hd-group-head">
            <span className="tv-hd-group-title">Детали заявки</span>
            <InfoHint
              side="left"
              text="Поля подобрал ИИ под вашу проблему. Со звёздочкой — обязательные: без них сисадмин не поймёт, куда идти и что взять с собой."
            />
          </div>
          <div className="tv-hd-fields">
            {ticket.fields.map((field) => (
              <TicketField
                key={field.key}
                field={field}
                value={ticket.values?.[field.key]}
                onChange={setFieldValue}
                cabinets={cabinets}
                disabled={locked}
                issue={fieldIssue(field)}
              />
            ))}
          </div>
          {!fresh && (
            <div className="tv-hd-group-foot">
              <button
                type="button"
                className="tv-btn tv-hd-compose-btn"
                disabled={locked}
                onClick={() => runAi('finalize')}
              >
                {aiMode === 'finalize'
                  ? <LoaderCircle size={15} className="tv-hd-spin" aria-hidden="true" />
                  : <Sparkles size={15} aria-hidden="true" />}
                {aiMode === 'finalize' ? 'Собираю…' : (ticket.text ? 'Пересобрать заявку' : 'Собрать заявку')}
              </button>
            </div>
          )}
        </section>
      )}

      {Boolean(ticket.text) && (
        <section className="tv-hd-group tv-hd-preview" aria-label="Текст заявки">
          <div className="tv-hd-group-head">
            <span className="tv-hd-group-title">Заявка</span>
            <InfoHint side="left" text="Так её увидит IT: задачей на доске IT Helpdesk. Заголовок и текст можно поправить." />
            <button
              type="button"
              className="tv-hd-link"
              disabled={locked}
              onClick={() => setEditingText((value) => !value)}
            >
              {editingText ? 'Готово' : 'Изменить'}
            </button>
          </div>
          <input
            className="tv-hd-preview-title"
            value={ticket.title}
            maxLength={255}
            disabled={locked}
            aria-label="Заголовок заявки"
            placeholder="Заголовок заявки"
            onChange={(event) => onChange({ title: event.target.value, titleEdited: true })}
          />
          {editingText ? (
            <textarea
              className="tv-textarea tv-hd-preview-edit"
              value={plainText}
              rows={8}
              disabled={locked}
              aria-label="Текст заявки"
              // Свой текст — окончательный: «Пересобрать» его не предлагает, пока не поменяют поля.
              onChange={(event) => onChange({
                text: escapeTelegram(event.target.value),
                composed: true,
                composedSig: ticketSignature(ticketRef.current),
              })}
            />
          ) : (
            <div className="tv-hd-preview-body">{renderTelegram(parseTelegramText(ticket.text))}</div>
          )}
          {!editingText && extraLines.length > 0 && (
            <div className="tv-hd-preview-extra">
              <span className="tv-hd-preview-extra-title">Детали</span>
              {extraLines.map((line) => <span key={line}>{line}</span>)}
            </div>
          )}
        </section>
      )}

      {isOpen('files') && renderFiles?.()}
    </>
  );
};

export default memo(HelpdeskTicketForm);
