"""Выгрузка статистики одного тренажёра в xlsx.

Четыре листа, и порядок у них смысловой — тот же, что в выгрузке «Провайдер
ЭДО» (fleet_edm/report.py), с которой этот модуль намеренно похож:

  «Контекст»    — что за файл, за какой период, кем и когда собран. Первым,
                  потому что цифра без даты и без оговорок живёт своей жизнью:
                  через месяц по одному числу уже не сказать, за какие дни оно.
  «Прохождения» — по строке на попытку. Это исходник, из которого посчитаны
                  остальные листы, и именно его пересчитывают вручную, когда
                  сводке не верят.
  «По людям»    — кто и сколько раз. Тот самый вопрос, ради которого выгрузку
                  и просят.
  «По статьям»  — откуда заходили. Отвечает «в какой статье сколько раз».

Длительность лежит ЧИСЛОМ секунд, а не строкой «3 мин 20 с»: по строке нельзя
ни отсортировать, ни посчитать среднее, а сводную таблицу по такой выгрузке
собирают в первый же день. Читаемая подпись стоит рядом отдельной колонкой.

Дат в ячейках две формы по той же причине: настоящая дата (её понимает
сортировка и фильтр Excel) плюс формат, в котором её читают глазами.
"""

from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from .report_kit import (
    DATE_FMT, TITLE_FONT, fill_table, note, now_almaty, parse, period_words,
)
from .report_kit import report_filename as report_kit_filename

STATUS_TITLES = {
    'finished': 'Прошёл',
    'started': 'Не завершил',
    'abandoned': 'Бросил',
}

SOURCE_TITLES = {
    'article': 'Из статьи',
    'catalog': 'Из вкладки «Тренажёры»',
    # Вкладка «Тренажёры» в iCORE Phone — та же подпись, что на экране
    # статистики (WikiTrainerStats.jsx): без неё в файле стоял сырой код.
    'phone': 'Из iCORE Phone',
}

ROLE_TITLES = {
    'super_admin': 'Супер-админ',
    'admin': 'Администратор',
    'sv': 'Супервайзер',
    'supervisor': 'Супервайзер',
    'trainer': 'Тренер',
    'operator': 'Оператор',
    'trainee': 'Стажёр',
    # Должности отделов без линии. Вика открыта им тумблером отдела, значит и в
    # выгрузке прохождений они появляются — без подписи в колонке «Должность»
    # стоял бы сырой код роли.
    'hr_manager': 'HR-менеджер',
    'accounting_manager': 'Менеджер бухгалтерии',
    'marketing_manager': 'Менеджер маркетинга',
}

RUN_COLUMNS = (
    ('started_at', 'Начал', 18),
    ('name', 'ФИО', 30),
    ('department', 'Отдел', 20),
    ('group', 'Группа', 20),
    ('role', 'Должность', 16),
    ('status', 'Результат', 15),
    ('progress', 'Дошёл до шага', 15),
    ('errors', 'Промахов', 11),
    ('hints', 'Подсказок', 11),
    ('restarts', 'Начинал заново', 15),
    ('seconds', 'Время, с', 11),
    ('human_time', 'Время', 11),
    ('source', 'Откуда запустил', 22),
    ('article_title', 'Статья', 40),
    ('finished_at', 'Закончил', 18),
)

PERSON_COLUMNS = (
    ('name', 'ФИО', 30),
    ('department', 'Отдел', 20),
    ('group', 'Группа', 20),
    ('role', 'Должность', 16),
    ('runs', 'Попыток', 10),
    ('finished', 'Прошёл', 10),
    ('errors', 'Промахов всего', 15),
    ('hints', 'Подсказок всего', 16),
    ('best_seconds', 'Лучшее время, с', 16),
    ('first_at', 'Первая попытка', 18),
    ('last_at', 'Последняя попытка', 18),
)

# Итог попытки — колонки только для тренажёров, где он есть: у аттестации
# балл «3 из 5» и что было не так, у старых записей тренажёра CRM — «верно /
# неверно». У прогулок по инструкции итога нет, и пустые колонки в их
# выгрузке были бы шумом, поэтому они встают, только когда в выгрузке есть
# хоть одна попытка с вердиктом (разбором). Разбор — последней колонкой: это
# длинный текст, в середине таблицы он развалил бы чтение строк.
VERDICT_COLUMN = ('verdict', 'Итог', 12)
MISSES_COLUMN = ('misses', 'Ошибки аттестации', 80)

