# -*- coding: utf-8 -*-
"""Документы ТЗ HR «iCore 4»: пакет приема (Word по меткам) и АВР (Excel по форме Р-1).

Шаблоны по умолчанию лежат рядом (templates/) и собраны из файлов HR: прочерки
заменены метками {{…}}, а реквизиты компании — тоже метками, поэтому ни ИИН, ни
счёт в репозиторий не попадают. HR может загрузить свой шаблон в разделе — он
хранится в базе и берётся вместо стандартного.
"""

import re
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from openpyxl import load_workbook
from openpyxl.styles import Alignment

from . import rules

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'

DOC_KINDS = (
    ('contract', 'Договор ГПХ'),
    ('liability', 'Договор о материальной ответственности'),
    ('questionnaire', 'Анкета'),
    ('nda', 'Соглашение о конфиденциальности'),
)
DOC_TITLES = dict(DOC_KINDS)
# Шаблонов трудового договора HR не прислал: для ТД собирается только анкета
# (договор ГПХ и приложения к нему по ТД не подходят).
KINDS_BY_EMPLOYMENT = {
    'gph': ('contract', 'liability', 'questionnaire', 'nda'),
    'smz': ('contract', 'liability', 'questionnaire', 'nda'),
    'of': ('questionnaire',),
    None: ('questionnaire',),
}

MARKER = re.compile(r'\{\{([^{}]{1,40})\}\}')
BLANK = '________________'
MAX_TEMPLATE_BYTES = 3 * 1024 * 1024

# Все метки, которые понимает заполнение, — для подсказки HR и проверки шаблона.
KNOWN_MARKERS = (
    'НОМЕР_ДОГОВОРА', 'ДАТА_ДОГОВОРА', 'ГОД', 'НОМЕР_МО', 'ГОРОД',
    'ЗАКАЗЧИК', 'ЗАКАЗЧИК_ИИН_БИН', 'ЗАКАЗЧИК_В_ЛИЦЕ', 'ЗАКАЗЧИК_АДРЕС', 'ЗАКАЗЧИК_БАНК', 'ЗАКАЗЧИК_КБЕ',
    'ЗАКАЗЧИК_БИК', 'ЗАКАЗЧИК_СЧЁТ', 'ЗАКАЗЧИК_ВИД_НОМЕРА', 'ЗАКАЗЧИК_БИН', 'ЗАКАЗЧИК_ПОДПИСАНТ',
    'ЗАКАЗЧИК_ДОЛЖНОСТЬ',
    'ФИО', 'ФИО_КРАТКО', 'ФАМИЛИЯ', 'ИМЯ', 'ОТЧЕСТВО', 'ИИН', 'ДОКУМЕНТ', 'ДОКУМЕНТ_ЗАГ', 'ДОКУМЕНТ_НОМЕР',
    'ДОКУМЕНТ_КЕМ', 'ДОКУМЕНТ_КОГДА', 'ВЫДАНО', 'ВЫДАНО_ЗАГ', 'АДРЕС', 'АДРЕС_ФАКТ', 'ПРОЖИВАЮЩИЙ', 'ИМЕНУЕМЫЙ',
    'ТЕЛЕФОН', 'EMAIL', 'TELEGRAM', 'ДАТА_РОЖДЕНИЯ', 'МЕСТО_РОЖДЕНИЯ', 'ГРАЖДАНСТВО', 'ДОЛЖНОСТЬ', 'СУММА',
    'СУММА_ПРОПИСЬЮ', 'УЧЁБА_ГОД', 'УЧЁБА_МЕСТО', 'УЧЁБА_СПЕЦИАЛЬНОСТЬ',
    'КОНТАКТ1_КТО', 'КОНТАКТ1_ФИО', 'КОНТАКТ1_ТЕЛЕФОН', 'КОНТАКТ2_КТО', 'КОНТАКТ2_ФИО', 'КОНТАКТ2_ТЕЛЕФОН',
)


