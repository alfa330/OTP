# -*- coding: utf-8 -*-
"""Фоновый прогон ИИ по пачке объявлений: ход, остановка, один прогон за раз.

Зачем фон, а не длинный запрос. Пачка на кабинет — 30–36 объявлений по 2–6 с
на каждое, то есть 2–4 минуты. Человек столько без признаков жизни не ждёт:
22.09.2026 маркетолог через 92 секунды обновил страницу и нажал снова, и две
пачки по 32 объявления пошли параллельно. Поэтому запрос лишь СТАВИТ прогон и
сразу отвечает его номером, а экран опрашивает ход.

Почему состояние в базе (olx_ads_gen_jobs), а не в памяти процесса. До
25.11.2026 реестр прогонов жил в памяти: на Render сервис стоял одним
экземпляром. После переезда в Yandex Cloud на /api отвечают несколько процессов
(проверено 25.11 снаружи: снимок «Табло СЗоВ», который кэшируется в памяти
процесса, по соседним запросам скакал назад-вперёд — 10 скачков за 30 с). Опрос
хода через раз попадал в процесс, который прогона не знал, и экран писал
«Прогон не найден», хотя прогон шёл; «Остановить» и повторное нажатие уходили
туда же — второе нажатие ставило вторую пачку параллельно первой. Теперь:

  * строка прогона в базе; второй идущий не даёт поставить уникальный индекс
    uniq_olx_ads_gen_job_running — ответ 409 и тот же прогон, экран к нему
    подключается, в какой бы процесс ни попал;
  * ведёт прогон поток процесса, принявшего нажатие. Его аренда — `owner`
    (процесс + случайный хвост): каждая запись хода идёт с условием «прогон ещё
    мой», поэтому поток, у которого прогон забрали, ничего не испортит, даже
    если очнётся;
  * пульс раз в HEARTBEAT_SECONDS. Ведущий молчит дольше ORPHAN_SECONDS — он
    умер (выкладка перезапускает поды, под может упасть); любой живой процесс
    забирает прогон условным UPDATE и продолжает с `position` — с того
    объявления, на котором оборвалось. Подхватов не больше MAX_ATTEMPTS - 1:
    прогон, который роняет процесс сам, не должен ронять его бесконечно;
  * «Остановить» — флаг в строке: до ведущего он доходит из любого процесса.
    Остановка между объявлениями, написанное остаётся;
  * ИИ лёг целиком (все звенья цепочки отказали) — одна пауза AI_RETRY_PAUSE и
    повтор того же объявления: 429 Vertex «Resource exhausted» плавающий (см.
    wiki/ai/providers.py). Не ожил — остаток пачки помечается пропущенным, как
    и раньше, а не гонится по мёртвой цепочке.
"""

import logging
import os
import socket
import threading
import time
import uuid

from . import ai, queries, service

log = logging.getLogger(__name__)

# Пульс ведущего и порог «ведущий умер». Порог — четыре с половиной пропущенных
# удара: база может моргнуть, а лишний подхват стоит лишь одного повторного
# обращения к ИИ (у брошенного потока запись хода уже не пройдёт).
HEARTBEAT_SECONDS = 10.0
ORPHAN_SECONDS = 45.0
# Поток жив, а с места не двигается: цепочка ИИ — до шести звеньев по 60 с, плюс
# пауза на повтор. Дольше этого — повис; пульс замолкает, прогон заберут.
STALL_SECONDS = 900.0
# Первая попытка и два подхвата после перезапуска — дальше прогон закрывается
# с объяснением, а не крутится по кругу.
MAX_ATTEMPTS = 3
# Обход брошенных: по таймеру и из ручек опроса (не чаще SWEEP_MIN_GAP).
SWEEP_SECONDS = 20.0
SWEEP_FIRST_DELAY = 15.0
SWEEP_MIN_GAP = 5.0
AI_RETRY_PAUSE = 30.0

