"""Справочник «Парки» в помощнике: карточка парка и сводка всех парков.

Просьба владельца 30.10.2026: «сделай так, чтобы помощник мог читать
информацию из раздела вики «Парки», чтобы знал всю информацию о таксопарках».
Во вкладке «Парки» (wiki_taxi_parks, wiki/parks.py) у парка комиссия, номера,
офисы, сайт, акции и описание с условиями — в описании же «комиссия при выводе
средств». Помощник брал из вкладки только названия (узнать парк в вопросе,
directory.match_parks), а сами сведения — нет. Журнал за 100 дней (10.08–
30.10.2026): 111 вопросов называют парк, ещё около пятидесяти спрашивают про
парки вообще («комиссия парков», «номера телефонов парков»). На «по парку
Департамент сколько комиссия» и «Комиссия в парке Itaxi» помощник отвечал «нет
информации» или архивом, на «расскажи о таксопарке глобал» — переспрашивал,
хотя во вкладке у всех троих стоит 2%.

Модуль, как и прочие справочники (directory.py, registration.py), сам в базу не
ходит: SQL живёт в parks.py и offices.py под стражей пространства, здесь —
разбор вопроса, решение и текст фрагментов.

Что показывается:
  * назван парк — его карточка целиком: комиссия, головной офис, офисы и
    номера, сайт, действующие акции, описание. Названо больше трёх — сводка по
    названным: четыре описания по полторы тысячи знаков — уже не ответ;
  * парк не назван, а спросили про парки вообще — сводка всех парков: комиссия
    и номера, на вопрос о выводе — ещё строки вывода, на «инфо про каждый» —
    первая фраза описания;
  * продолжение разговора («а номер?», «а при выводе?») — карточка или сводка
    по последней реплике человека.

Где карточка лишняя. «Куда регистрировать водителя айтакси из Рудного» и
«адрес айтакси в Шымкенте» отвечают справочники регистрации и офисов, а в
описании парка перечислены города («представлен в Алматы, Астане, Таразе…»):
модель взяла бы город оттуда — тот же урок, что у регистрации (29.10.2026):
отсутствие данных во фрагменте держит лучше запрета в промпте. Поэтому, если
ответили они, а о самом парке не спросили ничего, карточки нет.

Доступ — как у вкладки: пространство выдано не гостем, и вкладка «Парки» в нём
включена (directory.directory_space с tabs=('parks',)).
"""

import re

from . import directory
from . import offices as wiki_offices
from . import parks as wiki_parks
from . import registration
from .ai import currency
from .ai.answer import FOLLOWUP_TURNS

SOURCE_KIND = 'park'
SOURCE_TAB = 'parks'
TITLE = 'Парки'
# Номера фрагментов: у «Списков Байги» с тысячи, у «Классификатора авто» с двух,
# у регистрации с трёх тысяч, у парков — с четырёх (сшивка источников ищет
# фрагмент по chunk_id, и одинаковые номера у соседних веток спутали бы цитаты).
_CHUNK_BASE = 4000
# Карточек в одном ответе; больше — сводка по названным паркам.
MAX_CARDS = 3
# Продолжение разговора — короткая реплика: «а номер?», «а комиссия при выводе?».
FOLLOWUP_WORDS = 5
# Слаг уезжает в источник (ref_key — VARCHAR(64) в wiki_ai_message_sources):
# длиннее не влезет, и чип откроет вкладку «Парки», а не страницу парка.
REF_KEY_LIMIT = 64
# Первая фраза описания в сводке — не длиннее этого.
GIST_LIMIT = 220

# Справочники, которые отвечают на вопрос о парке лучше карточки: город
# регистрации и офис в названном городе (см. шапку).
ANSWERED_ELSEWHERE = frozenset(('registration', 'office'))

_LETTERS = r'a-zа-я0-9'


def _bounded(pattern):
    return re.compile(r'(?<![%s])(?:%s)' % (_LETTERS, pattern))