ARTICLE_COLUMNS = (
    ('title', 'Статья', 46),
    ('runs', 'Запусков', 11),
    ('finished', 'Прошли', 10),
    ('people', 'Человек', 10),
    ('last_at', 'Последний запуск', 18),
)


def _human_time(seconds):
    if seconds is None:
        return ''
    minutes, rest = divmod(int(seconds), 60)
    return '%d:%02d' % (minutes, rest)


def _seconds(duration_ms):
    return None if duration_ms is None else round(duration_ms / 1000)


def _count(value):
    # bool — подкласс int в питоне: True из браузера не должен стать «1 из 1».
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def result_verdict(result):
    """Вердикт итога: «3 из 5» у аттестации, «верно»/«неверно» у итога с
    вердиктом, иначе пусто — у курса и свободной среды правильного ответа нет,
    судит наставник. Тот же разбор, что на экране (trainerResult.js)."""
    if not isinstance(result, dict):
        return ''
    exam = result.get('exam')
    if isinstance(exam, dict):
        total = _count(exam.get('total'))
        if total:
            return '%d из %d' % (min(_count(exam.get('correct')), total), total)
    correct = result.get('correct')
    if isinstance(correct, bool):
        return 'верно' if correct else 'неверно'
    return ''


def result_misses(result):
    """Что было не так на аттестации: по строке на неверно решённую
    ситуацию. Не дошедшие до ситуации (бросил на середине) не перечисляются —
    это и так видно по «Результату» и баллу."""
    exam = result.get('exam') if isinstance(result, dict) else None
    items = exam.get('items') if isinstance(exam, dict) else None
    if not isinstance(items, list):
        return ''
    lines = []
    for item in items:
        if not isinstance(item, dict) or item.get('ok') or item.get('answered') is False:
            continue
        title = str(item.get('title') or item.get('key') or 'Ситуация').strip()
        problems = item.get('problems')
        text = ' '.join(str(p).strip() for p in problems if p) if isinstance(problems, list) else ''
        lines.append('%s — %s' % (title, text) if text else title)
    return '\n'.join(lines)


def _run_columns(runs):
    """Колонки листа «Прохождения»: «Итог» — следом за «Результатом», разбор
    — последней, и только если им есть что показать."""
    with_verdict = any(result_verdict(run.get('result')) for run in runs)
    with_misses = any(result_misses(run.get('result')) for run in runs)
    columns = []
    for column in RUN_COLUMNS:
        columns.append(column)
        if column[0] == 'status' and with_verdict:
            columns.append(VERDICT_COLUMN)
    if with_misses:
        columns.append(MISSES_COLUMN)
    return tuple(columns)


def build_workbook(*, trainer, totals, runs, people, articles,
                   since=None, until=None, requested_by='', generated_at=None):
    """Возвращает BytesIO с готовой книгой.

    trainer — {'key', 'title', 'app'}: сервер сценариев не знает, название
    приходит из фронта вместе с запросом. Ключ пишем рядом с названием, потому
    что по ключу выгрузку и опознают в переписке.
    """
    generated_at = generated_at or now_almaty()

    run_columns = _run_columns(runs)
    workbook = Workbook()
    _fill_context(workbook.active, trainer, totals, since, until,
                  requested_by, generated_at, exam=VERDICT_COLUMN in run_columns)
    runs_sheet = fill_table(workbook.create_sheet('Прохождения'), run_columns, runs,
                            _run_values)
    if MISSES_COLUMN in run_columns:
        # Разбор — несколько строк в ячейке: без переноса Excel покажет одну.
        column = run_columns.index(MISSES_COLUMN) + 1
        for row in range(2, len(runs) + 2):
            runs_sheet.cell(row=row, column=column).alignment = Alignment(
                wrap_text=True, vertical='top')
    fill_table(workbook.create_sheet('По людям'), PERSON_COLUMNS, people, _person_values)
    fill_table(workbook.create_sheet('По статьям'), ARTICLE_COLUMNS, articles,
               _article_values)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def report_filename(trainer_key, generated_at=None):
    """Имя файла узнают по ключу сценария, поэтому он в имени и стоит."""
    return report_kit_filename('trainer_%s' % (trainer_key or 'trainer'), generated_at)


# ── Листы ────────────────────────────────────────────────────────────────────