class DocumentError(Exception):
    def __init__(self, message, status=400, code='HR_DOCUMENT_ERROR'):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _text(value):
    return ' '.join(str(value or '').split())


def default_template(kind):
    path = TEMPLATES_DIR / f'{kind}.docx'
    return path.read_bytes()


def document_values(person, company, contract, today):
    """Значения меток для одного человека. Нет данных — прочерк, чтобы вписать от руки."""
    company = company or {}
    contract = contract or {}
    signed_on = contract.get('signed_on')
    last, first, middle = rules.name_parts(person.get('name'))
    gender = person.get('gender')
    doc_type = person.get('doc_type') or 'id_card'
    is_passport = doc_type == 'passport'
    amount = contract.get('amount')
    kind = company.get('kind')

    def value(raw, blank=BLANK):
        text = _text(raw)
        return text if text else blank

    issued = person.get('doc_issued_on')
    values = {
        'НОМЕР_ДОГОВОРА': value(contract.get('number'), '______'),
        'ДАТА_ДОГОВОРА': rules.date_long(signed_on),
        'ГОД': str((signed_on or today).year),
        'НОМЕР_МО': value(contract.get('liability_number') or contract.get('number'), '______'),
        'ГОРОД': value(company.get('city'), 'Алматы'),
        'ЗАКАЗЧИК': value(rules.company_display_name(company)),
        'ЗАКАЗЧИК_ИИН_БИН': f"{rules.company_id_label(kind)} {value(company.get('bin'), '____________')}",
        'ЗАКАЗЧИК_В_ЛИЦЕ': value(company.get('in_person'), 'в лице ________________, действующего на основании ________'),
        'ЗАКАЗЧИК_АДРЕС': value(company.get('legal_address')),
        'ЗАКАЗЧИК_БАНК': value(company.get('bank_name')),
        'ЗАКАЗЧИК_КБЕ': value(company.get('kbe'), '___'),
        'ЗАКАЗЧИК_БИК': value(company.get('bik')),
        'ЗАКАЗЧИК_СЧЁТ': value(company.get('iik')),
        'ЗАКАЗЧИК_ВИД_НОМЕРА': rules.company_id_label(kind),
        'ЗАКАЗЧИК_БИН': value(company.get('bin'), '____________'),
        'ЗАКАЗЧИК_ПОДПИСАНТ': value(company.get('signer_name')),
        'ЗАКАЗЧИК_ДОЛЖНОСТЬ': value(company.get('signer_position'), 'Директор'),
        'ФИО': value(person.get('name')),
        'ФИО_КРАТКО': value(rules.short_name(person.get('name'))),
        'ФАМИЛИЯ': value(last),
        'ИМЯ': value(first),
        'ОТЧЕСТВО': value(middle),
        'ИИН': value(person.get('iin'), '____________'),
        'ДОКУМЕНТ': 'паспорт' if is_passport else 'удостоверение личности',
        'ДОКУМЕНТ_ЗАГ': 'Паспорт' if is_passport else 'Удостоверение личности',
        'ДОКУМЕНТ_НОМЕР': value(person.get('doc_number'), '__________'),
        'ДОКУМЕНТ_КЕМ': value(person.get('doc_issuer'), 'МВД РК'),
        'ДОКУМЕНТ_КОГДА': rules.date_dots(issued) if issued else '__________',
        'ВЫДАНО': 'выдан' if is_passport else 'выдано',
        'ВЫДАНО_ЗАГ': 'Выдан' if is_passport else 'Выдано',
        'АДРЕС': value(person.get('registration_address')),
        'АДРЕС_ФАКТ': value(person.get('actual_address') or person.get('registration_address')),
        'ПРОЖИВАЮЩИЙ': rules.gender_word(gender, 'проживающий', 'проживающая'),
        'ИМЕНУЕМЫЙ': rules.gender_word(gender, 'именуемый', 'именуемая', 'именуемый(ая)'),
        'ТЕЛЕФОН': value(person.get('phone')),
        'EMAIL': value(person.get('email')),
        'TELEGRAM': value(person.get('telegram_nick')),
        'ДАТА_РОЖДЕНИЯ': rules.date_dots(person.get('birth_date')) or BLANK,
        'МЕСТО_РОЖДЕНИЯ': value(person.get('birth_place')),
        'ГРАЖДАНСТВО': value(person.get('citizenship')),
        'ДОЛЖНОСТЬ': value(contract.get('job_title') or person.get('job_title')),
        'СУММА': rules.money_text(amount) if amount is not None else '_________',
        'СУММА_ПРОПИСЬЮ': rules.amount_words(amount) if amount is not None else '__________________',
        'УЧЁБА_ГОД': _text(person.get('study_completion_year')),
        'УЧЁБА_МЕСТО': _text(person.get('study_place')),
        'УЧЁБА_СПЕЦИАЛЬНОСТЬ': _text(person.get('study_specialty')),
        'КОНТАКТ1_КТО': _text(person.get('close_contact_1_relation')),
        'КОНТАКТ1_ФИО': _text(person.get('close_contact_1_full_name')),
        'КОНТАКТ1_ТЕЛЕФОН': _text(person.get('close_contact_1_phone')),
        'КОНТАКТ2_КТО': _text(person.get('close_contact_2_relation')),
        'КОНТАКТ2_ФИО': _text(person.get('close_contact_2_full_name')),
        'КОНТАКТ2_ТЕЛЕФОН': _text(person.get('close_contact_2_phone')),
    }
    return values