# ── Что спросили о парке ─────────────────────────────────────────────────────
#
# Слова — из журнала помощника (10.08–30.10.2026), с опечатками, которые там
# есть: «коммисия», «комисиия», «скоко проц», «сдерживается», «теххподдержка».
# Казахские — свёрнутыми (directory.normalize): «нөмір» — «номир», «қанша» —
# «канша», «барлық» — «барлык», «ақпарат» — «акпарат».

_COMMISSION = _bounded(
    r'к[оа]м+[иы]?с+\w*|процент\w*|проц(?![%s])|%%'
    r'|сколько\s+(?:\w+\s+){0,2}(?:берет|берут|снимает|снимают|удерживает|удерживают'
    r'|удерживается|сдерживается|забирает|забирают)' % _LETTERS)
# «Выведи список» — просьба показать, а не вывод денег: «вывед…» здесь нет.
_WITHDRAWAL = _bounded(r'вывод\w*|вывест\w*|сняти\w*|снять|сниму|выплат\w*')
# Пополнение — не вывод: его комиссия (Kaspi, 3%) — в статьях, и строки «при
# выводе средств» из карточки на «процент при пополнении» были бы чужим ответом.
_TOPUP = _bounded(r'пополн\w*')
_PHONE = _bounded(
    r'номер\w*|номир\w*|телефон\w*|контакт\w*|связ\w*|позвон\w*|звон\w*|whatsapp|ватсап\w*'
    r'|вацап\w*|вотсап\w*|тех+\s*поддерж\w*|поддержк\w*|байланыс\w*')
_SITE = _bounded(r'сайт\w*|site|лендинг\w*|ссылк\w*')
_INFO = _bounded(
    r'услови\w*|инфо\w*|расскаж\w*|подробн\w*|что\s+(?:такое|за)|кто\s+так\w*'
    r'|характеристик\w*|описани\w*|особенност\w*|преимуществ\w*|отлича\w*'
    r'|на\s+ч[её]м\s+зарабатыва\w*|туралы|акпарат\w*')
_PROMO = _bounded(r'акци\w*|бонус\w*|розыгрыш\w*|конкурс\w*|мотивац\w*|приз\w*')
# Комиссия Яндекса — не комиссия парка: её дают «Города».
_YANDEX = _bounded(r'яндекс\w*|yandex|сервис\w*')
# Адрес и офис — тоже о парке, но отвечает на них и справочник офисов: на
# продолжение («а адрес?») карточка нужна, а снимать ради них карточку при
# ответе офисов незачем — им и так ответили.
_PLACE = _bounded(r'адрес\w*|офис\w*|где(?![%s])|мекенжай\w*|кенсе\w*' % _LETTERS)

# Слово «парк» во множественном или единственном числе — но не «парковый ИП» и
# не «парковка». Казахское «таксопарктер» и опечатки журнала («таксопрака»,
# «честный таксопарак») — тоже.
_PARK = (r'(?:такс[оа]\s*|такос)?п(?:ар|ра|ара)к(?:а|у|ом|е|ов|и|ам|ами|ах|т[еа]р\w*)?(?![%s])'
         % _LETTERS)
_PARK_WORD = _bounded(_PARK)
# Про все парки сразу: «все парки», «каждый таксопарк», «каталог таксопарков»,
# «какие у нас парки», «номера разных таксопарков». Слово-перечень стоит рядом
# со словом «парк»: «какие документы нужны для подключения к парку» — вопрос о
# документах, а не просьба перечислить парки.
_ALL_PARKS = _bounded(
    r'(?:все|всех|каждый|каждого|каждому|каждом|разных|наши|наших|других|остальных|барлык)\s+'
    + _PARK
    + r'|(?:список|списк\w*|перечень|перечисл\w*|катало\w*)\s+(?:\w+\s+)?' + _PARK
    + r'|' + _PARK + r'\s+(?:список|перечень)'
    + r'|какие\s+(?:\w+\s+){0,2}' + _PARK
    + r'|сколько\s+(?:\w+\s+){0,2}' + _PARK)