def _fill_context(sheet, trainer, totals, since, until, requested_by, generated_at,
                  exam=False):
    sheet.title = 'Контекст'
    sheet.column_dimensions['A'].width = 34
    sheet.column_dimensions['B'].width = 52

    sheet['A1'] = 'Статистика тренажёра'
    sheet['A1'].font = TITLE_FONT

    rows = [
        ('Тренажёр', trainer.get('title') or trainer.get('key')),
        ('Ключ сценария', trainer.get('key')),
        ('Приложение', trainer.get('app') or ''),
        ('Период', period_words(since, until)),
        ('Выгрузку собрал', requested_by or ''),
        ('Дата выгрузки', generated_at),
        ('', ''),
        ('Запусков', totals.get('runs', 0)),
        ('Из них дошли до конца', totals.get('finished', 0)),
        ('Человек садилось', totals.get('people', 0)),
        ('Человек прошло', totals.get('people_done', 0)),
        ('Медианное время прохождения, с', _seconds(totals.get('median_ms'))),
        ('Промахов на прохождение (среднее)', totals.get('avg_errors')),
        ('Подсказок на прохождение (среднее)', totals.get('avg_hints')),
        ('Начинали заново', totals.get('restarts', 0)),
        ('Первый запуск', parse(totals.get('first_at'))),
        ('Последний запуск', parse(totals.get('last_at'))),
    ]
    for index, (label, value) in enumerate(rows, start=3):
        sheet.cell(row=index, column=1, value=label).font = Font(bold=bool(label))
        cell = sheet.cell(row=index, column=2, value=value)
        if isinstance(value, datetime):
            cell.number_format = DATE_FMT

    sheet.cell(row=sheet.max_row + 2, column=1)
    note(sheet, '«Человек садилось» и «человек прошло» считаются по разным людям, а не '
         'по попыткам: один и тот же оператор мог пройти тренажёр трижды.')
    note(sheet, 'Медиана, а не среднее: одна попытка, оставленная открытой на двадцать '
         'минут, сдвигает среднее так, что цифра перестаёт что-либо значить.')
    note(sheet, 'Время и промахи считаются только по завершённым попыткам — у брошенной '
         'ни то, ни другое не окончательно.')
    note(sheet, 'Строка со статусом «Не завершил» — попытка, которую человек начал и '
         'закрыл, не дойдя до конца. Это не ошибка учёта, а рабочая цифра: по ней '
         'видно, на каком шаге инструкция теряет людей.')
    if exam:
        note(sheet, '«Итог» — сколько ситуаций аттестации решено верно; что именно было '
             'не так, — в колонке «Ошибки аттестации». На аттестации подсказок нет и на '
             'каждую ситуацию одна попытка, поэтому промах там — это и есть ошибка.')


def _run_values(run):
    seconds = _seconds(run.get('duration_ms'))
    return {
        'started_at': parse(run.get('started_at')),
        'name': run.get('name'),
        'department': run.get('department') or '',
        'group': run.get('group') or '',
        'role': ROLE_TITLES.get(run.get('role'), run.get('role') or ''),
        'status': STATUS_TITLES.get(run.get('status'), run.get('status')),
        'verdict': result_verdict(run.get('result')),
        'misses': result_misses(run.get('result')),
        'progress': '%d из %d' % (run.get('stages_done') or 0,
                                  run.get('stages_total') or 0),
        'errors': run.get('errors') or 0,
        'hints': run.get('hints') or 0,
        'restarts': run.get('restarts') or 0,
        'seconds': seconds,
        'human_time': _human_time(seconds),
        'source': SOURCE_TITLES.get(run.get('source'), run.get('source')),
        'article_title': run.get('article_title') or '',
        'finished_at': parse(run.get('finished_at')),
    }


def _person_values(person):
    return {
        'name': person.get('name'),
        'department': person.get('department') or '',
        'group': person.get('group') or '',
        'role': ROLE_TITLES.get(person.get('role'), person.get('role') or ''),
        'runs': person.get('runs') or 0,
        'finished': person.get('finished') or 0,
        'errors': person.get('errors') or 0,
        'hints': person.get('hints') or 0,
        'best_seconds': _seconds(person.get('best_ms')),
        'first_at': parse(person.get('first_at')),
        'last_at': parse(person.get('last_at')),
    }


def _article_values(article):
    return {
        'title': article.get('title'),
        'runs': article.get('runs') or 0,
        'finished': article.get('finished') or 0,
        'people': article.get('people') or 0,
        'last_at': parse(article.get('last_at')),
    }