# ── Заполнение Word ─────────────────────────────────────────────────────────

def _run_text(run):
    return ''.join(t.text or '' for t in run.iter(qn('w:t')))


def _set_run_text(run, text):
    texts = list(run.iter(qn('w:t')))
    if not texts:
        node = run.makeelement(qn('w:t'), {})
        run.append(node)
        texts = [node]
    texts[0].text = text
    texts[0].set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    for extra in texts[1:]:
        extra.text = ''


def _substitute(text, values):
    return MARKER.sub(lambda match: values.get(match.group(1).strip(), match.group(0)), text)


def _fill_paragraph(paragraph, values):
    runs = list(paragraph.iter(qn('w:r')))
    if not runs:
        return
    # 1) Метка целиком внутри одного куска — заменяем там же: оформление не трогаем.
    for run in runs:
        text = _run_text(run)
        if '{{' in text:
            _set_run_text(run, _substitute(text, values))
    # 2) Метка разрезана по кускам (так бывает после правки в Word) — собираем её в
    #    первый кусок, текст до и после остаётся в своих кусках.
    for _attempt in range(50):
        texts = [_run_text(run) for run in runs]
        full = ''.join(texts)
        match = MARKER.search(full)
        while match and match.group(1).strip() not in values:
            match = MARKER.search(full, match.end())
        if not match:
            return
        start, end = match.span()
        position, first, last = 0, None, None
        for index, text in enumerate(texts):
            if first is None and start < position + len(text):
                first = index
            if first is not None and end <= position + len(text):
                last = index
                break
            position += len(text)
        if first is None or last is None:
            return
        head_offset = sum(len(text) for text in texts[:first])
        tail_offset = sum(len(text) for text in texts[:last])
        replacement = values[match.group(1).strip()]
        head = texts[first][: start - head_offset]
        tail = texts[last][end - tail_offset:]
        if first == last:
            _set_run_text(runs[first], head + replacement + tail)
        else:
            _set_run_text(runs[first], head + replacement)
            for index in range(first + 1, last):
                _set_run_text(runs[index], '')
            _set_run_text(runs[last], tail)


def _parts(document):
    yield document.element.body
    for section in document.sections:
        for part in (section.header, section.footer, section.first_page_header, section.first_page_footer):
            try:
                yield part._element
            except Exception:  # noqa: BLE001 — колонтитула нет
                continue