# Слова, которые вопрос о парках объясняют, — по началу слова: то, что спрошено,
# слова «парк» и «все/каждый/список», глаголы комиссии. Короче четырёх букв,
# стоп-слова и «подскажите» справочника отбрасываются и так (directory).
_PARK_VOCABULARY = (
    'комис', 'коммис', 'камис', 'процент', 'проц', 'удерж', 'сдерж', 'взим', 'взым',
    'списыва', 'составля', 'вывод', 'вывест', 'сняти', 'снять', 'сниму', 'пополн', 'выплат',
    'номер', 'номир', 'телефон', 'контакт', 'связ', 'позвон', 'звон', 'whatsapp', 'ватсап',
    'вацап', 'вотсап', 'тех', 'поддерж', 'байланыс', 'сайт', 'site', 'лендинг', 'ссылк',
    'услови', 'инфо', 'информац', 'расскаж', 'подробн', 'такое', 'характеристик', 'описан',
    'особенност', 'преимуществ', 'отлича', 'зарабатыва', 'туралы', 'акпарат', 'акци', 'бонус',
    'розыгрыш', 'конкурс', 'мотивац', 'приз', 'адрес', 'офис', 'мекенжай', 'кенсе', 'находит',
    'парк', 'прак', 'таксопар', 'таксапар', 'таксопр', 'такоспар', 'такси', 'taxi',
    'каждый', 'каждого', 'каждому', 'каждом', 'разных', 'других', 'остальных', 'барлык',
    'список', 'списк', 'перечень', 'перечисл', 'катало', 'какие', 'какая', 'какой', 'каких',
    'каков', 'сколько', 'скоко', 'канша', 'есть', 'всех', 'наши', 'наших', 'выведи', 'покажи',
    'напиши', 'дай',
)
# Названия-обычные слова: «у водителя стабильный интернет», «честный отзыв»,
# «департамент транспорта», «какие регионы обслуживаем» (разбор 30.10.2026).
# Такой парк назван, только если о нём что-то спросили, рядом стоит «парк» или
# «такси» или в реплике нет ничего, кроме названия («департамент» — тоже вопрос:
# так в журнале спрашивали про парк). По началу слова: падежи — те же слова.
_COMMON_WORD_NAMES = ('стабильн', 'честн', 'департамент', 'регион', 'халык', 'аманат')
_TAXI_WORD = _bounded(r'такси|taxi')
# Союзы между названиями: «сравни iTaxi и iTaxi VIP», «чем отличается айтакси
# от айтакси вип». Знаки препинания до разбора уже стали пробелами.
_SEGMENT = re.compile(r'\s(?:и|или|от|vs|против|с|со|а)\s')
# «Такси Про» — приложение, а не «такси» с предлогом: «акции на такси про» —
# вопрос о приложении, и продолжением про прошлый парк он не становится.
_APP_NAME = _bounded(r'(?:такси|taxi|яндекс|yandex)\s*(?:про|pro)(?![%s])' % _LETTERS)


def _any(pattern, variants):
    return any(pattern.search(value) for value in variants or ())


def unexplained(variants):
    """Слова реплики, которых вопрос о парке не объясняет. [] — объяснено всё.

    Так короткая реплика отличает продолжение («а номер?», «а при выводе?») от
    новой темы («условия конкурса лимонопад», «как поменять номер клиента»), а
    вопрос про все парки («комиссия парков») — от вопроса про парк, которого во
    вкладке нет («номер таксопарка Достойный»). Хватает одного написания без
    лишних слов — латиница и её транслит."""
    best = None
    for value in variants or ():
        rest = []
        for word in _APP_NAME.sub(' приложение ', value).split():
            if (len(word) < 4 or word.isdigit() or word in directory._STOP_WORDS
                    or word in directory._FILLER_WORDS or word.startswith(_PARK_VOCABULARY)):
                continue
            rest.append(word)
        if best is None or len(rest) < len(best):
            best = rest
    return best or []


