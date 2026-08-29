import { normalizeRole } from '../../utils/roles.js';

export const SHIFT_AUCTION_DIRECTION_NAME = 'основа';
// У чата направление называется «Чат менеджер», но имена направлений в компании
// дублируются, поэтому сверяем по вхождению — тем же шаблоном, что и сервер (%чат%).
export const SHIFT_AUCTION_CHAT_DIRECTION_TOKEN = 'чат';
export const SHIFT_AUCTION_MODE_LINE = 'line';
export const SHIFT_AUCTION_MODE_CHAT = 'chat';

const normalizeScopeText = (value) => (
  String(value || '')
    .trim()
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase('ru-RU')
);

export const normalizeShiftAuctionOperatorId = (value) => {
  const id = Number(value);
  return Number.isFinite(id) && id > 0 ? id : null;
};

export const normalizeShiftAuctionDirectionMode = (value) => (
  normalizeScopeText(value) === SHIFT_AUCTION_MODE_CHAT
    ? SHIFT_AUCTION_MODE_CHAT
    : SHIFT_AUCTION_MODE_LINE
);

/**
 * Принадлежит ли человек направлению ЭТОГО аукциона.
 *
 * Списки участников у линии и чата не пересекаются: в чат-аукционе выбирать надо
 * из чатников, а не из операторов линии. Границу отдела здесь не проверить —
 * в карточках, которые приходит на фронт, отдела нет; её держит сервер
 * (shift_auction_direction_scope_sql: %чат% И dep.code = 'szov'), поэтому
 * «ТП чат» отдела Тез сюда попасть может, а в аукцион — уже нет.
 */
export const isShiftAuctionDirection = (value, directionMode = SHIFT_AUCTION_MODE_LINE) => {
  const name = normalizeScopeText(value);
  if (normalizeShiftAuctionDirectionMode(directionMode) === SHIFT_AUCTION_MODE_CHAT) {
    return name.includes(SHIFT_AUCTION_CHAT_DIRECTION_TOKEN);
  }
  return name === SHIFT_AUCTION_DIRECTION_NAME;
};

export const isDismissedShiftAuctionOperator = (value) => {
  const status = normalizeScopeText(value);
  return status === 'fired' || status === 'dismissal' || status === 'dismissed';
};

export const isActiveShiftAuctionOperator = (operatorOrStatus) => {
  const status = typeof operatorOrStatus === 'object'
    ? operatorOrStatus?.status
    : operatorOrStatus;
  return normalizeScopeText(status) === 'working';
};

export const getShiftAuctionOperatorStatusLabel = (value) => {
  const status = normalizeScopeText(value);
  const labels = {
    working: 'Активен',
    bs: 'Б/С',
    unpaid_leave: 'Б/С',
    sick_leave: 'Больничный',
    annual_leave: 'Отпуск',
  };
  return labels[status] || (status ? String(value) : '');
};

const operatorStatusPriority = (value) => {
  const status = normalizeScopeText(value);
  if (!status) return 0;
  if (isDismissedShiftAuctionOperator(status)) return 100;
  if (status === 'bs' || status === 'unpaid_leave') return 80;
  if (status === 'sick_leave') return 70;
  if (status === 'annual_leave') return 60;
  if (status === 'working') return 10;
  return 50;
};

const mergeOperatorStatus = (previousStatus, nextStatus) => (
  operatorStatusPriority(nextStatus) >= operatorStatusPriority(previousStatus)
    ? (nextStatus ?? previousStatus ?? '')
    : (previousStatus ?? nextStatus ?? '')
);

export const normalizeShiftAuctionOperators = (
  operators = [],
  selectedOperators = [],
  directionMode = SHIFT_AUCTION_MODE_LINE,
) => {
  const rows = new Map();
  const roleConflicts = new Set();
  // Snapshot and the App employee directory refresh independently. Display
  // fields may use the directory row processed last, but restrictive status,
  // role and direction data is merged fail-closed so stale `working` can never
  // overwrite a Б/С status from the other source.
  const sources = [
    ...(Array.isArray(selectedOperators) ? selectedOperators : []),
    ...(Array.isArray(operators) ? operators : []),
  ];

  sources.forEach((operator) => {
    const id = normalizeShiftAuctionOperatorId(operator?.id ?? operator?.operator_id);
    if (!id) return;
    const role = normalizeRole(operator?.role || 'operator');
    if (role && role !== 'operator') {
      roleConflicts.add(id);
      rows.delete(id);
      return;
    }
    if (roleConflicts.has(id)) return;

    const previous = rows.get(id) || {};
    const previousDirection = previous.direction ?? '';
    const nextDirection = operator?.direction ?? operator?.direction_name;
    const hasDirectionConflict = Boolean(
      previous.scope_conflict
      || (
        previousDirection
        && nextDirection
        && normalizeScopeText(previousDirection) !== normalizeScopeText(nextDirection)
      )
    );
    rows.set(id, {
      id,
      name: operator?.name || previous.name || `Оператор #${id}`,
      direction: nextDirection ?? previousDirection,
      direction_id: operator?.direction_id ?? previous.direction_id ?? null,
      supervisor_name: operator?.supervisor_name ?? previous.supervisor_name ?? '',
      rate: Number(operator?.rate ?? previous.rate ?? 1),
      status: mergeOperatorStatus(previous.status, operator?.status),
      scope_conflict: hasDirectionConflict,
    });
  });

  return Array.from(rows.values())
    // The selector intentionally keeps temporary non-working statuses such as
    // Б/С. Only dismissed employees are unavailable for future participation.
    .filter((operator) => !operator.scope_conflict && isShiftAuctionDirection(operator.direction, directionMode))
    .filter((operator) => !isDismissedShiftAuctionOperator(operator.status))
    .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'ru'));
};

export const filterOperationalShiftAuctionOperators = (
  operators = [],
  directionMode = SHIFT_AUCTION_MODE_LINE,
) => (
  (Array.isArray(operators) ? operators : [])
    .filter((operator) => (
      operator
      && operator.id != null
      && !operator.scope_conflict
      && isShiftAuctionDirection(operator.direction ?? operator.direction_name, directionMode)
      && isActiveShiftAuctionOperator(operator)
    ))
);

const comparableServerTimestamp = (value) => {
  const match = String(value || '').trim().match(
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?$/
  );
  if (!match) return null;
  return `${match[1]}.${String(match[2] || '').padEnd(9, '0')}`;
};

const isTimestampAtOrAfter = (candidate, boundary) => {
  const candidateComparable = comparableServerTimestamp(candidate);
  const boundaryComparable = comparableServerTimestamp(boundary);
  if (candidateComparable && boundaryComparable) {
    return candidateComparable >= boundaryComparable;
  }
  const candidateMs = Date.parse(String(candidate || ''));
  const boundaryMs = Date.parse(String(boundary || ''));
  if (Number.isFinite(candidateMs) && Number.isFinite(boundaryMs)) {
    return candidateMs >= boundaryMs;
  }
  return String(candidate || '') >= String(boundary || '');
};

export const shouldHydrateShiftAuctionDraft = ({
  dirty = false,
  snapshotUpdatedAt = '',
  pendingSavedAt = '',
} = {}) => {
  if (dirty) return false;
  if (!pendingSavedAt) return true;
  if (!snapshotUpdatedAt) return false;
  return isTimestampAtOrAfter(snapshotUpdatedAt, pendingSavedAt);
};