INSTANCE = '%s-%d' % (socket.gethostname()[:60], os.getpid())

INTERRUPTED_TEXT = ('Прогон прервался внутренней ошибкой — обновите список: '
                    'написанное до сбоя сохранено')
RESTARTS_TEXT = ('Прогон прерывали перезапуски сервера — написанное сохранено, '
                 'остальное запустите ещё раз')
SKIPPED_AFTER_AI_TEXT = 'пропущено: ИИ перестал отвечать'


class JobBusy(RuntimeError):
    """Прогон уже идёт. В `job` — его ход, чтобы экран мог к нему подключиться."""

    def __init__(self, job):
        super(JobBusy, self).__init__('ИИ уже пишет пачку')
        self.job = job


def _lease():
    return '%s/%s' % (INSTANCE, uuid.uuid4().hex[:8])


def _ref(target):
    return {'cabinet': target['cabinet'], 'advert_id': str(target['advert_id'])}


def _is_unique_violation(exc):
    return getattr(exc, 'pgcode', None) == '23505'


# ─────────────────────────────────────────────────────────────────────────────
# Ход для экрана
# ─────────────────────────────────────────────────────────────────────────────

def snapshot(row, result=None):
    """Ход прогона для экрана. `result` — итог с текстами: нужен один раз, в
    конце, а не на каждом опросе раз в полторы секунды."""
    if not row:
        return None
    running = row['status'] == 'running'
    made, failed = int(row['made_count'] or 0), int(row['failed_count'] or 0)
    out = {
        'id': row['id'],
        'status': row['status'],
        'total': int(row['total'] or 0),
        'done': int(row['position'] or 0) if running else made + failed,
        'made': made,
        'failed': failed,
        'skipped': int(row['skipped_count'] or 0),
        'current': row['current'],
        'note': row['note'],
        'actor_id': row['actor_id'],
        'actor_name': row['actor_name'],
        'elapsed': round(float(row['elapsed'] or 0), 1),
        # Сколько секунд ведущий молчит. Экран по нему говорит «сервер
        # перезапускается — прогон продолжится сам» вместо замершей полосы.
        'beat_age': round(float(row['beat_age'] or 0), 1) if running else None,
        'resumed': int(row['attempts'] or 1) > 1,
        'stop_requested': bool(row['stop_requested']),
        'error': row['error'],
        'code': row['error_code'],
    }
    if result is not None and not running:
        out['result'] = {
            'made': result.get('made') or [],
            'failed': result.get('failed') or [],
            'skipped': result.get('skipped') or [],
            'stopped': row['status'] == 'stopped',
            'briefs': [{'id': int(brief_id), 'title': title}
                       for brief_id, title in (result.get('briefs') or {}).items()],
        }
    return out


def get(db, job_id, full=False):
    try:
        job_id = int(job_id)
    except (TypeError, ValueError):
        return None
    with db._get_cursor() as cursor:
        row = queries.gen_job_summary(cursor, job_id)
        result = (queries.gen_job_result(cursor, job_id)
                  if row and full and row['status'] != 'running' else None)
    return snapshot(row, result)


def active(db):
    """Идущий прогон или None. Экран спрашивает это при открытии раздела."""
    with db._get_cursor() as cursor:
        return snapshot(queries.running_gen_job(cursor))


# ─────────────────────────────────────────────────────────────────────────────
# Ведущий
# ─────────────────────────────────────────────────────────────────────────────

_live_lock = threading.Lock()
_live = {}          # id прогона → ведущий в ЭТОМ процессе (быстрый стоп-кран)