def asked_facts(variants):
    """Что спрошено о парке — по написаниям одной реплики. Пустое множество —
    о самом парке не спросили ничего (назвали его рядом с другим вопросом)."""
    found = set()
    for name, pattern in (('commission', _COMMISSION), ('withdrawal', _WITHDRAWAL),
                          ('topup', _TOPUP), ('phone', _PHONE), ('site', _SITE),
                          ('info', _INFO), ('promo', _PROMO)):
        if _any(pattern, variants):
            found.add(name)
    return found


def analyze(question, search_query=None, history=None):
    """Разбор вопроса поверх разбора регистрации и справочника (registration,
    directory): регистрацию — «куда зарегать водителя айтакси?» — карточке надо
    узнавать и без строк регистрации (plan).

    history — прошлые реплики разговора (store.recent_turns): продолжение
    («а номер?») берёт парк из ПОСЛЕДНЕЙ реплики человека, а не из склейки двух
    (search_query), — иначе «itaxi адрес в шымкенте» → «акции на такси про» →
    «акции» приводило карточку iTaxi в разговор о приложении.

    Чистая функция: парков она не знает, их подставляет plan.
    """
    base = registration.analyze(question, search_query)
    own = base.get('own_variants') or base['variants']
    prior = [str(turn.get('text') or '').strip() for turn in (history or ())
             if turn.get('role') == 'user' and str(turn.get('text') or '').strip()]
    return dict(base,
                park_own_variants=own,
                park_facts=asked_facts(own),
                park_place=_any(_PLACE, own),
                park_word=_any(_PARK_WORD, own),
                park_taxi=_any(_TAXI_WORD, own),
                park_all=_any(_ALL_PARKS, own),
                park_yandex=_any(_YANDEX, own),
                park_rest=unexplained(own),
                park_short=len(base['words']) <= FOLLOWUP_WORDS,
                park_prior=prior[-FOLLOWUP_TURNS:])


def _name_forms(park):
    forms = set()
    for words in directory.park_name_words(park.get('name')):
        for word in words:
            forms |= directory.park_word_forms(word)
    return forms


def _common_word(park):
    """Есть ли у парка имя из обычного слова (_COMMON_WORD_NAMES)."""
    return any(all(word.startswith(_COMMON_WORD_NAMES) for word in words)
               for words in directory.park_name_words(park.get('name')))


def named_parks(analysis, parks):
    """Парки, названные в самой реплике, — с двумя поправками к match_parks.

    «iTaxi и iTaxi VIP» — два парка: match_parks из вложенных имён оставляет
    длинное, и короткое, названное отдельно, пропадало. Поэтому вложенные имена
    перепроверяются по частям реплики между союзами («и», «или», «от»).

    Парк с именем из обычного слова («стабильный интернет») назван, только если
    о нём спросили, рядом «парк»/«такси» или в реплике нет других слов."""
    variants = analysis['park_own_variants']
    found = directory.match_parks({'variants': variants}, parks)
    names = {park['id']: directory.normalize(park.get('name')) for park in parks}
    ids = {park['id'] for park in found}
    shadowed = [park for park in parks if park['id'] not in ids and any(
        names[park['id']] in names[other['id']] for other in found)]
    if shadowed:
        for value in variants:
            for part in _SEGMENT.split(value):
                for park in directory.match_parks({'variants': [part]}, shadowed + found):
                    if park['id'] not in ids:
                        ids.add(park['id'])
                        found.append(park)
    if analysis['park_facts'] or analysis['park_word'] or analysis['park_taxi']:
        return found
    kept = []
    for park in found:
        if _common_word(park):
            forms = _name_forms(park)
            if [word for word in analysis['park_rest'] if not directory._stem_hit(word, forms)]:
                continue
        kept.append(park)
    return kept


