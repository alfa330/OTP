"""HTTP раздела «Провайдер ЭДО».

Blueprint, как у вики, «Обращений» и «Ограничителя Перезвона»: зависимости
приходят аргументами, обратный импорт из bot_schedule2 сюда был бы циклом.

Выгрузка считается В ФОНЕ и отдаётся 202-м с номером задания. Не потому что так
моднее: обход занимает минуты (замеренные пять на восьми тысячах строк), а
waitress рвёт соединение на 120 секундах. Синхронный ответ здесь означал бы
«кнопка всегда падает по таймауту».

ПОЧЕМУ ВЫГРУЗКА УМЕЕТ ПРОДОЛЖАТЬСЯ. Приложение живёт на Render, где каждый пуш в
main перезапускает процесс: 21.08.2026 деплоев было 61, медиана промежутка между
ними 10 минут, каждый четвёртый промежуток короче шести минут. Выгрузка на 15
тысяч строк в такой промежуток не укладывается — и оба прогона того дня (6 962 и
15 738 строк) погибли ровно так, вместе с потоком. Человек видел «Сервер
перезапустился — запустите заново», запускал заново, и следующий деплой убивал
выгрузку снова.

Поэтому здесь три механизма, и каждый закрывает свою причину смерти:

1. **Контрольная точка.** Найденное складывается в базу по ходу обхода
   (fleet_edm_job_rows), а не в конце. Перезапуск теряет секунды работы, а не всё.
2. **Подхват.** У каждого процесса свой идентификатор; запись «идёт» с чужим
   идентификатором и полутора минутами молчания означает мёртвый процесс — такую
   выгрузку новый процесс забирает себе и продолжает сам, без человека.
3. **Пульс.** Пока выгрузка жива, она отмечается в базе каждые 15 секунд.
   Без этого раздел убивал СВОИ ЖЕ живые выгрузки: прогресс писался только на
   границах раундов, а добор карточками 21.08.2026 молчал двадцать минут —
   сторож счёл выгрузку мёртвой, хотя она работала (и работала ещё двадцать минут
   впустую, потому что остановить её было нечем).
"""

import logging
import os
import re
import threading
import time
import uuid
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import access, engine, queries, report
from .client import FleetClient, FleetError, FleetSessionExpired

# Обычная выгрузка из кабинета на 150 тысяч строк весит около 10 МБ. Двадцать
# пять — с запасом, и при этом не даёт положить инстанс одним запросом.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = ('.xlsx', '.xlsm', '.csv')

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Прогресс пишем в базу не чаще раза в две секунды: обход шлёт сотни событий в
# минуту, и каждое означало бы соединение из общего пула.
PROGRESS_MIN_INTERVAL = 2.0

# Кто мы в этом запуске. Процесс на Render живёт от деплоя до деплоя, и это
# единственный способ отличить «выгрузку считает живой поток» от «поток умер
# вместе с процессом»: по времени старта эти два состояния не различимы.
INSTANCE_ID = '{}-{}'.format(os.getpid(), uuid.uuid4().hex[:10])

# Пульс: раз в 15 секунд. Сторож считает мёртвой выгрузку, молчащую полторы
# минуты, — то есть переживает шесть пропущенных ударов.
HEARTBEAT_SECONDS = 15

# Как часто ищем брошенные выгрузки. Минута — это медиана «человек ещё смотрит на
# экран», и при этом не опрос базы вхолостую каждые пять секунд.
SWEEP_SECONDS = 60

# Первый обход — не сразу: при старте приложение ещё поднимает схему и пулы, и
# лезть в базу в этот момент незачем.
SWEEP_FIRST_DELAY = 20

# Контрольную точку пишем пачками: обход находит строки сотнями в секунду, и
# запись на каждую означала бы соединение из пула на каждую сотню строк.
CHECKPOINT_MIN_INTERVAL = 3.0
CHECKPOINT_MAX_ROWS = 2000