class _Runner(object):
    """Ведёт один прогон: объявление за объявлением, с пульсом рядом."""

    def __init__(self, db, job_id, owner, *, note=None, generate_fn=None, pause=None):
        self.db = db
        self.job_id = int(job_id)
        self.owner = owner
        self.note = note
        self.generate_fn = generate_fn
        self.pause = AI_RETRY_PAUSE if pause is None else float(pause)
        self._stop = threading.Event()
        self._done = threading.Event()
        self._moved_at = time.time()

    def start(self):
        with _live_lock:
            _live[self.job_id] = self
        for target, name in ((self._beat, 'beat'), (self._work, 'work')):
            threading.Thread(target=target, name='olx-ads-ai-%s-%s' % (self.job_id, name),
                             daemon=True).start()

    def request_stop(self):
        self._stop.set()

    def _moved(self):
        self._moved_at = time.time()

    def _call(self, fn, *args, **kwargs):
        with self.db._get_cursor() as cursor:
            return fn(cursor, self.job_id, self.owner, *args, **kwargs)

    # ── пульс ───────────────────────────────────────────────────────────

    def _beat(self):
        while not self._done.wait(HEARTBEAT_SECONDS):
            if time.time() - self._moved_at > STALL_SECONDS:
                log.warning('Объявления OLX: прогон ИИ %s стоит на месте дольше %d с — '
                            'пульс снят, прогон заберёт живой процесс',
                            self.job_id, STALL_SECONDS)
                return
            try:
                stop = self._call(queries.beat_gen_job)
            except Exception:                                # noqa: BLE001
                # База моргнула — не повод бросать прогон: следующий удар через
                # HEARTBEAT_SECONDS, а обход ждёт ORPHAN_SECONDS.
                log.exception('Объявления OLX: пульс прогона ИИ %s не записан', self.job_id)
                continue
            if stop is None:
                log.warning('Объявления OLX: прогон ИИ %s больше не наш — останавливаю '
                            'свой поток', self.job_id)
                self._stop.set()
                return
            if stop:
                self._stop.set()

    # ── работа ──────────────────────────────────────────────────────────

    def _work(self):
        try:
            self._run()
        except Exception:                                    # noqa: BLE001
            # Любой сбой обязан закрыть прогон: иначе он «идёт», пока его не
            # заберёт обход, и раздел всё это время не примет новой пачки.
            log.exception('Объявления OLX: фоновый прогон ИИ %s упал', self.job_id)
            self._finish('error', error=INTERRUPTED_TEXT, error_code='error')
        finally:
            self._done.set()
            with _live_lock:
                if _live.get(self.job_id) is self:
                    _live.pop(self.job_id, None)

    def _finish(self, status, **kwargs):
        try:
            self._call(queries.finish_gen_job, status, **kwargs)
        except Exception:                                    # noqa: BLE001
            # Не закрыли — пульс уже снят (поток выходит), обход заберёт прогон
            # и закроет его с того же места.
            log.exception('Объявления OLX: прогон ИИ %s не закрыт', self.job_id)

    def _run(self):
        work = self._call(queries.gen_job_work)
        if not work:
            return
        targets = list(work['targets'] or [])
        made_any = int(work['made_count'] or 0) > 0
        briefs = {}
        note = self.note
        for index in range(int(work['position'] or 0), len(targets)):
            target = targets[index]
            cabinet = target['cabinet']
            with self.db._get_cursor() as cursor:
                advert = queries.get_advert(cursor, cabinet, target['advert_id'])
                if cabinet not in briefs:
                    # Бриф читается на месте, а не берётся из плана: продолженный
                    # после перезапуска прогон пишет по брифу, действующему сейчас.
                    briefs[cabinet] = queries.active_briefs_for(cursor, [cabinet]).get(cabinet)
            current = ({'cabinet': cabinet, 'advert_id': str(target['advert_id']),
                        'title': advert.get('title'), 'city_name': advert.get('city_name')}
                       if advert else dict(_ref(target), title=None, city_name=None))
            stop = self._call(queries.mark_gen_job_current, current, note)
            note = None
            self._moved()
            if stop is None:
                return
            if stop or self._stop.is_set():
                self._finish('stopped', skipped=[_ref(t) for t in targets[index:]])
                return

            outcome = self._one(advert, target, briefs[cabinet], work)
            if outcome is None:
                return
            kind, entry = outcome
            if kind == 'stop':
                self._finish('stopped', skipped=[_ref(t) for t in targets[index:]])
                return
            if kind == 'unavailable':
                rest = [_ref(t) for t in targets[index + 1:]]
                if not made_any:
                    # Ничего ещё не написано — отказ словами, как синхронный путь.
                    self._finish('error', error=entry, error_code='ai_unavailable',
                                 skipped=[_ref(target)] + rest)
                else:
                    self._finish('done', failed=[dict(_ref(target), error=entry)]
                                 + [dict(item, error=SKIPPED_AFTER_AI_TEXT) for item in rest])
                return

            brief = briefs[cabinet]
            used = {str(brief['id']): brief.get('title')} if brief and kind == 'made' else {}
            stop = self._call(queries.record_gen_job_item, index + 1,
                              made=[entry] if kind == 'made' else (),
                              failed=[entry] if kind == 'failed' else (),
                              briefs=used)
            self._moved()
            if stop is None:
                return
            made_any = made_any or kind == 'made'
        self._finish('done')

    def _one(self, advert, target, brief, work):
        """Одно объявление. ('made'|'failed', запись), ('unavailable', причина),
        ('stop', None) — остановили во время паузы, None — прогон больше не наш."""
        if not advert:
            return 'failed', dict(_ref(target), error='объявления больше нет в списке — '
                                                      'обновите его')
        for attempt in (1, 2):
            try:
                return service.generate_one(
                    self.db, advert, brief, instruction=work['instruction'],
                    actor_id=work['actor_id'], actor_name=work['actor_name'],
                    generate_fn=self.generate_fn)
            except ai.AiUnavailable as exc:
                reason = 'ИИ сейчас недоступен: %s' % (str(exc)[:200],)
                log.warning('Объявления OLX: ИИ недоступен на %s/%s (попытка %d) — %s',
                            advert['cabinet_code'], advert['advert_id'], attempt, exc)
                if attempt == 2 or self.pause <= 0:
                    return 'unavailable', reason
                stop = self._call(queries.set_gen_job_note,
                                  'ИИ не ответил — пробую снова через %d с' % self.pause)
                self._moved()
                if stop is None:
                    return None
                if stop or self._stop.wait(self.pause):
                    return 'stop', None
                self._moved()
        return 'unavailable', 'ИИ сейчас недоступен'