def follows_up(analysis):
    """Короткое продолжение о парке: «а номер?», «а при выводе?», «подробнее» —
    спрошено что-то о парке, и посторонних слов нет («условия конкурса
    лимонопад» — уже новая тема)."""
    return bool(analysis['park_short'] and (analysis['park_facts'] or analysis['park_place'])
                and not analysis['park_all'] and not analysis['park_rest'])


def wants_summary(analysis):
    """Вопрос про парки вообще: «комиссия парков», «номера телефонов парков»,
    «каталог таксопарков», «инфо про каждый таксопарк».

    Каждое слово про парки: «номер таксопарка Достойный» спрашивает про парк,
    которого во вкладке нет, а «что такое таксопарк… работает в определённой
    локации» — про понятие. Бонусы и пополнение — тоже мимо: «сколько процентов
    берёт парк с бонусов», «процент парка при пополнении» отвечают статьи, а
    таблица «комиссия парка 2%» была бы чужим ответом. Комиссия Яндекса — тоже
    («комиссия яндекса во всех парках»): её дают «Города»."""
    facts = analysis['park_facts']
    if not analysis['park_word'] or analysis['park_rest'] or facts & {'promo', 'topup'}:
        return False
    wanted = facts & {'commission', 'phone', 'withdrawal'}
    if analysis['park_yandex'] and not wanted - {'commission'}:
        return False
    return bool(analysis['park_all'] or wanted)


def plan(analysis, parks, *, answered=()):
    """Что показать: {'cards': [парк], 'summary': [парк] | None, 'extras': set()}.

    Пустые cards и summary — показывать нечего. answered — source_kind строк,
    которые к этому вопросу уже собрали другие справочники (routes_ai).
    """
    result = {'cards': [], 'summary': None, 'extras': set()}
    if not parks:
        return result
    facts = analysis['park_facts']
    named = named_parks(analysis, parks)
    summary = False
    if not named and follows_up(analysis):
        # Продолжение: парк или сводка — из последней реплики человека; реплика,
        # которая сама была таким продолжением, отсылает к своей предыдущей.
        # Раньше своей сводки: «а какая комиссия парка» после вопроса про iTaxi —
        # про iTaxi, а не про все парки.
        for previous in reversed(analysis['park_prior']):
            earlier = analyze(previous)
            named = named_parks(earlier, parks)
            # «Номера телефонов офисов» после «номера телефонов парков» — уже про
            # офисы: их даёт справочник офисов, а не сводка парков.
            summary = not named and not analysis['park_place'] and wants_summary(earlier)
            if named or summary or not follows_up(earlier):
                break
    if not named and not summary:
        summary = wants_summary(analysis)
    if named:
        # «Куда зарегать водителя айтакси?», «адрес айтакси в Шымкенте» — город
        # дают регистрация и офисы (а без города регистрация попросит его сама),
        # и карточка со списком городов из описания там только мешает.
        if not facts and (analysis['registration'] or set(answered or ()) & ANSWERED_ELSEWHERE):
            return result
        if len(named) > MAX_CARDS:
            result['summary'] = named
            result['extras'] = facts & {'withdrawal', 'info'}
        else:
            result['cards'] = named
        return result
    if summary:
        result['summary'] = list(parks)
        extras = facts & {'withdrawal'}
        # Первая фраза описания — только на «инфо про каждый парк»: «что такое
        # таксопарк» спрашивает не про наши парки.
        if analysis['park_all'] and 'info' in facts:
            extras.add('info')
        result['extras'] = extras
    return result


# ── Текст фрагментов ─────────────────────────────────────────────────────────

_BULLET = re.compile(r'^[\s•·\-–—*]+')


