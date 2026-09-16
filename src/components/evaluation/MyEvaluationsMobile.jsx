import React, { useMemo, useState } from 'react';
import {
  GraduationCap,
  Loader2,
  MessageSquareText,
  Phone,
  Quote,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  UserRound,
} from 'lucide-react';
import { IosBadge, IosModal, iosCard } from '../ui/ios';
import {
  AUCTION_PHONE_BUTTON,
  AuctionPhoneGauge,
  AuctionPhoneGroup,
  AuctionPhoneLinkRow,
  AuctionPhoneMonthHeader,
  useLastPresent,
} from '../resources/ShiftAuctionMobile';
import '../resources/shift-auction-mobile.css';
import './my-evaluations-mobile.css';
import { formatPhoneMonth, shiftPhoneMonth } from '../../utils/phoneMonth';
import {
  buildEvaluationCriteria,
  buildEvaluationRows,
  buildEvaluationTest,
  canRequestReevaluation,
  evaluationChatQuotes,
  evaluationScoreTone,
  evaluationVerdict,
  formatEvaluationDay,
  formatEvaluationScore,
  formatEvaluationsCount,
  summarizeEvaluationCriteria,
  summarizeEvaluationKinds,
  summarizeMonitoringScale,
} from './myEvaluationsPhone';

/*
 * Телефонный вид «Моих оценок» — только разметка.
 *
 * На компьютере раздел — карточка с выпадающим «Выбор месяца», полукруг
 * среднего балла, кнопка «Мониторинговая шкала» выдвижной панелью справа и
 * таблица оценок на восемь колонок (на узком экране она подменялась рядом
 * карточек с раскрытием на месте). На экране в 390 px это давало таблицу с
 * горизонтальной прокруткой, панель шкалы шириной во весь экран без шапки и
 * карточку оценки, внутри которой раскрывалась вторая, вложенная таблица
 * критериев. Здесь раздел — список настроек телефона, как «Мои часы»:
 * крупный заголовок, месяц стрелками, балл карточкой с полукругом, оценки
 * строками, а оценка и шкала — экранами.
 *
 * Данные и решения (средний балл, статус запроса на переоценку, ссылка на
 * запись разговора, шкала направления) считает App.jsx теми же помощниками,
 * что и настольный вид; что показать по оценке — myEvaluationsPhone.js.
 *
 * ЦВЕТ — утилитами Tailwind, а не своими hex в CSS: тёмный слой портала
 * перекрашивает утилиты, а собственные цвета файла стилей он не видит.
 *
 * Ловушки общего слоя разделов (mobile-shell.css): ряды с gap-* и
 * justify-between переносятся, .flex.items-start с .flex-1 внутри встаёт
 * колонкой, многоколоночные сетки сводятся к двум колонкам, min-w-[…]
 * обнуляется. Поэтому строки выровнены по центру, перенос запрещён инлайном,
 * а сводка по видам оценок собрана строкой, а не сеткой плиток.
 */

const NO_WRAP = { flexWrap: 'nowrap' };

const KIND_ICONS = { call: Phone, chat: MessageSquareText, test: GraduationCap };
const KIND_TILES = { call: 'bg-slate-400', chat: 'bg-blue-500', test: 'bg-indigo-500' };

export const MyEvaluationsPhoneHeader = ({ direction = '', month, onMonthChange, disabled = false, children }) => {
  /* Шапка общая с «Моими часами» (AuctionPhoneMonthHeader): заголовок, под ним
     стрелки парой и подпись месяца с системным колесом. Шаг стрелки — по тому
     же списку месяцев, что в «Выборе месяца» на компьютере. */
  const prev = shiftPhoneMonth(month, -1);
  const next = shiftPhoneMonth(month, 1);
  return (
    <AuctionPhoneMonthHeader
      title="Мои оценки"
      subtitle={direction}
      month={month}
      label={formatPhoneMonth(month)}
      onMonthChange={onMonthChange}
      onPrev={() => prev && onMonthChange?.(prev)}
      onNext={() => next && onMonthChange?.(next)}
      prevDisabled={!prev}
      nextDisabled={!next}
      disabled={disabled}
    >
      {children}
    </AuctionPhoneMonthHeader>
  );
};

export const MyEvaluationsPhoneSkeleton = () => (
  <div className="flex flex-col gap-4" aria-hidden="true">
    <div className={`${iosCard} h-[116px] animate-pulse`} />
    <div className={`${iosCard} h-[112px] animate-pulse`} />
    <div className={`${iosCard} h-[280px] animate-pulse`} />
  </div>
);

