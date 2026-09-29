"""Смены Clockster из ленты отметок человека (ТЗ iCore 3, п. 4).

Раньше раздел «Отметки» брал готовые `in`/`out` из клетки Clockster. Клетка —
календарный день и тип отметки, который угадал терминал, поэтому выгрузка по
Клокстеру врала в двух местах:
  * ночная смена рвалась на два дня: приход 21:55 в одном, уход 08:18 в другом,
    и оба дня получали ноль часов;
  * терминал один на вход и выход и тип отметки угадывает: «10:45 приход,
    20:27 приход» давало приход без ухода, то есть ноль часов.

Модуль без сети и без базы: только сведение ленты отметок одного человека в
смены, чтобы правило проверялось тестами на живых последовательностях.

Правило проверено на месяце реальных отметок (28.08–28.09.2026: 61 человек,
1435 отметок). Терминал путает тип редко, примерно в 4% отметок, а вот
одиночная отметка за день — у трети дней. Поэтому «первая — приход, следующая —
уход» по всей ленте подряд не годится: одна пропущенная отметка сдвигала бы все
пары дальше, и вечерний уход без прихода склеивался с утренним приходом
следующего дня в ложную ночную смену (18 таких за месяц). Отсюда:

1. Повторное касание. Отметки подряд в пределах `TOUCH_SECONDS` — одна отметка:
   человек приложил палец дважды или администратор поправил тип сразу после.
2. День с графиком. Всё, что попало в окно смены Clockster («начало − 3 ч» …
   «конец + 4 ч», это границы из самого расписания), — одна смена. Тип решает
   ПОРЯДОК, а не терминал: первая отметка — приход, последняя — уход, между ними
   попеременно. Ночная смена по графику (17:00–02:00) целиком в окне.
3. День без графика. Отметки календарного дня — одна смена тем же порядком.
   Одинокий приход накануне закрывает отметка следующего дня:
     * до `NIGHT_OUT_BEFORE` (06:00) — всегда: вечерняя смена до 00:00–02:00;
     * до `NIGHT_OUT_LATEST` (12:00) — только если это ЕДИНСТВЕННАЯ отметка
       того дня и терминал назвал её уходом: ночная смена 19:25 → 09:01.
   Утренняя отметка 08:18, за которой в тот же день есть вечерняя 21:54, —
   начало нового дня, а не ночной уход. Тип терминала сам по себе не
   доказательство: Clockster ставит его переключателем — после «прихода»
   следующая отметка «уход». Пропустил человек утреннюю отметку, и вечерний уход
   становится «приходом», утренний приход следующего дня — «уходом», и так
   несколько дней подряд. На месяце так выглядели «ночные смены» руководителя HR,
   IT-специалиста и СВ ОП, которые работают днём 08–09 → 21: у них за утренней
   отметкой всегда идёт вечерняя того же дня. Цена правила: дневной работник,
   который отметился только вечером, а на следующий день только утром, получит
   ложную ночную смену (на месяце — один такой день).
4. Смена длиннее `MAX_SHIFT_SECONDS` не складывается: ранние отметки дня уходят
   в «приход без ухода», а не превращаются в сутки работы.
5. Отметка до `ORPHAN_OUT_BEFORE` (04:00), которой нечего закрыть, — уход без
   прихода (хвост вчерашней смены), а не приход в 00:03. Граница раньше, чем у
   ночного ухода: за месяц все уходы после полуночи были не позже 03:01, а
   приход в 05:45 без графика должен остаться приходом.
6. Единственная отметка дня с графиком — приход («первая — приход»): пришедший в
   13:40 при графике 09:00–18:00 опоздал, а не прогулял. Уходом она считается,
   только если стоит ПОСЛЕ конца графика и её не закрывает ни более поздняя
   отметка того же дня, ни утро следующего (ночная смена при дневном графике).

Смена относится ко дню прихода — как смены в графиках проекта.
"""

from datetime import date as date_cls, datetime, time, timedelta

# Повторное касание терминала. За месяц пар отметок ближе 5 минут — 12, и все
# они двойные касания или поправка администратора; дальше идут короткие выходы.
TOUCH_SECONDS = 5 * 60
# Самая длинная правдоподобная смена — как у часов СВ (#352).
MAX_SHIFT_SECONDS = 16 * 3600
# Отметка следующего дня закрывает одинокий приход накануне до этого часа всегда.
NIGHT_OUT_BEFORE = time(6, 0)
# …а до этого — только единственная отметка дня, названная терминалом уходом.
NIGHT_OUT_LATEST = time(12, 0)
# Отметка, которой нечего закрыть, до этого часа — уход без прихода.
ORPHAN_OUT_BEFORE = time(4, 0)
# Границы смены Clockster, если расписание их не дало (в данных у всех 3 и 4 ч).
DEFAULT_BOUNDARY_START_HOURS = 3
DEFAULT_BOUNDARY_END_HOURS = 4

# Соглашение Workpace, к которому приведены отметки: 0 — вход, 1 — выход.
IN = 0
OUT = 1


class _Cluster:
    """Одна или несколько отметок подряд — одно касание терминала."""

    def __init__(self, mark):
        self.marks = [mark]

    @property
    def start(self):
        return self.marks[0]['at']

    @property
    def end(self):
        return self.marks[-1]['at']