def _lines(text):
    """Строки описания без пустых, табов и двойных пробелов."""
    return [' '.join(line.split()) for line in str(text or '').splitlines() if line.strip()]


def _dated(line, on_date):
    """Строка описания с прошедшим сроком помечается прямо в тексте.

    Карточку целиком помечать несвежей нельзя (currency.chunk_currency её
    пропускает): «акция до 30.09.2026» в описании объявила бы «уже не
    действующими» и комиссию, и номера парка — живые данные вкладки."""
    found = currency.deadlines(line)
    if found and max(found) < (on_date or currency.today()):
        return '(срок истёк %s) %s' % (max(found).strftime('%d.%m.%Y'), line)
    return line


def withdrawal_lines(description):
    """Пункты «Комиссия парка при выводе средств:» из описания — без маркеров.

    Заголовок — строка со словом «вывод» и двоеточием в конце, пункты — строки
    под ним, пока идут маркеры «•». Так их пишут во всех карточках прода."""
    found, inside = [], False
    for line in _lines(description):
        if inside and _BULLET.match(line):
            found.append(_BULLET.sub('', line).rstrip(';,. '))
            continue
        inside = line.endswith(':') and bool(_WITHDRAWAL.search(directory.normalize(line)))
    return [line for line in found if line]


def gist(description):
    """Первая фраза описания — для сводки «инфо про каждый парк»."""
    lines = _lines(description)
    if not lines:
        return ''
    first = re.split(r'(?<=[.!?])\s+', lines[0], maxsplit=1)[0]
    return first if len(first) <= GIST_LIMIT else first[:GIST_LIMIT].rstrip() + '…'


def _number(phone):
    number = str(phone.get('phone') or '').strip()
    return number + (' (%s)' % phone['note'] if phone.get('note') else '')


def _office_label(office):
    name = '«%s»' % office.get('name')
    return '%s (%s)' % (name, office['city']) if office.get('city') else name


def card_text(park, *, offices=(), phones=None, promotions=(), on_date=None):
    """Карточка парка — по строке на факт, с названием парка в каждой.

    Помощник цитирует одну лучшую строку (answer.pick_excerpt), а проверка чисел
    смотрит только в текст фрагмента: телефон, процент и суммы обязаны стоять
    здесь буквально. Чего в карточке нет (сайта, акций), о том ни слова:
    «акций нет» спорило бы со статьёй «Все акции», где они расписаны. Комиссии
    это тоже касается: «в карточке не указана» модель переносила бы дословно, и
    пробел в данных не уходил бы супервайзеру — признание «в доступных
    фрагментах нет информации о комиссии» уходит (answer.admits_missing).
    """
    name = park['name']
    lines = ['Таксопарк «%s».' % name]
    if park.get('commission') is not None:
        lines.append('Комиссия парка %s: %s.' % (name, directory.format_percent(park['commission'])))
    if park.get('city'):
        lines.append('Город парка %s: %s.' % (name, park['city']))
    head = park.get('head_office') or {}
    if head.get('name'):
        line = 'Головной офис парка %s: «%s»' % (name, head['name'])
        if head.get('city'):
            line += ', город %s' % head['city']
        if head.get('address'):
            line += ', адрес: %s' % str(head['address']).strip().rstrip('.,')
        lines.append(line + '.')
    elif park.get('address'):
        lines.append('Адрес парка %s: %s.' % (name, str(park['address']).strip().rstrip('.,')))
    for phone in (phones or {}).get(None, []):
        if phone.get('phone'):
            lines.append('Телефон парка %s: %s.' % (name, _number(phone)))
    for office in offices or ():
        numbers = [_number(phone) for phone in office.get('phones') or () if phone.get('phone')]
        if numbers:
            for number in numbers:
                lines.append('Телефон парка %s в офисе %s: %s.' % (name, _office_label(office), number))
        else:
            lines.append('Офис парка %s: %s.' % (name, _office_label(office)))
    if park.get('website'):
        lines.append('Сайт парка %s: %s.' % (name, str(park['website']).strip()))
    for promo in promotions or ():
        line = 'Акция парка %s «%s»' % (name, promo.get('title'))
        period = _period(promo)
        if period:
            line += ', %s' % period
        if promo.get('description'):
            line += ': %s' % ' '.join(str(promo['description']).split()).rstrip('.')
        lines.append(line + '.')
    description = _lines(park.get('description'))
    if description:
        lines.append('Описание парка %s:' % name)
        lines.extend(_dated(line, on_date) for line in description)
    return '\n'.join(lines)


