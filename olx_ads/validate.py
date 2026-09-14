# -*- coding: utf-8 -*-
"""Правила OLX для текста объявления. Чистая логика: ни базы, ни сети.

Зачем это отдельным модулем. Объявление, не прошедшее правила площадки,
отклоняется НА СТОРОНЕ OLX уже после отправки — то есть человек узнаёт об ошибке,
когда текст уже улетел, а объявление ушло на проверку. Дешевле поймать это до
отправки: те же правила считаются локально, показываются прямо в редакторе и
блокируют кнопку «Применить».

Откуда взяты правила (спецификация Partner API, раздел «Validation rules»,
и справка OLX.kz), с числами, проверенными на 284 живых объявлениях 14.09.2026:

* заголовок 16–70 символов. В спецификации API стоит 150, но и справка OLX.kz
  («Максимальное количество символов - 70»), и интерфейс кабинета, и ТЗ #299
  говорят 70; из 281 живого заголовка ни один не длиннее 70, а ровно 70 — шесть.
  Берём 70: пропустив 150, мы получим текст, который не сохранит человек руками;
* описание 80–9000 символов. Живьём 351–1269, то есть запас огромный;
* заглавных не больше половины текста. Это правило легко нарушить случайно:
  наши же тексты пишут подзаголовки капсом («ВАШИ ПРЕИМУЩЕСТВА С НАМИ»), и
  десяток таких строк подряд переводит объявление за половину;
* три одинаковых знака препинания подряд запрещены;
* телефоны и адреса почты в заголовке и описании запрещены. Телефон ищем ПО
  ФОРМЕ казахстанского номера, а не «семь цифр подряд»: в наших же текстах
  сплошь суммы вида «1 600 000 тг» и «300 000 ₸», и наивная проверка ругалась бы
  на призовой фонд;
* HTML в описании — только <p>, <ul>, <li>, <strong>, <em>, и только в рубрике
  «Работа». Кабинет arenda сидит в категории 3031 «Аренда авто» — это НЕ
  «Работа», и там HTML отмечается предупреждением.

Ошибка (`error`) блокирует отправку, предупреждение (`warning`) — нет: часть
правил площадки описана нестрого, и превращать догадку в запрет значит мешать
работать. Что именно догадка — помечено в каждом правиле.
"""

import re

TITLE_MIN = 16
TITLE_MAX = 70
DESCRIPTION_MIN = 80
DESCRIPTION_MAX = 9000

# Теги, разрешённые OLX в описании. Всё прочее вырезается перед отправкой.
ALLOWED_TAGS = ('p', 'ul', 'li', 'strong', 'em')

# Рубрика «Работа»: только в ней документация разрешает HTML в описании.
# 1812 «Работа в такси», 1802 «Курьер», 1801 «Водитель», 1942 «Другое» —
# всё это ветки категории 6 «Работа». 3031 «Аренда авто» растёт из «Аренды и
# проката товаров» и в список не входит намеренно.
JOBS_CATEGORY_IDS = (1801, 1802, 1812, 1942)

_TAG = re.compile(r'<\s*/?\s*([a-zA-Z0-9]+)[^>]*>')
_ANY_TAG = re.compile(r'<[^>]+>')
_SPACES = re.compile(r'[ \t\r\f\v]+')

# Знаки, которые нельзя ставить трижды подряд (перечень из спецификации).
_PUNCT_RUN = re.compile(r'([!?.,\-=+#%&@*_><:()|])\1{2,}')

_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')

# Форма казахстанского номера: +7/8, затем 3-3-2-2 с любыми разделителями.
# Именно форма, а не «много цифр»: суммы в текстах («1 600 000 тг») под неё не
# попадают, а номер «8 700 123 45 67» попадает.
_PHONE = re.compile(r'(?:\+?7|8)[\s\-.(]{0,3}\d{3}[\s\-.)]{0,3}\d{3}'
                    r'[\s\-.]{0,3}\d{2}[\s\-.]{0,3}\d{2}')