def _launch(db, job_id, owner, **kwargs):
    runner = _Runner(db, job_id, owner, **kwargs)
    runner.start()
    return runner


# ─────────────────────────────────────────────────────────────────────────────
# Ручки
# ─────────────────────────────────────────────────────────────────────────────

def start(db, plan, *, instruction=None, actor_id=None, actor_name=None,
          generate_fn=None, pause=None):
    """Поставить прогон по готовому плану (см. `service.plan_generation`).

    Проверку «уже идёт» делает база: второй INSERT упирается в уникальный
    индекс, и это 409 с ходом идущего прогона, в каком бы процессе он ни шёл.
    """
    targets = [{'cabinet': advert['cabinet_code'], 'advert_id': str(advert['advert_id'])}
               for advert in plan.get('adverts') or []]
    owner = _lease()
    for _attempt in (1, 2):
        try:
            with db._get_cursor() as cursor:
                job_id = queries.create_gen_job(
                    cursor, targets, owner=owner, instruction=instruction,
                    actor_id=actor_id, actor_name=actor_name)
            break
        except Exception as exc:
            if not _is_unique_violation(exc):
                raise
            running = active(db)
            if running:
                raise JobBusy(running)
            # Идущий закончился между INSERT и чтением — место свободно, ещё раз.
    else:
        raise JobBusy(active(db))
    _launch(db, job_id, owner, generate_fn=generate_fn, pause=pause)
    return get(db, job_id)