def build_fleet_edm_blueprint(*, db, require_api_key, build_cors_preflight_response,
                              resolve_requester, pool, excel_text_warning=None):
    """pool — свой ThreadPoolExecutor раздела. Общий executor_pool сюда не годится:
    в нём четыре места на всё приложение, а выгрузка занимает одно из них на
    минуты подряд."""
    bp = Blueprint('fleet_edm', __name__, url_prefix='/api/fleet_edm')

    # ── доступ ───────────────────────────────────────────────────────────────

    def section_route(rule, methods=('GET',), manage_session=False):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _row, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    with db._get_cursor() as cursor:
                        requester = queries.access_context(cursor, requester_id)
                    if not requester:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    # Гейт здесь, а не в обработчиках: спрятанный пункт меню
                    # доступом не является, раздел открывается и прямым адресом.
                    if not access.can_view_section(requester):
                        return jsonify({"error": "Раздел вам не открыт"}), 403
                    if manage_session and not access.can_manage_session(requester):
                        return jsonify({"error": "Сессию кабинета меняют только админы"}), 403
                    return handler(requester_id, requester, *args, **kwargs)
                except Exception:
                    logging.exception("Провайдер ЭДО: ошибка в %s", rule)
                    return jsonify({"error": "Внутренняя ошибка"}), 500
            return wrapper
        return decorator

    # ── фоновая выгрузка ─────────────────────────────────────────────────────

    # Живые выгрузки этого процесса: номер задания → его пульс со стоп-краном.
    _lives = {}
    _lives_lock = threading.Lock()

    def make_progress(job_id, requests_before=0):
        """requests_before — запросы прошлых попыток. Обход считает свои с нуля
        (клиент новый), а карточка обязана показывать, чего выгрузка стоила
        целиком: после трёх деплоев «180 запросов» выглядело бы издевательством."""
        state = {'at': 0.0}

        def emit(**payload):
            now = time.time()
            # Финальные события (100%) пишем всегда, промежуточные — не чаще
            # раза в две секунды.
            if payload.get('percent') != 100 and now - state['at'] < PROGRESS_MIN_INTERVAL:
                return
            state['at'] = now
            requests = payload.get('requests')
            try:
                with db._get_cursor() as cursor:
                    queries.update_progress(
                        cursor, job_id,
                        percent=payload.get('percent'),
                        note=payload.get('note'),
                        rows_total=payload.get('rows_total'),
                        rows_resolved=payload.get('rows_resolved'),
                        requests_count=(None if requests is None
                                        else requests_before + requests),
                    )
            except Exception:
                logging.exception("Провайдер ЭДО: прогресс задания %s не записан", job_id)

        return emit

    def make_checkpoint(job_id, requests_done=None):
        """Контрольная точка обхода: найденное — в базу, пачками.

        Пачками, а не по строке: раунд по «Бумажному документообороту» находит
        сразу тысячи людей, и запись на каждого означала бы тысячу соединений из
        общего пула. Этапы (`stages`) пишем сразу, как только их прислали — по ним
        следующая попытка решает, какие раунды повторять не нужно, и терять их
        обиднее всего.
        """
        buffer = []
        state = {'at': time.time()}
        lock = threading.Lock()

        def emit(rows=(), stages=None):
            with lock:
                buffer.extend(rows or ())
                now = time.time()
                due = (stages is not None
                       or len(buffer) >= CHECKPOINT_MAX_ROWS
                       or (buffer and now - state['at'] >= CHECKPOINT_MIN_INTERVAL))
                if not due:
                    return
                batch, buffer[:] = list(buffer), []
                state['at'] = now
            if stages is not None and requests_done is not None:
                # Счётчик запросов кладём в те же этапы: следующая попытка начнёт
                # свой счёт с нуля и без этого числа не узнает, сколько уже стоила
                # выгрузка до неё.
                stages = dict(stages, requests_done=requests_done())
            try:
                with db._get_cursor() as cursor:
                    queries.save_checkpoint(cursor, job_id, rows=batch, checkpoint=stages)
            except Exception:
                # Не записанная контрольная точка — это лишний повтор после
                # перезапуска. Упавший из-за неё обход — потерянные минуты работы.
                logging.exception(
                    "Провайдер ЭДО: контрольная точка задания %s не записана", job_id)

        return emit

    class JobLife:
        """Пульс выгрузки и стоп-кран.

        Пульс — отдельным потоком, а не «заодно с прогрессом»: длинные шаги обхода
        (добор карточками — до тысячи запросов подряд) прогресс не пишут, и именно
        в такой тишине сторож 21.08.2026 закрыл живую выгрузку.

        Стоп-кран — обратная сторона того же удара пульса. Если карточка больше не
        наша (её закрыли или подхватил другой процесс), поток обязан остановиться
        сам: два обхода по одному файлу удвоят темп запросов к чужому кабинету, а
        его лимит нам не принадлежит. В тот же день это стоило двух прогонов,
        которые работали одновременно и оба упирались в 429.
        """

        def __init__(self, job_id):
            self.job_id = job_id
            self._stop = threading.Event()
            self._done = threading.Event()
            self._thread = None

        def start(self):
            # Записываемся в реестр живых: кнопка «Остановить» приходит в ТОТ ЖЕ
            # процесс, и дёрнуть стоп-кран напрямую куда быстрее, чем ждать
            # следующего удара пульса (замерено на проде: 15 секунд лишней работы
            # в чужом кабинете). Пульс остаётся страховкой на случай, когда
            # инстансов станет два и поток окажется в другом.
            with _lives_lock:
                _lives[self.job_id] = self
            self._thread = threading.Thread(
                target=self._beat, name='fleet-edm-beat-{}'.format(self.job_id),
                daemon=True)
            self._thread.start()

        def request_stop(self):
            self._stop.set()

        def _beat(self):
            while not self._done.wait(HEARTBEAT_SECONDS):
                try:
                    with db._get_cursor() as cursor:
                        state = queries.touch_job(cursor, self.job_id, INSTANCE_ID)
                except Exception:
                    # База моргнула — это не повод бросать обход: следующий удар
                    # пульса через 15 секунд, а сторож ждёт полторы минуты.
                    logging.exception(
                        "Провайдер ЭДО: пульс задания %s не записан", self.job_id)
                    continue
                if not state.get('mine'):
                    logging.warning(
                        "Провайдер ЭДО: задание %s больше не наше (статус %s) — "
                        "останавливаем обход", self.job_id, state.get('status'))
                    self._stop.set()
                    return

        def should_stop(self):
            return self._stop.is_set()

        def close(self):
            self._done.set()
            with _lives_lock:
                if _lives.get(self.job_id) is self:
                    _lives.pop(self.job_id, None)

    class CardCache:
        """Общие для всех выгрузок подтверждения карточкой.

        Обход не знает про базу — он получает объект с get/put, как получает
        Checkpoint. Отказ базы здесь НЕ должен ронять выгрузку: кеш ускоряет
        работу, а не хранит результат, и потерянная запись означает лишь лишний
        запрос в кабинет на следующем прогоне.
        """

        def get(self, contractor_ids):
            try:
                with db._get_cursor() as cursor:
                    return queries.load_card_providers(cursor, contractor_ids)
            except Exception:
                logging.exception('Провайдер ЭДО: не смог прочитать кеш карточек')
                return {}

        def put(self, rows):
            try:
                with db._get_cursor() as cursor:
                    queries.save_card_providers(cursor, rows)
            except Exception:
                logging.exception('Провайдер ЭДО: не смог записать кеш карточек')

    def run_job(job_id):
        """Тело фоновой выгрузки. Живёт в потоке, поэтому не трогает ни request,
        ни flask.g — у потока их нет (на этом уже обжигались обработчики бота).

        Аргументом идёт только номер задания: и файл, и уже найденное берутся из
        базы. Это не экономия памяти, а требование продолжаемости — поток может
        оказаться вторым (или пятым) для одной и той же выгрузки, и знать он
        обязан не то, что ему передали при загрузке, а то, что есть в базе сейчас.
        """
        life = JobLife(job_id)
        requests_count = 0
        try:
            with db._get_cursor() as cursor:
                source = queries.job_file(cursor, job_id, kind='source')
                state = queries.load_checkpoint(cursor, job_id)
                session = queries.load_session(cursor)
            if not source or not source.get('content'):
                raise engine.InputError(
                    'Исходный файл выгрузки не найден в базе — загрузите его заново.')
            if not session or not session.get('cookies'):
                raise FleetSessionExpired(
                    'Сессия кабинета Fleet не настроена. Нужен вход в кабинет и '
                    'передача сессии в раздел.'
                )

            source_name = source.get('file_name') or ''
            rows, meta = engine.parse_input(source['content'], source_name)
            requests_before = int((state.get('stages') or {}).get('requests_done') or 0)
            emit = make_progress(job_id, requests_before=requests_before)
            ready = len(state.get('results') or {})
            emit(percent=6 if ready else 3, rows_total=meta['rows_total'],
                 rows_resolved=ready or None,
                 note=('Продолжаем после перезапуска: {} строк из {} уже собрано'
                       .format(ready, meta['rows_total']) if ready
                       else 'Файл разобран: {} строк'.format(meta['rows_total'])))

            life.start()
            client = FleetClient(session['cookies'], session.get('user_agent'))
            resolution = engine.resolve(
                rows, client, progress=emit,
                checkpoint=make_checkpoint(
                    job_id,
                    requests_done=lambda: requests_before + client.requests_count),
                resume=state,
                # Секунда, а не пять: обход спрашивает «нас ещё ждут?» у флажка в
                # памяти, а не у базы, — дорогого здесь ничего нет, зато кнопка
                # «Остановить» перестаёт стоить лишних запросов в чужой кабинет.
                should_stop=engine.Stopper(life.should_stop, interval=1.0),
                card_cache=CardCache(),
            )
            requests_count = requests_before + (resolution.get('requests')
                                                or client.requests_count)

            emit(percent=98, note='Собираем файл', requests=requests_count)
            stream = report.build_workbook(
                rows, resolution, source_name=source_name,
                text_warning_patch=excel_text_warning,
            )
            file_bytes_out = stream.getvalue()
            file_name = report.report_filename(source_name)

            results = resolution.get('results') or {}
            resolved = sum(1 for entry in results.values() if entry.get('provider_name'))
            resolution_stats = resolution.get('stats') or {}
            stats = {
                'rows_total': meta['rows_total'],
                'unique_ids': meta['unique_ids'],
                'had_park_column': meta['has_park_column'],
                'providers': resolution.get('provider_counts') or {},
                'check': resolution.get('check') or {},
                'from_card': resolution_stats.get('from_card', 0),
                'not_found': resolution_stats.get('not_found', 0),
                'no_provider_by_kind': resolution_stats.get('no_provider_by_kind', 0),
                'unverified': resolution_stats.get('unverified', 0),
                'park_probe_requests': resolution.get('park_probe_requests') or 0,
                'classify_requests': resolution.get('classify_requests') or 0,
                'skipped_orphans': resolution.get('skipped_orphans') or 0,
                'parks_total': resolution.get('parks_total') or 0,
                'verify': resolution.get('verify') or {},
            }
            with db._get_cursor() as cursor:
                queries.finish_job(
                    cursor, job_id,
                    file_bytes=file_bytes_out, file_name=file_name, stats=stats,
                    # duration_ms не передаём: его считает база от первого старта,
                    # а этот поток мог быть третьим по счёту.
                    rows_total=meta['rows_total'],
                    rows_resolved=resolved,
                    # «Не выяснено» — это НЕ сотрудники парка: у них ответ есть, он
                    # называется «ЭДО не применяется». Иначе раздел красил бы
                    # янтарным совершенно нормальную выгрузку.
                    rows_failed=max(0, meta['rows_total'] - resolved
                                    - stats['no_provider_by_kind']),
                    requests_count=requests_count,
                )
                queries.mark_session_ok(cursor)
                # Файл собран — держать рядом ещё и построчную копию незачем.
                queries.drop_checkpoint(cursor, job_id)
            logging.info("Провайдер ЭДО: задание %s готово, %s строк, %s запросов",
                         job_id, meta['rows_total'], requests_count)

        except engine.Cancelled:
            # Карточку у нас забрали (или закрыли). Ничего не пишем: тот, кто
            # забрал, продолжит с контрольной точки, а наши слова только сбили бы
            # его с толку.
            logging.info("Провайдер ЭДО: задание %s остановлено — его ведёт кто-то другой",
                         job_id)
        except FleetSessionExpired as error:
            _fail(job_id, error, 'session_expired', requests_count)
            try:
                with db._get_cursor() as cursor:
                    queries.mark_session_error(cursor, str(error))
            except Exception:
                logging.exception("Провайдер ЭДО: не удалось отметить сессию протухшей")
        except engine.InputError as error:
            _fail(job_id, error, 'bad_file', requests_count)
        except FleetError as error:
            # Сетевые и прочие ошибки кабинета карточку НЕ закрывают: контрольная
            # точка на месте, и подхват попробует ещё раз. Иначе один моргнувший
            # запрос на десятой минуте стоил бы всей выгрузки.
            logging.warning("Провайдер ЭДО: задание %s прервано кабинетом (%s) — "
                            "оставляем на подхват", job_id, error)
            _pause(job_id, error)
        except Exception as error:  # noqa: BLE001 — карточка обязана закрыться всегда
            logging.exception("Провайдер ЭДО: задание %s упало", job_id)
            _fail(job_id, error, 'internal', requests_count)
        finally:
            life.close()
            with _local_lock:
                _local_jobs.discard(job_id)

    def _fail(job_id, error, code, requests_count):
        try:
            with db._get_cursor() as cursor:
                # Если карточку уже подхватил другой процесс — молчим: закрыть
                # ошибкой чужую живую выгрузку хуже, чем не закрыть свою.
                state = queries.touch_job(cursor, job_id, INSTANCE_ID)
                if not state.get('mine') and state.get('status') == 'running':
                    return
                queries.finish_job(
                    cursor, job_id, error=str(error), error_code=code,
                    requests_count=requests_count,
                )
        except Exception:
            logging.exception("Провайдер ЭДО: не удалось закрыть карточку %s", job_id)

    def _pause(job_id, error):
        """Оставить выгрузку на подхват: карточка остаётся «идёт», в примечании —
        человеческая причина остановки.

        Владельца при этом снимаем. Иначе выгрузка ждала бы СВОЙ ЖЕ процесс,
        который её только что бросил: подхват берёт записи с чужим владельцем, а
        свои — лишь после десяти минут молчания, которых здесь не будет (пульс
        мёртв, но и молчание считается от последней записи).
        """
        try:
            with db._get_cursor() as cursor:
                queries.release_job(
                    cursor, job_id, INSTANCE_ID,
                    note='Кабинет прервал обход ({}). Продолжим сами.'.format(
                        str(error)[:120]),
                )
        except Exception:
            logging.exception("Провайдер ЭДО: не удалось отметить паузу задания %s", job_id)

    # ── подхват брошенных выгрузок ───────────────────────────────────────────

    _local_jobs = set()
    _local_lock = threading.Lock()
    _sweep_at = {'at': 0.0}

    def submit_job(job_id):
        """Поставить выгрузку в свой пул, но не дважды. Пул раздела — на одно
        место, и задание может ждать в очереди дольше, чем длится проверка на
        брошенность; без этой памяти обход брошенных отправил бы туда же второй
        поток по тому же файлу."""
        with _local_lock:
            if job_id in _local_jobs:
                return False
            _local_jobs.add(job_id)
        try:
            pool.submit(run_job, job_id)
        except Exception:
            # Пул закрыт (приложение гасят) — забываем задание, иначе оно навсегда
            # останется «нашим» и подхват его больше не тронет.
            with _local_lock:
                _local_jobs.discard(job_id)
            raise
        return True

    def sweep_jobs(force=False):
        """Найти выгрузки, за которыми больше никого нет, и продолжить их.

        Зовётся и по таймеру, и из ручек раздела: человек, который смотрит на
        экран, узнаёт о продолжении сразу, а не через минуту.
        """
        now = time.time()
        if not force and now - _sweep_at['at'] < 15:
            return []
        _sweep_at['at'] = now
        with db._get_cursor() as cursor:
            outcome = queries.orphan_jobs(cursor, INSTANCE_ID)
        for job_id in outcome.get('exhausted') or ():
            logging.error("Провайдер ЭДО: задание %s закрыто — слишком много "
                          "перезапусков подряд", job_id)
        resumed = []
        for job_id in outcome.get('resume') or ():
            with _local_lock:
                if job_id in _local_jobs:
                    continue
            with db._get_cursor() as cursor:
                attempt = queries.claim_job(
                    cursor, job_id, INSTANCE_ID,
                    note='Продолжаем после перезапуска сервера')
            if not attempt:
                continue
            try:
                started = submit_job(job_id)
            except Exception:
                # Пул уже гасят — задание останется «идущим» без владельца, и его
                # подхватит следующий процесс. Это не повод ронять ручку раздела.
                logging.exception("Провайдер ЭДО: задание %s не удалось поставить "
                                  "в пул", job_id)
                continue
            if started:
                resumed.append(job_id)
                logging.info("Провайдер ЭДО: подхватили брошенное задание %s "
                             "(попытка %s)", job_id, attempt)
        return resumed

    def _sweeper():
        time.sleep(SWEEP_FIRST_DELAY)
        while True:
            try:
                sweep_jobs(force=True)
            except Exception:
                logging.exception("Провайдер ЭДО: обход брошенных выгрузок не удался")
            time.sleep(SWEEP_SECONDS)

    threading.Thread(target=_sweeper, name='fleet-edm-sweeper', daemon=True).start()

    # ── ручки ────────────────────────────────────────────────────────────────

    @section_route('/overview')
    def fleet_edm_overview(requester_id, requester):
        """Всё, что нужно разделу при входе, одним запросом: состояние сессии и
        список выгрузок."""
        # Брошенные выгрузки подхватываем здесь же, а не только по таймеру:
        # человек, который смотрит на экран, увидит «продолжаем» сразу.
        sweep_jobs()
        with db._get_cursor() as cursor:
            session = queries.session_status(cursor)
            jobs = queries.list_jobs(cursor, limit=request.args.get('limit', 50))
            running = queries.has_running_job(cursor)
        return jsonify({
            'status': 'success',
            'session': session,
            'jobs': jobs,
            'running_job_id': running,
            'can_manage_session': access.can_manage_session(requester),
        }), 200

    @section_route('/jobs', methods=('GET', 'POST'))
    def fleet_edm_jobs(requester_id, requester):
        if request.method == 'GET':
            with db._get_cursor() as cursor:
                jobs = queries.list_jobs(cursor, limit=request.args.get('limit', 50))
            return jsonify({'status': 'success', 'jobs': jobs}), 200

        if not access.can_run_job(requester):
            return jsonify({"error": "Недостаточно прав"}), 403

        uploaded = request.files.get('file')
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "Файл не приложен"}), 400
        source_name = _safe_name(uploaded.filename)
        if not source_name.lower().endswith(ALLOWED_EXTENSIONS):
            return jsonify({"error": "Нужен файл Excel (.xlsx) или .csv"}), 400
        file_bytes = uploaded.read()
        if not file_bytes:
            return jsonify({"error": "Файл пустой"}), 400
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            return jsonify({"error": "Файл больше {} МБ".format(
                MAX_UPLOAD_BYTES // (1024 * 1024))}), 413

        return _start_job(source_name, file_bytes, requester_id, requester)

    def _start_job(source_name, file_bytes, requester_id, requester):
        """Общий старт выгрузки: и для загруженного файла, и для «Повторить».

        Одной функцией, потому что проверок здесь четыре и забыть любую — значит
        либо удвоить темп запросов к чужому кабинету, либо запустить обход, которому
        некуда идти.
        """
        # Брошенные выгрузки сначала подхватываем: та, что убита деплоем, должна
        # дойти до конца сама, а не мешать запустить новую и не пропасть.
        sweep_jobs()
        with db._get_cursor() as cursor:
            session = queries.session_status(cursor)
            if not session.get('configured'):
                return jsonify({
                    "error": "Сессия кабинета Fleet не настроена — выгрузка не сможет "
                             "туда зайти.",
                    "code": "SESSION_NOT_CONFIGURED",
                }), 503
            running = queries.has_running_job(cursor)
            if running:
                # Параллельный обход не сломает данные, но удвоит темп запросов к
                # чужому кабинету, а его лимит нам неизвестен.
                return jsonify({
                    "error": "Выгрузка №{} ещё идёт. Дождитесь её окончания.".format(running),
                    "code": "ALREADY_RUNNING",
                    "running_job_id": running,
                }), 409
            queries.cleanup(cursor)
            job_id = queries.create_job(
                cursor, user_id=requester_id, user_name=(requester or {}).get('name'),
                source_name=source_name, source_bytes=file_bytes,
                owner_instance=INSTANCE_ID,
            )

        try:
            submit_job(job_id)
        except Exception:
            # Карточка и исходник уже в базе, значит выгрузка не потеряна: её
            # подхватит следующий процесс. Отвечаем как обычно — человеку нечего
            # делать с нашей внутренней очередью.
            logging.exception("Провайдер ЭДО: задание %s принято, но в пул не "
                              "встало — уйдёт на подхват", job_id)
        return jsonify({'status': 'success', 'job_id': job_id, 'job_status': 'running'}), 202

    @section_route('/jobs/<int:job_id>', methods=('GET', 'DELETE'))
    def fleet_edm_job(requester_id, requester, job_id):
        if request.method == 'DELETE':
            if not access.can_run_job(requester):
                return jsonify({"error": "Недостаточно прав"}), 403
            with db._get_cursor() as cursor:
                job = queries.get_job(cursor, job_id)
                if not job:
                    return jsonify({"error": "Выгрузка не найдена"}), 404
                if job.get('status') == 'running':
                    return jsonify({
                        "error": "Выгрузка ещё идёт — сначала остановите её.",
                        "code": "STILL_RUNNING",
                    }), 409
                queries.delete_job(cursor, job_id)
            logging.info("Провайдер ЭДО: выгрузка %s удалена пользователем %s",
                         job_id, requester_id)
            return jsonify({'status': 'success', 'deleted_job_id': job_id}), 200

        with db._get_cursor() as cursor:
            job = queries.get_job(cursor, job_id)
        if not job:
            return jsonify({"error": "Выгрузка не найдена"}), 404
        return jsonify({'status': 'success', 'job': job}), 200

    @section_route('/jobs/<int:job_id>/stop', methods=('POST',))
    def fleet_edm_job_stop(requester_id, requester, job_id):
        """Остановить идущую выгрузку.

        Отвечаем сразу, не дожидаясь потока: он в этот момент может стоять в
        паузе, которую попросил кабинет. Карточку закрывает сам запрос, а поток
        увидит это пульсом и остановится не позже чем через 15 секунд.
        """
        if not access.can_run_job(requester):
            return jsonify({"error": "Недостаточно прав"}), 403
        with db._get_cursor() as cursor:
            job = queries.get_job(cursor, job_id)
            if not job:
                return jsonify({"error": "Выгрузка не найдена"}), 404
            if job.get('status') != 'running':
                return jsonify({"error": "Эта выгрузка уже не идёт",
                                "code": "NOT_RUNNING"}), 409
            queries.stop_job(cursor, job_id, (requester or {}).get('name'))
            job = queries.get_job(cursor, job_id)
        # Поток этой выгрузки живёт в нашем же процессе — дёргаем стоп-кран сразу,
        # не дожидаясь, пока он сам заметит закрытую карточку пульсом.
        with _lives_lock:
            life = _lives.get(job_id)
        if life:
            life.request_stop()
        logging.info("Провайдер ЭДО: выгрузку %s остановил пользователь %s%s",
                     job_id, requester_id, '' if life else ' (поток в другом процессе)')
        return jsonify({'status': 'success', 'job': job}), 200

    @section_route('/jobs/<int:job_id>/repeat', methods=('POST',))
    def fleet_edm_job_repeat(requester_id, requester, job_id):
        """Собрать заново по тому же исходнику.

        Исходник раздел хранит у себя ровно для этого — просить у человека тот же
        файл второй раз незачем. Пригодилось в тот же день, когда обнаружился
        дефект с потерянной диспетчерской: файл заказчицы пришлось пересобирать, и
        делать это руками через API было стыдно.
        """
        if not access.can_run_job(requester):
            return jsonify({"error": "Недостаточно прав"}), 403
        with db._get_cursor() as cursor:
            job = queries.get_job(cursor, job_id)
            if not job:
                return jsonify({"error": "Выгрузка не найдена"}), 404
            source = queries.job_file(cursor, job_id, kind='source')
        if not source or not source.get('content'):
            return jsonify({
                "error": "Исходный файл этой выгрузки уже удалён по сроку хранения — "
                         "загрузите его заново.",
                "code": "SOURCE_GONE",
            }), 410
        name = source.get('file_name') or job.get('source_name') or 'file.xlsx'
        return _start_job(_safe_name(name), source['content'], requester_id, requester)

    @section_route('/jobs/<int:job_id>/file')
    def fleet_edm_job_file(requester_id, requester, job_id):
        with db._get_cursor() as cursor:
            payload = queries.job_file(cursor, job_id, kind=request.args.get('kind', 'result'))
        if not payload:
            return jsonify({"error": "Файл не найден"}), 404
        name = payload['file_name'] or 'Провайдер ЭДО {}.xlsx'.format(job_id)
        # Исходник бывает и csv — отдавать его как xlsx значит получить файл,
        # который Excel откроет кракозябрами.
        mimetype = 'text/csv' if name.lower().endswith('.csv') else XLSX_MIME
        return send_file(
            BytesIO(payload['content']),
            mimetype=mimetype,
            as_attachment=True,
            download_name=name,
        )

    @section_route('/session', methods=('GET', 'POST'), manage_session=True)
    def fleet_edm_session(requester_id, requester):
        if request.method == 'GET':
            with db._get_cursor() as cursor:
                return jsonify({'status': 'success', 'session': queries.session_status(cursor)}), 200

        payload = request.get_json(silent=True) or {}
        cookies = payload.get('cookies')
        if not cookies:
            return jsonify({"error": "Не переданы куки сессии"}), 400
        user_agent = str(payload.get('user_agent') or '').strip()

        # Принимаем только то, что реально работает: сразу ходим в кабинет.
        # Молча сохранённая нерабочая сессия — это выгрузка, падающая через час
        # ожидания вместо честного отказа сейчас.
        try:
            client = FleetClient(cookies, user_agent)
            checked = client.check()
        except FleetSessionExpired as error:
            return jsonify({"error": str(error), "code": "SESSION_INVALID"}), 400
        except FleetError as error:
            return jsonify({"error": "Кабинет не ответил: {}".format(error)}), 502

        with db._get_cursor() as cursor:
            queries.save_session(
                cursor, cookies=cookies, user_agent=user_agent,
                account=checked.get('account'), parks_count=checked.get('parks_count'),
                updated_by=requester_id,
            )
            session = queries.session_status(cursor)
        logging.info("Провайдер ЭДО: сессия кабинета обновлена (%s парков)",
                     checked.get('parks_count'))
        return jsonify({'status': 'success', 'session': session}), 200

    @section_route('/session/check', methods=('POST',))
    def fleet_edm_session_check(requester_id, requester):
        """Жива ли сессия прямо сейчас. Отдельной кнопкой: узнавать об этом из
        упавшей через десять минут выгрузки — плохой способ."""
        with db._get_cursor() as cursor:
            session = queries.load_session(cursor)
        if not session or not session.get('cookies'):
            return jsonify({'status': 'success', 'alive': False,
                            'error': 'Сессия не настроена'}), 200
        try:
            client = FleetClient(session['cookies'], session.get('user_agent'))
            checked = client.check()
        except FleetError as error:
            with db._get_cursor() as cursor:
                queries.mark_session_error(cursor, str(error))
                status = queries.session_status(cursor)
            return jsonify({'status': 'success', 'alive': False,
                            'error': str(error), 'session': status}), 200
        with db._get_cursor() as cursor:
            queries.mark_session_ok(cursor)
            status = queries.session_status(cursor)
        return jsonify({'status': 'success', 'alive': True,
                        'account': checked.get('account'),
                        'parks_count': checked.get('parks_count'),
                        'session': status}), 200

    return bp


def _safe_name(filename):
    """Кириллицу оставляем: secure_filename её съедает, и «Нет провайдера.xlsx»
    превращается в «.xlsx» — в списке выгрузок такое имя бесполезно."""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', str(filename or '')).strip()
    name = name.replace('..', '.')
    return name[:200] or 'file.xlsx'