def fill_docx(template_bytes, values):
    document = Document(BytesIO(template_bytes))
    seen = set()
    for part in _parts(document):
        if id(part) in seen:
            continue
        seen.add(id(part))
        for paragraph in list(part.iter(qn('w:p'))):
            _fill_paragraph(paragraph, values)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def check_template(content, file_name=''):
    """Проверка загруженного шаблона: это .docx, без внешних ссылок и не огромный.
    Возвращает найденные метки и незнакомые (опечатки)."""
    if not content:
        raise DocumentError('Файл пустой')
    if len(content) > MAX_TEMPLATE_BYTES:
        raise DocumentError('Шаблон больше 3 МБ — сохраните его без картинок или сожмите')
    if not str(file_name or '').lower().endswith('.docx'):
        raise DocumentError('Нужен файл Word в формате .docx')
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
            if 'word/document.xml' not in names:
                raise DocumentError('Это не документ Word')
            total = sum(info.file_size for info in archive.infolist())
            if total > 40 * 1024 * 1024:
                raise DocumentError('Шаблон распаковывается в слишком большой файл')
            if any(name.lower().endswith('vbaproject.bin') for name in names):
                raise DocumentError('В шаблоне есть макросы — сохраните его как обычный .docx')
            for name in names:
                if name.endswith('.rels') and _has_external_relationship(archive.read(name)):
                    raise DocumentError('В шаблоне есть внешние ссылки — уберите их и загрузите снова')
                if re.match(r'word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$', name) and \
                        _has_external_field(archive.read(name)):
                    raise DocumentError('В шаблоне есть поле-связь с другим файлом (LINK, INCLUDE) — '
                                        'в Word: «Файл → Сведения → Изменить связи → Разорвать связь»')
    except zipfile.BadZipFile:
        raise DocumentError('Файл повреждён или это не .docx') from None
    try:
        document = Document(BytesIO(content))
    except Exception:  # noqa: BLE001
        raise DocumentError('Word не открывает этот файл') from None
    text = []
    for part in _parts(document):
        for paragraph in part.iter(qn('w:p')):
            text.append(''.join(_run_text(run) for run in paragraph.iter(qn('w:r'))))
    markers = sorted(set(match.strip() for match in MARKER.findall('\n'.join(text))))
    unknown = [marker for marker in markers if marker not in KNOWN_MARKERS]
    return {'markers': markers, 'unknown': unknown}


_REL_NS = '{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'
EXTERNAL_FIELDS = ('LINK', 'INCLUDETEXT', 'INCLUDEPICTURE', 'INCLUDE', 'DDE', 'DDEAUTO')


def _has_external_relationship(xml_bytes):
    """Связь на внешний адрес. Разбор XML, а не поиск строки: атрибут бывает в одинарных
    кавычках и с пробелами вокруг «=»."""
    from xml.etree import ElementTree
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return True
    return any(str(node.get('TargetMode') or '').strip().lower() == 'external' for node in root.iter(_REL_NS))


def _has_external_field(xml_bytes):
    """Поле LINK/INCLUDE*/DDE — Word при открытии сходит по нему в чужой файл или адрес
    (для пути \\\\host\\share Windows ещё и отдаст хеш пароля)."""
    from xml.etree import ElementTree
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return True
    instr = []
    for node in root.iter():
        if node.tag == qn('w:instrText') and node.text:
            instr.append(node.text)
        elif node.tag == qn('w:fldSimple'):
            instr.append(' ' + str(node.get(qn('w:instr')) or '') + ' ')
    text = ' '.join(instr).upper()
    return any(re.search(r'(^|\s)%s(\s|$)' % field, text) for field in EXTERNAL_FIELDS)


def _safe_file_part(text):
    cleaned = re.sub(r'[\\/:*?"<>|]+', ' ', str(text or '')).strip()
    return ' '.join(cleaned.split())[:80] or 'документ'