def request_stop(db, job_id):
    """Остановить после текущего объявления. Написанное остаётся."""
    try:
        job_id = int(job_id)
    except (TypeError, ValueError):
        return None
    with db._get_cursor() as cursor:
        queries.request_gen_job_stop(cursor, job_id)
    with _live_lock:
        runner = _live.get(job_id)
    if runner is not None:
        # Ведущий в этом же процессе — не ждём пульса.
        runner.request_stop()
    # Ведущий мёртв — закрыть сразу, а не ждать таймера обхода.
    sweep(db, force=True)
    return get(db, job_id)


# ─────────────────────────────────────────────────────────────────────────────
# Подхват брошенных
# ─────────────────────────────────────────────────────────────────────────────

_sweep_lock = threading.Lock()
_sweep_state = {'at': 0.0, 'thread': False}


def sweep(db, force=False, generate_fn=None, pause=None):
    """Найти прогоны, ведущий которых умер, и продолжить или закрыть их.

    Зовётся по таймеру (`start_sweeper`) и из ручек опроса: человек, который
    смотрит на экран, увидит продолжение сразу, а не через минуту. Ошибки не
    выпускает — опрос хода не должен падать из-за обхода.
    """
    now = time.time()
    with _sweep_lock:
        if not force and now - _sweep_state['at'] < SWEEP_MIN_GAP:
            return []
        _sweep_state['at'] = now
    resumed = []
    try:
        with db._get_cursor() as cursor:
            orphans = queries.orphan_gen_jobs(cursor, ORPHAN_SECONDS)
        for row in orphans:
            position = int(row['position'] or 0)
            remaining = [_ref(t) for t in (row['targets'] or [])[position:]]
            if row['stop_requested']:
                with db._get_cursor() as cursor:
                    queries.close_orphan_gen_job(cursor, row['id'], ORPHAN_SECONDS,
                                                 'stopped', skipped=remaining)
                continue
            if int(row['attempts'] or 1) >= MAX_ATTEMPTS:
                with db._get_cursor() as cursor:
                    closed = queries.close_orphan_gen_job(
                        cursor, row['id'], ORPHAN_SECONDS, 'error', skipped=remaining,
                        error=RESTARTS_TEXT, error_code='too_many_restarts')
                if closed:
                    log.error('Объявления OLX: прогон ИИ %s закрыт — %d перезапусков подряд',
                              row['id'], row['attempts'])
                continue
            owner = _lease()
            note = 'Сервер перезапускался — продолжаю с %d-го объявления' % (position + 1)
            with db._get_cursor() as cursor:
                attempt = queries.claim_gen_job(cursor, row['id'], owner, ORPHAN_SECONDS,
                                                note=note)
            if not attempt:
                continue
            log.warning('Объявления OLX: подхватили брошенный прогон ИИ %s (попытка %s) '
                        'с %d-го объявления', row['id'], attempt, position + 1)
            _launch(db, row['id'], owner, note=note, generate_fn=generate_fn, pause=pause)
            resumed.append(row['id'])
    except Exception as exc:                                 # noqa: BLE001
        if getattr(exc, 'pgcode', None) == '42P01':
            return resumed                                   # схема ещё не развёрнута
        log.exception('Объявления OLX: обход брошенных прогонов ИИ не удался')
    return resumed


def start_sweeper(db):
    """Таймер обхода — по одному на процесс. Не в процессах роли realtime:
    те держат только SSE (bot_schedule2.ROLE_RUNS_BACKGROUND)."""
    with _sweep_lock:
        if _sweep_state['thread']:
            return False
        _sweep_state['thread'] = True

    def loop():
        time.sleep(SWEEP_FIRST_DELAY)
        while True:
            sweep(db, force=True)
            time.sleep(SWEEP_SECONDS)

    threading.Thread(target=loop, name='olx-ads-ai-sweeper', daemon=True).start()
    return True