def _dmy(value):
    day = wiki_offices.parse_day(value)
    return day.strftime('%d.%m.%Y') if day else ''


def _period(promo):
    start, end = _dmy(promo.get('starts_at')), _dmy(promo.get('ends_at'))
    if start and end:
        return 'с %s по %s' % (start, end)
    if end:
        return 'по %s' % end
    if start:
        return 'с %s' % start
    return ''


def _phones_of(offices, phones):
    """Номера парка для сводки: (сколько, строка). Сперва без офиса, потом по
    офисам — «в офисе «…» (город) номер»: через запятую с номерами без офиса
    офисный номер читался бы одним из них."""
    count, parts = 0, []
    online = [_number(phone) for phone in (phones or {}).get(None, []) if phone.get('phone')]
    if online:
        count += len(online)
        parts.append(', '.join(online))
    for office in offices or ():
        numbers = [_number(phone) for phone in office.get('phones') or () if phone.get('phone')]
        if numbers:
            count += len(numbers)
            parts.append('в офисе %s %s' % (_office_label(office), ', '.join(numbers)))
    return count, '; '.join(parts)


def summary_text(parks, *, offices=None, phones=None, promotions=(), extras=(), total=None):
    """Сводка по паркам — строка на парк: комиссия и номера; по просьбе —
    строки вывода средств и первая фраза описания."""
    offices, phones = offices or {}, phones or {}
    if total is not None and total == len(parks):
        head = 'Таксопарки во вкладке «Парки» — %d. Комиссия парка и номера:' % total
    else:
        head = 'Таксопарки из вопроса — комиссия парка и номера:'
    lines = [head]
    for park in parks:
        name = park['name']
        commission = ('комиссия парка %s' % directory.format_percent(park['commission'])
                      if park.get('commission') is not None else 'комиссия парка не указана')
        count, numbers = _phones_of(offices.get(park['id']), phones.get(park['id']))
        line = '%s: %s' % (name, commission)
        if count:
            line += '; %s: %s' % ('телефон' if count == 1 else 'телефоны', numbers)
        lines.append(line + '.')
        if 'withdrawal' in extras:
            pulled = withdrawal_lines(park.get('description'))
            if pulled:
                lines.append('%s, комиссия при выводе средств: %s.' % (name, '; '.join(pulled)))
        if 'info' in extras:
            first = gist(park.get('description'))
            if first:
                lines.append('%s, о парке: %s' % (name, first))
    by_id = {park['id']: park['name'] for park in parks}
    for promo in promotions or ():
        names = [by_id[park_id] for park_id in promo.get('park_ids') or () if park_id in by_id]
        if names:
            period = _period(promo)
            lines.append('Акция «%s» — парки: %s%s.' % (promo.get('title'), ', '.join(names),
                                                       '; ' + period if period else ''))
    return '\n'.join(lines)