class Shift:
    """Смена: день прихода, приход, уход и отметки с ролью по порядку."""

    def __init__(self, day, clusters, planned=False, departure_only=False):
        self.day = day
        self.clusters = list(clusters)
        self.planned = planned
        self.departure_only = departure_only
        # Одинокая отметка после конца графика: уход, если её ничто не закроет.
        self.maybe_departure = False

    @property
    def arrival(self):
        if self.departure_only or not self.clusters:
            return None
        return self.clusters[0].start

    @property
    def departure(self):
        if self.departure_only:
            return self.clusters[-1].end
        if len(self.clusters) < 2:
            return None
        return self.clusters[-1].end

    @property
    def is_open(self):
        return not self.departure_only and len(self.clusters) == 1

    def roles(self):
        """[(отметка, роль)]: первая — приход, последняя — уход, между ними попеременно."""
        out = []
        last = len(self.clusters) - 1
        for index, cluster in enumerate(self.clusters):
            if self.departure_only:
                role = OUT
            elif index == 0:
                role = IN
            elif index == last:
                role = OUT
            else:
                role = OUT if index % 2 else IN
            out.extend((mark, role) for mark in cluster.marks)
        return out


def _as_day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()


def _clusters(marks):
    out = []
    for mark in marks:
        if out and (mark['at'] - out[-1].end).total_seconds() <= TOUCH_SECONDS:
            out[-1].marks.append(mark)
        else:
            out.append(_Cluster(mark))
    return out


def _closes_night(open_shift, cluster, alone_that_day):
    """Отметка следующего дня закрывает одинокий приход накануне (п. 3)."""
    arrival = open_shift.arrival
    if arrival is None:
        return False
    if cluster.start.date() != arrival.date() + timedelta(days=1):
        return False
    if not 0 < (cluster.start - arrival).total_seconds() <= MAX_SHIFT_SECONDS:
        return False
    moment = cluster.start.time()
    if moment < NIGHT_OUT_BEFORE:
        return True
    return (moment < NIGHT_OUT_LATEST and alone_that_day
            and any(mark['terminal'] == OUT for mark in cluster.marks))


def _after_plan_end(moment, plan):
    end = plan[1]
    return end is not None and moment > end


def _settle(shift):
    """Смену ничто не закрыло: одинокая отметка после графика — уход без прихода."""
    if shift is not None and shift.maybe_departure and shift.is_open:
        shift.departure_only = True
    if shift is not None:
        shift.maybe_departure = False


def build_shifts(marks, plans=None):
    """Лента отметок человека → смены.

    `marks` — [{'at': aware datetime, 'terminal': 0|1, ...}] (остальные поля
    возвращаются как есть); `plans` — {день: (начало, конец|None, граница до ч,
    граница после ч)} только для рабочих дней графика.
    Каждая отметка попадает ровно в одну смену."""
    ordered = sorted(marks or [], key=lambda item: (item['at'], item['terminal']))
    clusters = _clusters(ordered)
    plans = plans or {}

    used = set()
    planned = {}
    for day in sorted(plans, key=lambda key: plans[key][0]):
        start, end, before_h, after_h = plans[day]
        low = start - timedelta(hours=DEFAULT_BOUNDARY_START_HOURS if before_h is None else before_h)
        high = (end or start) + timedelta(hours=DEFAULT_BOUNDARY_END_HOURS if after_h is None else after_h)
        inside = [index for index, cluster in enumerate(clusters)
                  if index not in used and low <= cluster.start <= high]
        if inside:
            used.update(inside)
            group = [clusters[i] for i in inside]
            shift = Shift(_as_day(day), group, planned=True)
            shift.maybe_departure = len(group) == 1 and _after_plan_end(group[0].start, plans[day])
            planned[_as_day(day)] = shift

    free_by_day = {}
    for index, cluster in enumerate(clusters):
        if index not in used:
            free_by_day.setdefault(cluster.start.date(), []).append(cluster)

    shifts = []
    open_shift = None
    for day in sorted(set(planned) | set(free_by_day)):
        free = list(free_by_day.get(day, []))
        own = planned.get(day)
        if open_shift is not None and free:
            alone = len(free) == 1 and own is None
            if _closes_night(open_shift, free[0], alone):
                open_shift.clusters.append(free.pop(0))
                open_shift.maybe_departure = False
        _settle(open_shift)
        open_shift = None

        if own is not None:
            shifts.append(own)
            # Уход после окна графика в тот же день закрывает смену, где был
            # только приход, — это переработка, а не новая смена.
            if own.is_open:
                later = [c for c in free if c.start > own.arrival]
                if later:
                    own.clusters.extend(later)
                    own.maybe_departure = False
                    free = [c for c in free if c.start <= own.arrival]

        while free and free[0].start.time() < ORPHAN_OUT_BEFORE:
            shifts.append(Shift(day, [free.pop(0)], departure_only=True))
        while len(free) > 1 and (free[-1].end - free[0].start).total_seconds() > MAX_SHIFT_SECONDS:
            shifts.append(Shift(day, [free.pop(0)]))
        if free:
            shifts.append(Shift(day, free))

        # Открытым на ночь остаётся одинокий приход дня: своя смена графика
        # или единственная отметка дня без графика.
        candidates = [s for s in shifts if s.day == day and not s.departure_only]
        last = candidates[-1] if candidates else None
        if last is not None and last.is_open:
            open_shift = last
        # Одинокая отметка после графика, если на ночь открытой осталась не она,
        # закрыться уже не может.
        if own is not None and own is not open_shift:
            _settle(own)
    _settle(open_shift)
    return shifts


def main_shift(shifts, day):
    """Смена, которая даёт дню приход и уход: по графику, иначе своя смена дня,
    иначе уход без прихода. Остальные отметки дня только показываются."""
    target = _as_day(day)
    own = [s for s in shifts if s.day == target]
    for shift in own:
        if shift.planned:
            return shift
    regular = [s for s in own if not s.departure_only]
    if regular:
        # Из нескольких смен дня без графика — та, у которой есть уход, а из
        # равных — последняя: ранние одинокие отметки её не перебивают.
        closed = [s for s in regular if s.departure is not None]
        return (closed or regular)[-1]
    return own[-1] if own else None