def plain_text(html):
    """Текст без разметки. По нему считаются длина и доля заглавных."""
    if not html:
        return ''
    text = re.sub(r'<\s*(br|/p|/li|/ul)\s*/?\s*>', '\n', str(html), flags=re.I)
    text = _ANY_TAG.sub(' ', text)
    text = (text.replace('&nbsp;', ' ').replace('&amp;', '&')
                .replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"'))
    text = _SPACES.sub(' ', text)
    return '\n'.join(line.strip() for line in text.split('\n')).strip()


def capital_share(text):
    """Доля заглавных среди букв. Считаем от БУКВ, а не от всех символов.

    От всех символов доля всегда мала — пробелы и знаки размывают её, — и
    правило «не больше половины» никогда бы не сработало, хотя у OLX оно
    срабатывает. Поэтому знаменатель — буквы.
    """
    letters = [ch for ch in (text or '') if ch.isalpha()]
    if not letters:
        return 0.0
    upper = sum(1 for ch in letters if ch.isupper())
    return upper / float(len(letters))


def used_tags(html):
    """Какие теги встречаются в описании."""
    return sorted({m.lower() for m in _TAG.findall(str(html or ''))})


def trim_title(text, limit=TITLE_MAX):
    """Укоротить заголовок ДО ГРАНИЦЫ СЛОВА.

    ТЗ 2.4 требует проверять длину и «не обрезать текст посередине слова».
    Обрезаем по последнему пробелу и подчищаем висящий разделитель — заголовки
    у нас собраны из кусков через «|», и «Работа в такси |» выглядит как брак.
    """
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if ' ' in cut:
        cut = cut[:cut.rindex(' ')]
    return cut.rstrip(' |/,-–—').strip()


def _problem(level, field, code, message):
    return {'level': level, 'field': field, 'code': code, 'message': message}


def check_title(title):
    out = []
    text = (title or '').strip()
    length = len(text)
    if not text:
        out.append(_problem('error', 'title', 'empty', 'Заголовок пустой'))
        return out
    if length < TITLE_MIN:
        out.append(_problem('error', 'title', 'too_short',
                            'Заголовок короче %d символов (сейчас %d)'
                            % (TITLE_MIN, length)))
    if length > TITLE_MAX:
        out.append(_problem('error', 'title', 'too_long',
                            'Заголовок длиннее %d символов (сейчас %d)'
                            % (TITLE_MAX, length)))
    if capital_share(text) > 0.5:
        out.append(_problem('error', 'title', 'caps',
                            'В заголовке больше половины заглавных букв'))
    if _EMAIL.search(text):
        out.append(_problem('error', 'title', 'email',
                            'В заголовке адрес почты — OLX такое не принимает'))
    if _PHONE.search(text):
        out.append(_problem('error', 'title', 'phone',
                            'В заголовке номер телефона — OLX такое не принимает'))
    if _PUNCT_RUN.search(text):
        out.append(_problem('error', 'title', 'punctuation',
                            'Три одинаковых знака подряд'))
    return out


def check_description(description, category_id=None):
    out = []
    raw = description or ''
    text = plain_text(raw)
    length = len(text)
    if not text:
        out.append(_problem('error', 'description', 'empty', 'Описание пустое'))
        return out
    if length < DESCRIPTION_MIN:
        out.append(_problem('error', 'description', 'too_short',
                            'Описание короче %d символов (сейчас %d)'
                            % (DESCRIPTION_MIN, length)))
    if length > DESCRIPTION_MAX:
        out.append(_problem('error', 'description', 'too_long',
                            'Описание длиннее %d символов (сейчас %d)'
                            % (DESCRIPTION_MAX, length)))
    if capital_share(text) > 0.5:
        out.append(_problem('error', 'description', 'caps',
                            'В описании больше половины заглавных букв'))
    if _EMAIL.search(text):
        out.append(_problem('error', 'description', 'email',
                            'В описании адрес почты — OLX такое не принимает'))
    if _PHONE.search(text):
        out.append(_problem('error', 'description', 'phone',
                            'В описании номер телефона — OLX такое не принимает'))
    if _PUNCT_RUN.search(text):
        out.append(_problem('error', 'description', 'punctuation',
                            'Три одинаковых знака подряд'))

    extra = [tag for tag in used_tags(raw) if tag not in ALLOWED_TAGS]
    if extra:
        out.append(_problem('error', 'description', 'tags',
                            'OLX не примет теги: %s' % (', '.join(extra),)))
    if (used_tags(raw) and category_id is not None
            and int(category_id) not in JOBS_CATEGORY_IDS):
        # Предупреждение, а не запрет: документация разрешает HTML «только в
        # рубрике Работа», но что именно делает OLX с разметкой в «Аренде авто»
        # — не проверено, а на проде там и так плоский текст.
        out.append(_problem('warning', 'description', 'html_outside_jobs',
                            'Это не рубрика «Работа» — разметку OLX может не принять'))
    return out


def check(title, description, category_id=None):
    """Все замечания к тексту объявления."""
    return check_title(title) + check_description(description, category_id)


def blocking(problems):
    """Только то, что запрещает отправку."""
    return [p for p in problems or [] if p.get('level') == 'error']


def is_clean(title, description, category_id=None):
    return not blocking(check(title, description, category_id))