def _row(index, *, text, heading, space_id, parks, park=None):
    slug = str((park or {}).get('slug') or '')
    return {
        # Отрицательный id: у настоящих кусков он положителен (см. _CHUNK_BASE).
        'chunk_id': -(_CHUNK_BASE + index + 1),
        'article_id': None,
        'chunk_idx': 0,
        'title': TITLE,
        'slug': '',
        'heading_path': heading,
        'text': text,
        'requires_ack': False,
        'historical': False,
        'similarity': None,
        'found_by': [directory.DIRECTORY_BRANCH],
        'directory_hit': True,
        'source_kind': SOURCE_KIND,
        'tab': SOURCE_TAB,
        # Чип под ответом открывает страницу парка: по слагу — как рельс витрины.
        'ref_id': park['id'] if park else None,
        'ref_key': slug if slug and len(slug) <= REF_KEY_LIMIT else None,
        'ref_city': None,
        'space_id': space_id,
        # Парки фрагмента — для подшивки источника (answer._names_park): сервер
        # ставит карточку под ответ сам, только если ответ этот парк назвал и
        # повторил её число — номер, сумму или саму комиссию («2%»).
        'parks': [{'id': item['id'], 'name': item['name'], 'commission': item.get('commission')}
                  for item in parks],
    }


def rows_for(decision, catalog, *, space_id, on_date=None):
    """Фрагменты по решению plan и прочитанному справочнику."""
    offices = catalog.get('offices') or {}
    phones = catalog.get('phones') or {}
    promotions = catalog.get('promotions') or ()
    rows = []
    for park in decision['cards']:
        own = [promo for promo in promotions if park['id'] in (promo.get('park_ids') or ())]
        rows.append(_row(len(rows), heading=park['name'], space_id=space_id, park=park,
                         parks=[park],
                         text=card_text(park, offices=offices.get(park['id']),
                                        phones=phones.get(park['id']), promotions=own,
                                        on_date=on_date)))
    if decision['summary']:
        parks = decision['summary']
        everyone = len(parks) == len(catalog.get('parks') or ())
        rows.append(_row(len(rows), heading='Все парки' if everyone else ', '.join(
            park['name'] for park in parks), space_id=space_id, parks=parks,
            text=summary_text(parks, offices=offices, phones=phones, promotions=promotions,
                              extras=decision['extras'],
                              total=len(catalog.get('parks') or ()))))
    return rows


def build_ai_rows(analysis, catalog, *, space_id, answered=(), on_date=None):
    """Фрагменты помощника из уже прочитанного справочника. Без базы — для тестов."""
    decision = plan(analysis, catalog.get('parks') or [], answered=answered)
    if not decision['cards'] and not decision['summary']:
        return []
    return rows_for(decision, catalog, space_id=space_id, on_date=on_date)


# ─────────────────────────────────────────────────────────────────────────────
# Вход из роутов
# ─────────────────────────────────────────────────────────────────────────────

def load_details(cursor, park_ids, *, space_id):
    """Офисы, номера и действующие акции парков — через читающие функции под стражей."""
    return {
        'offices': wiki_offices.offices_by_park(cursor, park_ids, space_id=space_id),
        'phones': wiki_offices.phones_by_park(cursor, park_ids, space_id=space_id),
        'promotions': [promo for promo in wiki_parks.list_promotions(cursor, space_id=space_id)
                       if promo.get('is_running')],
    }


def ai_rows(cursor, ctx, question, *, space_id, search_query=None, answered=(), history=None):
    """Фрагменты «Парков» к вопросу помощника — или [].

    Сначала только список парков (узнать парк в вопросе), офисы, номера и акции —
    одним заходом на каждое и только когда карточке или сводке быть.
    history — прошлые реплики разговора, для продолжений (analyze)."""
    space = directory.directory_space(cursor, ctx, space_id, tabs=(SOURCE_TAB,))
    if space is None:
        return []
    parks = wiki_parks.list_parks(cursor, space_id=space['id'])
    decision = plan(analyze(question, search_query, history), parks, answered=answered)
    shown = decision['cards'] + (decision['summary'] or [])
    if not shown:
        return []
    catalog = dict(load_details(cursor, sorted({park['id'] for park in shown}),
                                space_id=space['id']), parks=parks)
    return rows_for(decision, catalog, space_id=space['id'], on_date=currency.today())