def documents_bundle(person, company, contract, templates, today):
    """(имя файла, байты) — один .docx или .zip со всем пакетом."""
    values = document_values(person, company, contract, today)
    who = _safe_file_part(rules.short_name(person.get('name')))
    files = []
    for kind, template in templates:
        files.append((f'{who} — {DOC_TITLES[kind]}.docx', fill_docx(template, values)))
    if len(files) == 1:
        return files[0]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files:
            archive.writestr(name, payload)
    return f'{who} — документы приема.zip', buffer.getvalue()


# ── АВР ─────────────────────────────────────────────────────────────────────
AVR_TEMPLATE = TEMPLATES_DIR / 'avr.xlsx'


def _customer_line(company):
    parts = [rules.company_display_name(company), _text(company.get('legal_address')), _text(company.get('contacts'))]
    return ', '.join(part for part in parts if part)


def _executor_line(person):
    parts = [_text(person.get('name')), _text(person.get('registration_address') or person.get('actual_address')),
             _text(person.get('phone'))]
    return ', '.join(part for part in parts if part)


def _sheet_title(name, used):
    base = re.sub(r'[\[\]:*?/\\]', ' ', rules.short_name(name) or 'АВР').strip()[:28] or 'АВР'
    title, index = base, 2
    while title in used:
        title = f'{base[:25]} {index}'
        index += 1
    used.add(title)
    return title


def acts_workbook(acts):
    """acts: [{person, company, contract, number, act_date, period_from, period_to, work_name, amount}].
    Один лист на человека — HR печатает пачку разом."""
    workbook = load_workbook(AVR_TEMPLATE)
    base = workbook.active
    sheets = [base] + [workbook.copy_worksheet(base) for _ in acts[1:]]
    used = set()
    for sheet, act in zip(sheets, acts):
        person, company, contract = act['person'], act.get('company') or {}, act.get('contract') or {}
        sheet.title = _sheet_title(person.get('name'), used)
        sheet['E9'] = _customer_line(company)
        sheet['AQ9'] = _text(company.get('bin'))
        sheet['E11'] = _executor_line(person)
        sheet['AQ11'] = _text(person.get('iin'))
        number = _text(contract.get('number'))
        signed_on = contract.get('signed_on')
        sheet['F13'] = (f'Договор № {number} от {rules.date_long(signed_on)}' if number
                        else 'Договор №___ от ____________ г.')
        sheet['AH15'] = act['number']
        sheet['AM15'] = act['act_date']
        sheet['AM15'].number_format = 'DD.MM.YYYY'
        sheet['C20'] = act['work_name']
        sheet['C20'].alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
        sheet['P20'] = rules.act_period_text(act['period_from'], act['period_to'])
        sheet['P20'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        amount = act.get('amount')
        sheet['AG20'] = 1
        if amount is not None:
            sheet['AL20'] = float(amount)
            sheet['AR20'] = float(amount)
            sheet['AR21'] = float(amount)
            sheet['AR20'].number_format = '#,##0.00'
        sheet['F29'] = _text(contract.get('job_title') or person.get('job_title'))
        sheet['R29'] = rules.short_name(person.get('name'))
        sheet['AF29'] = _text(company.get('signer_position')) or 'Директор'
        sheet['AR29'] = _text(company.get('signer_name'))
        # Печать — как в образце: альбом, по ширине листа.
        sheet.page_setup.orientation = 'landscape'
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 1
        sheet.print_area = 'A1:AW34'
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def acts_file_name(acts, period_to):
    month = rules.MONTHS_NOMINATIVE[period_to.month - 1] if period_to else ''
    if len(acts) == 1:
        return f"АВР {_safe_file_part(rules.short_name(acts[0]['person'].get('name')))} {month} {period_to.year}.xlsx"
    return f'АВР {month} {period_to.year} — {len(acts)} чел.xlsx'


def today_default():
    return date.today()
