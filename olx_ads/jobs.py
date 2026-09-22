# -*- coding: utf-8 -*-
"""Фоновый прогон ИИ по пачке объявлений: ход, остановка, один прогон за раз.

Зачем фон, а не длинный запрос. Пачка на кабинет — 30–36 объявлений по 2–6 с
на каждое, то есть 2–4 минуты. Render такой ответ держит (до 100 минут), а
человек — нет: 22.09.2026 маркетолог через 92 секунды без признаков жизни
обновил страницу и нажал снова, и две пачки по 32 объявления пошли параллельно —
двойной расход и двойная нагрузка на цепочку ИИ ради того же результата.
Поэтому:
  * запрос лишь СТАВИТ прогон и сразу отвечает его номером, экран опрашивает ход;
  * прогон в разделе один: пока он идёт, вторая попытка получает 409 и тот же
    прогон — обновлённая страница и второй человек подключаются к идущему, а не
    запускают новый;
  * остановить можно между объявлениями: уже написанные черновики остаются.

Реестр — в памяти процесса, не в базе. Сервис на Render в одном экземпляре, а
прогон живёт минуты: таблица под это была бы схемой ради состояния потока.
Плата: перезапуск сервиса теряет прогон — экран получает 404 и говорит об этом,
а черновики, написанные до перезапуска, целы: они пишутся по одному по ходу.
"""

import logging
import threading
import time
import uuid

from . import service

log = logging.getLogger(__name__)

# Законченный прогон помнится час: чтобы экран, опросивший его с опозданием,
# получил итог, а не 404. Дольше незачем — итог уже в черновиках и истории.
KEEP_FINISHED_SECONDS = 3600


class JobBusy(RuntimeError):
    """Прогон уже идёт. В `job` — он самый, чтобы экран мог к нему подключиться."""

    def __init__(self, job):
        super(JobBusy, self).__init__('ИИ уже пишет пачку')
        self.job = job


class GenerationJob(object):
    """Состояние одного прогона. Меняется из рабочего потока, читается из HTTP."""

    def __init__(self, total, actor_id=None, actor_name=None, instruction=None):
        self.id = uuid.uuid4().hex[:12]
        self.total = total
        self.actor_id = actor_id
        self.actor_name = actor_name
        self.instruction = instruction
        self.status = 'running'          # running | done | stopped | error
        self.done = 0
        self.made = 0
        self.failed = 0
        self.current = None
        self.result = None
        self.error = None
        self.error_code = None
        self.started_at = time.time()
        self.finished_at = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ── колбэки для service.run_generation ──────────────────────────────

    def on_progress(self, state):
        advert = state.get('current')
        with self._lock:
            self.done = int(state.get('done') or 0)
            self.total = int(state.get('total') or self.total)
            self.made = int(state.get('made') or 0)
            self.failed = int(state.get('failed') or 0)
            self.current = ({'cabinet': advert.get('cabinet_code'),
                             'advert_id': advert.get('advert_id'),
                             'title': advert.get('title'),
                             'city_name': advert.get('city_name')}
                            if advert else None)

    def should_stop(self):
        return self._stop.is_set()

    def request_stop(self):
        self._stop.set()

    def finish(self, result=None, error=None, error_code=None):
        with self._lock:
            self.result = result
            self.error = error
            self.error_code = error_code
            self.current = None
            self.finished_at = time.time()
            if result is not None:
                self.made = len(result.get('made') or [])
                self.failed = len(result.get('failed') or [])
                self.done = self.made + self.failed
            if error:
                self.status = 'error'
            elif result is not None and result.get('stopped'):
                self.status = 'stopped'
            else:
                self.status = 'done'

    def snapshot(self, full=False):
        """Ход прогона для экрана. `full` добавляет итог с текстами — он нужен
        один раз, в конце, а не на каждом опросе раз в полторы секунды."""
        with self._lock:
            result = self.result or {}
            now = self.finished_at or time.time()
            out = {
                'id': self.id,
                'status': self.status,
                'total': self.total,
                'done': self.done,
                'made': self.made,
                'failed': self.failed,
                'skipped': len(result.get('skipped') or []),
                'current': dict(self.current) if self.current else None,
                'actor_id': self.actor_id,
                'actor_name': self.actor_name,
                'elapsed': round(now - self.started_at, 1),
                'stop_requested': self._stop.is_set(),
                'error': self.error,
                'code': self.error_code,
            }
            if full and self.result is not None:
                out['result'] = self.result
            return out


_lock = threading.Lock()
_jobs = {}


def _prune_locked(now):
    for job_id, job in list(_jobs.items()):
        if job.finished_at and now - job.finished_at > KEEP_FINISHED_SECONDS:
            del _jobs[job_id]


def _running_locked():
    for job in _jobs.values():
        if job.status == 'running':
            return job
    return None


def active():
    """Идущий прогон или None. Экран спрашивает это при открытии раздела."""
    with _lock:
        return _running_locked()


def get(job_id):
    with _lock:
        return _jobs.get(job_id)


def start(db, plan, *, instruction=None, actor_id=None, actor_name=None,
          runner=None):
    """Поставить прогон по готовому плану (см. `service.plan_generation`).

    `runner` — параметр ради тестов, по умолчанию `service.run_generation`.
    Проверка «уже идёт» и регистрация — под одним замком, чтобы два одновременных
    нажатия не дали двух прогонов.
    """
    runner = runner or service.run_generation
    with _lock:
        _prune_locked(time.time())
        running = _running_locked()
        if running is not None:
            raise JobBusy(running)
        job = GenerationJob(total=len(plan.get('adverts') or []),
                            actor_id=actor_id, actor_name=actor_name,
                            instruction=instruction)
        _jobs[job.id] = job

    def work():
        try:
            result = runner(db, plan, instruction=instruction,
                            actor_id=actor_id, actor_name=actor_name,
                            progress=job.on_progress, should_stop=job.should_stop)
            job.finish(result=result)
        except service.AdsError as exc:
            job.finish(error=str(exc), error_code=exc.code)
        except Exception:                                    # noqa: BLE001
            # Любой сбой обязан закрыть прогон: иначе он вечно «идёт», и раздел
            # до перезапуска сервиса не примет ни одной новой пачки.
            log.exception('Объявления OLX: фоновый прогон ИИ %s упал', job.id)
            job.finish(error='Прогон прервался внутренней ошибкой — обновите '
                             'список: написанное до сбоя сохранено',
                       error_code='error')

    thread = threading.Thread(target=work, name='olx-ads-ai-%s' % job.id,
                              daemon=True)
    thread.start()
    return job