const MyEvaluationsPhoneEmpty = () => (
  <div className={`${iosCard} px-4 py-8 text-center text-[16px] text-slate-500`}>
    Нет оценок за этот месяц
  </div>
);

/* Строка «подпись — значение», как в настройках телефона. */
const ValueRow = ({ title, value = null, badge = null, note = null, lines = [] }) => (
  <div className="sa-m-row px-4 py-3">
    <div className="flex items-center gap-2.5" style={NO_WRAP}>
      <span className="min-w-0 flex-1 text-[16px] text-slate-900">{title}</span>
      {badge}
      {value !== null && value !== undefined ? <span className="shrink-0 text-[16px] tabular-nums text-slate-500">{value}</span> : null}
    </div>
    {note ? <p className="mt-1 text-[13px] leading-snug text-slate-500">{note}</p> : null}
    {lines.filter(Boolean).map((line, index) => (
      // Строки пояснения статичны и не переставляются — индекс здесь честный ключ.
      // eslint-disable-next-line react/no-array-index-key
      <p key={index} className="mt-1 whitespace-pre-wrap break-words text-[14px] leading-snug text-slate-600">{line}</p>
    ))}
  </div>
);

/* Средний балл: полукруг с числом внутри, слева — за что он и сколько оценок.
   Второй раз число не повторяется: на компьютере рядом с полукругом стоит та же
   цифра, и на телефоне это читалось бы как два разных показателя. */
