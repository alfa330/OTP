import React, { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronUp, Plus, X } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import {
  PRODUCTION_CHANNELS,
  PRODUCTION_COLOR_MODES,
  PRODUCTION_HINTS,
  PRODUCTION_ITEM_OPTIONS,
  PRODUCTION_MAX_COLOR_NOTES,
  PRODUCTION_MAX_CUSTOM_SIZES,
  PRODUCTION_SIZE_GROUPS,
  PRODUCTION_SIZE_PRESETS,
  PRODUCTION_SIZE_UNITS,
  frameSize,
  hexColorsIn,
  newCustomSize,
  productionBriefRows,
  sizeDimensions,
} from './productionBrief';
import './production-brief.css';

/*
 * Бриф задачи «Продакшн» (задача #354): поля в форме задачи и тот же бриф в
 * карточке. Справочник и проверка — в productionBrief.js, здесь только вид.
 *
 * Визуальный язык — формы задачи (tv-composer-*): та же типографика раздела,
 * те же сегменты. Свой здесь только выбор размеров плитками с рамкой нужной
 * формы — так форматы показывает Canva, о чём и просит ТЗ.
 */

/* Подсказка «i» с перечнем: вступление и строка на вариант или пункт. */
const HintList = ({ intro, rows = [], points = [] }) => (
  <span className="block">
    {intro && <span className="block">{intro}</span>}
    {rows.length > 0 && (
      <span className="mt-1.5 block space-y-1">
        {rows.map(([name, text]) => (
          <span key={name} className="block"><b className="font-semibold text-slate-800">{name}</b> — {text}</span>
        ))}
      </span>
    )}
    {points.length > 0 && (
      <span className="mt-1.5 block space-y-1">
        {points.map((point) => <span key={point} className="block">• {point}</span>)}
      </span>
    )}
  </span>
);

/* Поле брифа: подпись с «i», содержимое, строка «чего не хватает».
   Обёртка одна и та же при любой ошибке — меняется только класс: условная
   обёртка пересоздавала бы поле ввода, и первая же буква, снявшая ошибку,
   выбивала бы человека из поля (урок «Оплаты счетов», 14.10.2026). */
const BriefField = ({ anchor, label, hint, issue = '', children, labelFor }) => (
  <div
    className={`tv-brief-field${issue ? ' is-invalid' : ''}`}
    data-issue-anchor={anchor}
    data-issue={issue ? 'true' : undefined}
  >
    <div className="tv-brief-label">
      {labelFor ? <label htmlFor={labelFor}>{label}</label> : <span>{label}</span>}
      {hint && (typeof hint === 'string'
        ? <InfoHint side="left" text={hint} />
        : <InfoHint side="left">{hint}</InfoHint>)}
    </div>
    {children}
    {issue && <p className="tv-brief-issue">{issue}</p>}
  </div>
);

const SizeFrame = ({ width, height, box = 28 }) => {
  const size = frameSize(width, height, box);
  return (
    <span className="tv-brief-frame" style={{ width: box, height: box }} aria-hidden="true">
      <i style={{ width: size.width, height: size.height }} />
    </span>
  );
};

/* Подпись на плитке короче полной: «A4 альбомный» в плитку не влезает, а
   ориентацию и так видно по рамке и размерам. Полная подпись — у кнопки для
   читалки, в карточке задачи и в выгрузке. */
const TILE_LABELS = { a4_landscape: 'A4' };

const toggleCode = (list, code) => (
  (list || []).includes(code) ? list.filter((item) => item !== code) : [...(list || []), code]
);

export const ProductionBriefFields = ({
  brief,
  onChange,
  description,
  onDescriptionChange,
  disabled = false,
  issueFor = () => '',
}) => {
  const descriptionId = React.useId();
  const notesId = React.useId();
  const customSizes = brief.customSizes || [];
  const customOpen = customSizes.length > 0;
  const swatches = hexColorsIn(brief.colorNotes);

  const patch = (next) => onChange({ ...brief, ...next });
  const patchCustom = (key, next) => patch({
    customSizes: customSizes.map((size) => (size.key === key ? { ...size, ...next } : size)),
  });

  return (
    <section className="tv-brief" aria-label="Бриф продакшна">
      <header className="tv-brief-head">
        <span className="tv-brief-title">Бриф</span>
      </header>

      <BriefField anchor="item" label="Наименование" hint={PRODUCTION_HINTS.item} issue={issueFor('item')}>
        <CustomSelect
          className="tv-person-select tv-brief-select"
          variant="ios"
          ariaLabel="Наименование"
          value={brief.item}
          options={PRODUCTION_ITEM_OPTIONS}
          onChange={(next) => patch({ item: String(next ?? '') })}
          disabled={disabled}
          placeholder="Что нужно сделать…"
          searchable
          searchPlaceholder="Поиск: баннер, флаер, ролик…"
          invalid={Boolean(issueFor('item'))}
        />
      </BriefField>

      <BriefField anchor="channels" label="Адаптация" hint={PRODUCTION_HINTS.channels} issue={issueFor('channels')}>
        <div className="tv-brief-chips tv-brief-control" role="group" aria-label="Адаптация">
          {PRODUCTION_CHANNELS.map((channel) => {
            const active = (brief.channels || []).includes(channel.code);
            return (
              <button
                key={channel.code}
                type="button"
                className={`tv-brief-chip${active ? ' is-active' : ''}`}
                aria-pressed={active}
                disabled={disabled}
                onClick={() => patch({ channels: toggleCode(brief.channels, channel.code) })}
              >
                {active && <Check size={13} strokeWidth={2.5} aria-hidden="true" />}
                {channel.label}
              </button>
            );
          })}
        </div>
      </BriefField>

      <BriefField anchor="sizes" label="Размеры" hint={PRODUCTION_HINTS.sizes} issue={issueFor('sizes')}>
        <div className="tv-brief-sizes tv-brief-control">
          {PRODUCTION_SIZE_GROUPS.map((group) => (
            <div key={group.code} className="tv-brief-size-group">
              <span className="tv-brief-size-caption">
                {group.label}
                <small>{group.code === 'print' ? 'мм' : 'px'}</small>
              </span>
              <div className="tv-brief-tiles" role="group" aria-label={`Размеры: ${group.label.toLowerCase()}`}>
                {PRODUCTION_SIZE_PRESETS.filter((preset) => preset.group === group.code).map((preset) => {
                  const active = (brief.sizes || []).includes(preset.code);
                  return (
                    <button
                      key={preset.code}
                      type="button"
                      className={`tv-brief-tile${active ? ' is-active' : ''}`}
                      aria-pressed={active}
                      aria-label={`${preset.label}, ${sizeDimensions(preset.width, preset.height, preset.unit)}`}
                      disabled={disabled}
                      onClick={() => patch({ sizes: toggleCode(brief.sizes, preset.code) })}
                    >
                      <SizeFrame width={preset.width} height={preset.height} />
                      <span className="tv-brief-tile-label">{TILE_LABELS[preset.code] || preset.label}</span>
                      <span className="tv-brief-tile-dims">{preset.width}×{preset.height}</span>
                      {active && (
                        <span className="tv-brief-tile-check" aria-hidden="true">
                          <Check size={10} strokeWidth={3} />
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}

          {/* Своего размера нет среди плиток намеренно: плитка — это готовый
              формат, а здесь человек сам пишет ширину и высоту. */}
          {!customOpen && (
            <button
              type="button"
              className="tv-brief-add"
              disabled={disabled}
              onClick={() => patch({ customSizes: [newCustomSize()] })}
            >
              <Plus size={13} strokeWidth={2.5} aria-hidden="true" />
              Свой размер
            </button>
          )}

          {customOpen && (
            <div className="tv-brief-custom">
              {customSizes.map((size, index) => (
                <div key={size.key} className="tv-brief-custom-row">
                  <input
                    className="tv-input tv-brief-side"
                    inputMode="numeric"
                    placeholder="Ширина"
                    aria-label={`Свой размер ${index + 1}: ширина`}
                    value={size.width}
                    disabled={disabled}
                    onChange={(event) => patchCustom(size.key, { width: event.target.value.replace(/[^\d]/g, '').slice(0, 6) })}
                  />
                  <span className="tv-brief-times" aria-hidden="true">×</span>
                  <input
                    className="tv-input tv-brief-side"
                    inputMode="numeric"
                    placeholder="Высота"
                    aria-label={`Свой размер ${index + 1}: высота`}
                    value={size.height}
                    disabled={disabled}
                    onChange={(event) => patchCustom(size.key, { height: event.target.value.replace(/[^\d]/g, '').slice(0, 6) })}
                  />
                  <div className="tv-composer-segmented tv-brief-units" role="group" aria-label="Единица">
                    {PRODUCTION_SIZE_UNITS.map((unit) => (
                      <button
                        key={unit.code}
                        type="button"
                        className={size.unit === unit.code ? 'is-active' : ''}
                        aria-pressed={size.unit === unit.code}
                        disabled={disabled}
                        onClick={() => patchCustom(size.key, { unit: unit.code })}
                      >
                        {unit.label}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="tv-brief-remove"
                    aria-label="Убрать этот размер"
                    title="Убрать этот размер"
                    disabled={disabled}
                    onClick={() => patch({ customSizes: customSizes.filter((item) => item.key !== size.key) })}
                  >
                    <X size={14} strokeWidth={2.25} />
                  </button>
                </div>
              ))}
              {customSizes.length < PRODUCTION_MAX_CUSTOM_SIZES && (
                <button
                  type="button"
                  className="tv-brief-add"
                  disabled={disabled}
                  onClick={() => patch({ customSizes: [...customSizes, newCustomSize(customSizes[customSizes.length - 1]?.unit)] })}
                >
                  <Plus size={13} strokeWidth={2.5} aria-hidden="true" />
                  Ещё размер
                </button>
              )}
            </div>
          )}
        </div>
      </BriefField>

      <BriefField
        anchor="color"
        label="Цветность"
        hint={<HintList intro={PRODUCTION_HINTS.color.intro} rows={PRODUCTION_HINTS.color.options} />}
        issue={issueFor('color')}
      >
        <div className="tv-composer-segmented tv-brief-segmented tv-brief-control" role="group" aria-label="Цветность">
          {PRODUCTION_COLOR_MODES.map((mode) => (
            <button
              key={mode.code}
              type="button"
              className={brief.color === mode.code ? 'is-active' : ''}
              aria-pressed={brief.color === mode.code}
              disabled={disabled}
              onClick={() => patch({ color: mode.code })}
            >
              {mode.label}
            </button>
          ))}
        </div>
      </BriefField>

      {brief.color === 'custom' && (
        <BriefField anchor="colorNotes" label="Какие тона" issue={issueFor('colorNotes')} labelFor={notesId}>
          <div className="tv-brief-notes">
            <input
              id={notesId}
              className="tv-input"
              placeholder="Например: жёлтый #FFD400 и чёрный, без синего"
              maxLength={PRODUCTION_MAX_COLOR_NOTES}
              value={brief.colorNotes}
              disabled={disabled}
              onChange={(event) => patch({ colorNotes: event.target.value })}
            />
            {swatches.length > 0 && (
              <span className="tv-brief-swatches" aria-hidden="true">
                {swatches.map((color) => <i key={color} style={{ background: color }} title={color} />)}
              </span>
            )}
          </div>
        </BriefField>
      )}

      <BriefField
        anchor="description"
        label="Описание задания"
        hint={<HintList intro={PRODUCTION_HINTS.description.intro} points={PRODUCTION_HINTS.description.points} />}
        issue={issueFor('description')}
        labelFor={descriptionId}
      >
        <textarea
          id={descriptionId}
          className="tv-textarea tv-brief-description"
          value={description}
          disabled={disabled}
          placeholder="Цель и место размещения, аудитория, текст на макете дословно, обязательные элементы, референсы, ограничения"
          onChange={(event) => onDescriptionChange(event.target.value)}
        />
      </BriefField>
    </section>
  );
};

/* Бриф в карточке задачи: те же поля строками «подпись — значение». */
export const ProductionBriefView = ({ brief }) => {
  const rows = productionBriefRows(brief);
  if (!rows.length) return null;
  return (
    <div className="tv-brief-view">
      <p className="tv-block-label">Бриф</p>
      <dl className="tv-brief-view-grid">
        {rows.map((row) => (
          <React.Fragment key={row.key}>
            <dt>{row.label}</dt>
            <dd>
              {row.text && <span>{row.text}</span>}
              {row.notes && (
                <span className="tv-brief-view-notes">
                  {row.notes}
                  {hexColorsIn(row.notes).map((color) => (
                    <i key={color} className="tv-brief-view-swatch" style={{ background: color }} title={color} />
                  ))}
                </span>
              )}
              {row.items && (
                <span className="tv-brief-view-chips">
                  {row.items.map((item) => <span key={item} className="tv-brief-view-chip">{item}</span>)}
                </span>
              )}
              {row.sizes && (
                <span className="tv-brief-view-chips">
                  {row.sizes.map((size) => (
                    <span key={size.key} className="tv-brief-view-chip is-size">
                      {size.width && size.height && <SizeFrame width={size.width} height={size.height} box={14} />}
                      {size.label && <b>{size.label}</b>}
                      {size.width && size.height && (
                        <span className="tv-brief-view-dims">{sizeDimensions(size.width, size.height, size.unit)}</span>
                      )}
                    </span>
                  ))}
                </span>
              )}
            </dd>
          </React.Fragment>
        ))}
      </dl>
    </div>
  );
};

/* ── Чего не хватает: подвести к полю ──────────────────────────────────────
   Нажали «Поставить задачу», а в брифе пусто, — поле обведено красным, под
   ним сказано, чего нет, и окно само подводит прокрутку к первому такому
   полю (так в «Оплате счетов», решение владельца 14.10.2026). Подвод — после
   отрисовки: в момент нажатия поля ещё не красные. */

const ISSUE_FLASH = [
  { boxShadow: '0 0 0 0 rgba(244, 63, 94, 0.30)' },
  { boxShadow: '0 0 0 8px rgba(244, 63, 94, 0.12)', offset: 0.4 },
  { boxShadow: '0 0 0 12px rgba(244, 63, 94, 0)' },
];

const prefersReducedMotion = () => (
  typeof window !== 'undefined'
  && typeof window.matchMedia === 'function'
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches
);

const revealField = (el) => {
  if (!el) return;
  const reduce = prefersReducedMotion();
  el.scrollIntoView({ block: 'center', behavior: reduce ? 'auto' : 'smooth' });
  if (!reduce && typeof el.animate === 'function') {
    el.animate(ISSUE_FLASH, { duration: 900, easing: 'ease-out' });
  }
};

/* `first()` — к первому полю с пунктом по порядку на экране, `step(±1)` — к
   следующему и предыдущему по кругу. Поле без якоря на экране пропускается. */
export const useBriefIssueNav = (rootRef, issueKeys) => {
  const pendingRef = useRef(null);
  const currentRef = useRef(null);
  const keysRef = useRef(issueKeys);
  keysRef.current = issueKeys;
  const [tick, setTick] = useState(0);

  useLayoutEffect(() => {
    const pending = pendingRef.current;
    pendingRef.current = null;
    const root = rootRef.current;
    if (!pending || !root) return;
    const wanted = new Set(keysRef.current || []);
    const anchors = [...root.querySelectorAll('[data-issue-anchor]')]
      .filter((el) => wanted.has(el.getAttribute('data-issue-anchor')));
    if (!anchors.length) return;
    let index = 0;
    if (pending !== 'first') {
      const current = anchors.findIndex((el) => el.getAttribute('data-issue-anchor') === currentRef.current);
      index = current < 0 ? 0 : (current + pending + anchors.length) % anchors.length;
    }
    currentRef.current = anchors[index].getAttribute('data-issue-anchor');
    revealField(anchors[index]);
  }, [tick, rootRef]);

  const go = useCallback((how) => {
    pendingRef.current = how;
    setTick((value) => value + 1);
  }, []);
  return { first: useCallback(() => go('first'), [go]), step: go };
};

/* «Не хватает: 3 ⌃ ⌄» — в нижней панели окна, когда пустых полей больше одного. */
export const BriefIssueNav = ({ count, onStep }) => {
  if (!(count > 1)) return null;
  return (
    <div className="tv-brief-issue-nav" role="group" aria-label="Поля, где не хватает данных">
      <span aria-live="polite">Не хватает: {count}</span>
      <button type="button" onClick={() => onStep(-1)} aria-label="К предыдущему полю, где не хватает данных" title="Предыдущее">
        <ChevronUp size={16} />
      </button>
      <button type="button" onClick={() => onStep(1)} aria-label="К следующему полю, где не хватает данных" title="Следующее">
        <ChevronDown size={16} />
      </button>
    </div>
  );
};