const ScoreCard = ({ average, total, kinds }) => {
  const tone = evaluationScoreTone(average);
  // Месяц без оценок: прочерк, а не «0» — нуля за качество никто не получал.
  const text = total > 0 ? formatEvaluationScore(average) : '—';
  return (
    <section className={`${iosCard} px-4 pb-3 pt-3.5`}>
      <div className="flex items-center gap-3" style={NO_WRAP}>
        <div className="min-w-0 flex-1">
          <p className="text-[17px] font-semibold text-slate-900">Средний балл</p>
          <p className="mt-0.5 text-[14px] tabular-nums text-slate-500">{formatEvaluationsCount(total)} за месяц</p>
        </div>
        <AuctionPhoneGauge percent={average} tone={tone} text={text} ariaLabel={`Средний балл: ${text}`} />
      </div>
      {kinds.length > 0 ? (
        <div className="mt-2 flex items-center gap-3 border-t border-slate-100 pt-2.5 text-[13px] tabular-nums text-slate-500" style={NO_WRAP}>
          {kinds.map((kind) => (
            <span key={kind.key} className="inline-flex items-center gap-1.5 truncate" style={NO_WRAP}>
              <span className={`h-2 w-2 shrink-0 rounded-full ${KIND_TILES[kind.key]}`} aria-hidden="true" />
              {kind.label} {kind.count}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
};

/* Назначенная повторная проверка — плашка с одним сроком: что именно проверят и
   почему её назначили, сотруднику не приходит вовсе (сервер отдаёт урезанную
   карточку). */
const CheckpointCard = ({ checkpoint }) => (
  <div className={`${iosCard} flex items-center gap-3 px-4 py-3`} style={NO_WRAP}>
    <span className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg bg-amber-500 text-white">
      <ShieldAlert size={16} aria-hidden="true" />
    </span>
    <span className="min-w-0 flex-1">
      <span className="block text-[15px] font-semibold leading-snug text-slate-900">Повторная проверка качества</span>
      <span className="block text-[13px] tabular-nums text-slate-500">
        {formatEvaluationDay(checkpoint.dueDate)}{checkpoint.when ? ` · ${checkpoint.when}` : ''}
      </span>
      {checkpoint.focus ? <span className="mt-0.5 block text-[13px] leading-snug text-slate-500">Подготовить: {checkpoint.focus}</span> : null}
    </span>
  </div>
);

/* Строка оценки: балл кружком, за ним — что оценено и когда. */
const EvaluationRow = ({ row, subtitle, requestMeta, onOpen }) => {
  const Icon = KIND_ICONS[row.kind] || Phone;
  const score = formatEvaluationScore(row.score);
  const body = (
    <>
      <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-full text-[15px] font-bold tabular-nums ${SCORE_CHIP[row.tone] || SCORE_CHIP.slate}`}>
        {score}
      </span>
      <span className="min-w-0 flex-1">
        <span className={`block truncate text-[16px] font-semibold ${row.isImported ? 'text-slate-400' : 'text-slate-900'}`}>{row.title}</span>
        <span className="mt-0.5 flex items-center gap-1.5 text-[13px] text-slate-500" style={NO_WRAP}>
          <Icon size={13} className="shrink-0 text-slate-400" aria-hidden="true" />
          <span className="truncate">{subtitle}</span>
        </span>
        {requestMeta?.status !== 'none' || row.isImported ? (
          <span className="mt-1.5 flex items-center gap-1.5" style={NO_WRAP}>
            {row.isImported ? <IosBadge tone="slate">Не оценено</IosBadge> : null}
            {requestMeta?.status !== 'none' ? <IosBadge tone={REQUEST_TONES[requestMeta.status] || 'slate'}>{requestMeta.label}</IosBadge> : null}
          </span>
        ) : null}
      </span>
      {row.isImported ? null : (
        <svg width="8" height="14" viewBox="0 0 8 14" fill="none" className="shrink-0 text-slate-300" aria-hidden="true">
          <path d="M1 1l5.5 6L1 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </>
  );
  const className = 'sa-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left';
  if (row.isImported) return <div className={className}>{body}</div>;
  return (
    <button type="button" onClick={onOpen} className={className} aria-label={`Оценка ${score}: ${row.title}`}>
      {body}
    </button>
  );
};

const REQUEST_TONES = { pending: 'amber', approved: 'green', rejected: 'red', none: 'slate' };

/* Балл кружком в строке списка: цвет — тон балла, те же пороги, что красят
   число в таблице на компьютере. */
const SCORE_CHIP = {
  green: 'bg-green-50 text-green-700 ring-1 ring-green-200',
  amber: 'bg-amber-50 text-amber-700 ring-1 ring-amber-200',
  red: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200',
  slate: 'bg-slate-100 text-slate-500 ring-1 ring-slate-200',
};

/* Экран оценки: комментарий, критерии, чат или запись, запрос на переоценку. */
const EvaluationScreen = ({
  row,
  formatDate,
  requestMeta,
  granted,
  audio,
  onOpenChat,
}) => {
  const evaluation = row?.evaluation;
  if (!evaluation) return null;
  const criteria = buildEvaluationCriteria(evaluation);
  const verdicts = summarizeEvaluationCriteria(criteria);
  const test = buildEvaluationTest(evaluation);
  const quotes = evaluationChatQuotes(evaluation);
  const comment = String(evaluation.comment || '').trim();

  return (
    <div className="flex flex-col gap-5">
      <section className={`${iosCard} flex items-center gap-3 px-4 py-3.5`} style={NO_WRAP}>
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] text-slate-500">Балл за {row.typeLabel.toLowerCase()}</span>
          <span className={`mt-0.5 block text-[34px] font-bold leading-[40px] tracking-tight tabular-nums ${SCORE_TEXT[row.tone] || SCORE_TEXT.slate}`}>
            {formatEvaluationScore(row.score)}
          </span>
        </span>
        {verdicts.length > 0 ? (
          <span className="flex shrink-0 flex-col gap-1">
            {verdicts.map((verdict) => (
              <IosBadge key={verdict.key} tone={verdict.tone}>{verdict.label} {verdict.count}</IosBadge>
            ))}
          </span>
        ) : null}
      </section>

      {comment ? (
        <AuctionPhoneGroup label="Комментарий оценщика">
          <div className="sa-m-row px-4 py-3">
            <p className="whitespace-pre-wrap break-words text-[15px] leading-snug text-slate-900">{comment}</p>
          </div>
        </AuctionPhoneGroup>
      ) : null}

      {test ? (
        <AuctionPhoneGroup
          label="Тестирование знаний"
          hint="Влияет на средний балл качества, но прослушанным звонком не считается."
        >
          <ValueRow title={test.title} />
          <ValueRow title="Баллы за вопросы" value={test.points} />
          <ValueRow title="Результат" value={test.result} />
          {test.autoSubmitted ? <ValueRow title="Отправлен автоматически" note="Время теста истекло." /> : null}
        </AuctionPhoneGroup>
      ) : null}

      {evaluation.c2d_snapshot_id ? (
        <AuctionPhoneGroup
          label="Оценённый чат"
          hint={granted ? (quotes.length > 0 ? 'Нажмите на цитату — переписка откроется на этом сообщении.' : null) : null}
        >
          {granted ? (
            <AuctionPhoneLinkRow
              icon={MessageSquareText}
              tileClassName="bg-blue-500"
              title="Открыть переписку"
              subtitle={quotes.length > 0 ? `Цитат супервайзера: ${quotes.length}` : null}
              onClick={() => onOpenChat?.(evaluation, null)}
            />
          ) : (
            <ValueRow title="Переписка скрыта" note="Откроется после QR-подтверждения доступа." />
          )}
          {granted
            ? quotes.map((quote, index) => (
              <button
                // Цитаты приходят списком в порядке комментариев — индекс здесь честный ключ.
                // eslint-disable-next-line react/no-array-index-key
                key={index}
                type="button"
                onClick={() => onOpenChat?.(evaluation, quote)}
                className="sa-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left"
              >
                <Quote size={15} className="shrink-0 text-amber-500" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block text-[15px] leading-snug text-slate-900">«{quote.text}»</span>
                  {quote.comment ? <span className="mt-0.5 block text-[13px] leading-snug text-slate-500">{quote.comment}</span> : null}
                </span>
              </button>
            ))
            : null}
        </AuctionPhoneGroup>
      ) : null}

      {criteria.length > 0 ? (
        <AuctionPhoneGroup label="Критерии">
          {criteria.map((criterion) => {
            const verdict = evaluationVerdict(criterion.status);
            const marks = [
              criterion.isCritical ? 'Критичный' : null,
              criterion.weight !== null && !criterion.isCritical ? `Вес ${criterion.weight}%` : null,
            ].filter(Boolean).join(' · ');
            return (
              <ValueRow
                key={criterion.key}
                title={`${criterion.number}. ${criterion.name}`}
                badge={<IosBadge tone={verdict.tone}>{verdict.label}</IosBadge>}
                note={marks || null}
                lines={[criterion.comment]}
              />
            );
          })}
        </AuctionPhoneGroup>
      ) : null}

      {audio ? (
        <AuctionPhoneGroup label="Запись разговора">
          <div className="sa-m-row px-4 py-3">{audio}</div>
        </AuctionPhoneGroup>
      ) : null}

      {requestMeta.status !== 'none' ? (
        <AuctionPhoneGroup label="Запрос на переоценку">
          <ValueRow
            title="Статус"
            badge={<IosBadge tone={REQUEST_TONES[requestMeta.status] || 'slate'}>{requestMeta.label}</IosBadge>}
          />
          {evaluation.sv_request_by_name ? <ValueRow title="Инициатор" value={evaluation.sv_request_by_name} /> : null}
          {evaluation.sv_request_comment ? <ValueRow title="Ваш комментарий" lines={[evaluation.sv_request_comment]} /> : null}
          {evaluation.sv_request_approved && evaluation.sv_request_approve_comment
            ? <ValueRow title="Комментарий при одобрении" lines={[evaluation.sv_request_approve_comment]} />
            : null}
          {evaluation.sv_request_reject_comment
            ? <ValueRow title="Причина отклонения" lines={[evaluation.sv_request_reject_comment]} />
            : null}
        </AuctionPhoneGroup>
      ) : null}

      <AuctionPhoneGroup label="Об оценке">
        <ValueRow title="Оценщик" value={evaluation.evaluator || '—'} />
        <ValueRow title="Дата обращения" value={formatDate(evaluation.appeal_date || evaluation.created_at)} />
        <ValueRow title="Дата оценки" value={formatDate(evaluation.evaluation_date)} />
        {evaluation.month ? <ValueRow title="Месяц" value={evaluation.month} /> : null}
      </AuctionPhoneGroup>
    </div>
  );
};

const SCORE_TEXT = {
  green: 'text-green-600',
  amber: 'text-amber-600',
  red: 'text-red-600',
  slate: 'text-slate-500',
};

/* Экран мониторинговой шкалы: направление строкой с системным списком, сводка
   тремя строками и критерии, которые раскрываются нажатием. На компьютере это
   панель, выезжающая справа; на телефоне она занимала весь экран и всё равно
   оставалась без шапки и без «назад». */
const MonitoringScaleScreen = ({ scale }) => {
  const criteria = Array.isArray(scale.criteria) ? scale.criteria : [];
  const summary = summarizeMonitoringScale(criteria);
  const directions = Array.isArray(scale.directions) ? scale.directions : [];
  return (
    <div className="flex flex-col gap-5">
      {directions.length > 1 ? (
        <AuctionPhoneGroup label="Направление">
          <label className="sa-m-row relative flex w-full items-center gap-2 px-4 py-3" style={NO_WRAP}>
            <span className="min-w-0 flex-1 truncate text-[16px] text-slate-900">{scale.directionName || 'Без названия'}</span>
            <svg width="8" height="14" viewBox="0 0 8 14" fill="none" className="shrink-0 text-slate-300" aria-hidden="true">
              <path d="M1 1l5.5 6L1 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <select
              className="sa-m-wheel"
              value={scale.selectedKey}
              onChange={(event) => scale.onSelectDirection?.(event.target.value)}
              aria-label="Направление"
            >
              {directions.map((direction, index) => (
                <option key={String(direction?._monitoringKey || index)} value={String(direction?._monitoringKey || '')}>
                  {String(direction?.name || 'Без названия')}
                </option>
              ))}
            </select>
          </label>
        </AuctionPhoneGroup>
      ) : null}

      {criteria.length > 0 ? (
        <AuctionPhoneGroup label="Шкала" hint="Нажмите на критерий — под ним раскроется описание.">
          <ValueRow title="Критериев" value={summary.total} />
          <ValueRow title="Сумма весов" value={`${summary.weight}%`} />
          <ValueRow title="Критичных" value={summary.critical} />
        </AuctionPhoneGroup>
      ) : (
        <div className={`${iosCard} px-4 py-6 text-center text-[15px] text-slate-500`}>
          В этом направлении пока нет критериев.
        </div>
      )}

      {criteria.length > 0 ? (
        <AuctionPhoneGroup label="Критерии">
          {criteria.map((criterion, index) => {
            const isOpen = scale.openIndex === index;
            const weight = Math.max(0, Math.min(100, Number(criterion?.weight) || 0));
            const description = String(criterion?.value || '').trim();
            const deficiency = criterion?.deficiency;
            return (
              <div key={`${criterion?.id ?? criterion?.name ?? 'criterion'}-${index}`} className="sa-m-row px-4 py-3">
                <button
                  type="button"
                  onClick={() => scale.onToggleCriterion?.(index)}
                  className="flex w-full items-center gap-2.5 text-left"
                  style={NO_WRAP}
                >
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-blue-100 text-[12px] font-bold tabular-nums text-blue-700">
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1 text-[16px] leading-snug text-slate-900">{criterion?.name || `Критерий ${index + 1}`}</span>
                  <IosBadge tone={criterion?.isCritical ? 'red' : 'green'}>
                    {criterion?.isCritical ? 'Критичный' : `${weight}%`}
                  </IosBadge>
                </button>
                {isOpen ? (
                  <>
                    <p className="mt-2 whitespace-pre-wrap break-words text-[14px] leading-snug text-slate-600">
                      {description || 'Описание не заполнено.'}
                    </p>
                    {deficiency ? (
                      <p className="mt-2 text-[13px] leading-snug text-amber-700">
                        Недочёт: {Math.max(0, Number(deficiency.weight) || 0)}%
                        {String(deficiency.description || '').trim() ? ` — ${String(deficiency.description).trim()}` : ''}
                      </p>
                    ) : null}
                  </>
                ) : null}
              </div>
            );
          })}
        </AuctionPhoneGroup>
      ) : null}
    </div>
  );
};

export const MyEvaluationsPhone = ({
  evaluations = [],
  average = 0,
  formatDate,
  requestMetaOf,
  access = null,
  checkpoint = null,
  lowRatings = null,
  scale = null,
  audio = null,
  onOpenEvaluation,
  onOpenChat,
  onRequestReevaluation,
}) => {
  const [openId, setOpenId] = useState(null);
  const rows = useMemo(() => buildEvaluationRows(evaluations), [evaluations]);
  const kinds = useMemo(() => summarizeEvaluationKinds(evaluations), [evaluations]);

  const openRow = openId === null ? null : rows.find((row) => row.key === openId) || null;
  // Экран уезжает ещё треть секунды после закрытия — пусть уезжает с той же оценкой.
  const shownRow = useLastPresent(openRow);
  const shownMeta = shownRow ? requestMetaOf(shownRow.evaluation) : null;
  const canRequest = shownRow ? canRequestReevaluation(shownRow.evaluation, shownMeta?.status) : false;

  const openEvaluation = (row) => {
    setOpenId(row.key);
    onOpenEvaluation?.(row.evaluation);
  };

  /* Запись разговора грузится только для раскрытой оценки: у теста и у чата её
     нет вовсе, а до QR-подтверждения сервер её и не отдаёт. */
  const audioNode = (() => {
    if (!shownRow || shownRow.kind !== 'call') return null;
    if (!audio?.granted) {
      return (
        <span className="flex items-center gap-2 text-[15px] text-amber-600" style={NO_WRAP}>
          <ShieldAlert size={16} className="shrink-0" aria-hidden="true" />
          Скрыта до QR-подтверждения доступа
        </span>
      );
    }
    if (audio.loading) {
      return (
        <span className="flex items-center gap-2 text-[15px] text-slate-500" style={NO_WRAP}>
          <Loader2 size={16} className="shrink-0 animate-spin" aria-hidden="true" />
          Загружаем запись…
        </span>
      );
    }
    if (audio.player && String(audio.evaluationId) === String(shownRow.evaluation?.id)) return audio.player;
    return <span className="text-[15px] text-slate-500">Файлов не имеется</span>;
  })();

  return (
    <>
      {checkpoint ? <CheckpointCard checkpoint={checkpoint} /> : null}

      <ScoreCard average={average} total={rows.length} kinds={kinds} />

      <AuctionPhoneGroup hint={access && !access.granted ? 'Покажите QR-код супервайзеру или администратору — он откроет телефоны, записи разговоров и переписки.' : null}>
        {scale ? (
          <AuctionPhoneLinkRow
            icon={SlidersHorizontal}
            tileClassName="bg-blue-500"
            title="Мониторинговая шкала"
            subtitle={scale.directionName || null}
            onClick={scale.onOpen}
          />
        ) : null}
        {access && !access.granted ? (
          <AuctionPhoneLinkRow
            icon={ShieldAlert}
            tileClassName="bg-amber-500"
            title="Сгенерировать QR-код"
            subtitle={access.loading ? 'Проверяем доступ…' : 'Записи и переписки закрыты'}
            onClick={access.onRequestQr}
          />
        ) : null}
        {access && access.granted ? (
          <div className="sa-m-row flex w-full items-center gap-3 px-4 py-2.5">
            <span className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg bg-green-500 text-white">
              <ShieldCheck size={17} aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[16px] text-slate-900">Доступ открыт</span>
              <span className="block truncate text-[13px] text-slate-500">Записи и переписки видны до конца сессии</span>
            </span>
          </div>
        ) : null}
      </AuctionPhoneGroup>

      {lowRatings}

      {rows.length === 0 ? (
        <MyEvaluationsPhoneEmpty />
      ) : (
        <AuctionPhoneGroup
          label="Оценки"
          right={<span className="shrink-0 text-[13px] tabular-nums text-slate-500">{formatEvaluationsCount(rows.length)}</span>}
        >
          {rows.map((row) => (
            <EvaluationRow
              key={row.key}
              row={row}
              subtitle={`${row.typeLabel} · ${formatDate(row.evaluation.appeal_date || row.evaluation.created_at)}`}
              requestMeta={requestMetaOf(row.evaluation)}
              onOpen={() => openEvaluation(row)}
            />
          ))}
        </AuctionPhoneGroup>
      )}

      <IosModal
        open={Boolean(openRow)}
        onClose={() => setOpenId(null)}
        title={shownRow?.title || ''}
        subtitle={shownRow ? `${shownRow.typeLabel} · оценка от ${formatDate(shownRow.evaluation.evaluation_date)}` : null}
        footer={canRequest ? (
          <button
            type="button"
            onClick={() => onRequestReevaluation?.(shownRow.evaluation)}
            className={AUCTION_PHONE_BUTTON.orange}
          >
            <UserRound size={18} aria-hidden="true" />
            {shownMeta?.status === 'rejected' ? 'Повторить запрос' : 'Запросить переоценку'}
          </button>
        ) : null}
      >
        {shownRow ? (
          <EvaluationScreen
            row={shownRow}
            formatDate={formatDate}
            requestMeta={shownMeta}
            granted={Boolean(audio?.granted)}
            audio={audioNode}
            onOpenChat={onOpenChat}
          />
        ) : null}
      </IosModal>

      {scale ? (
        <IosModal
          open={Boolean(scale.open)}
          onClose={scale.onClose}
          title="Мониторинговая шкала"
          subtitle={scale.directionName || null}
        >
          <MonitoringScaleScreen scale={scale} />
        </IosModal>
      ) : null}
    </>
  );
};
