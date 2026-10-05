# -*- coding: utf-8 -*-
"""«Списки Байги» для ИИ-помощника вики: итоги недели по водителю — к вопросу.

Просьба владельца 05.10.2026: помощник отвечает на «какое место занял на этой
неделе водитель с таким ФИО или номером ВУ, сколько он заработал за неделю и
какой у него приз». Правило доступа названо им же: «если у пользователя имеется
раздел „Списки Байги“ — помощник может его читать», и «кроме Тез КЦ».

Устроено по образцу справочников вики (wiki/directory.py): помощник списки НЕ
индексирует, а получает к вопросу готовые фрагменты с живыми данными раздела.
Индекс и эмбеддинги здесь были бы вредны: в строках ФИО и номера ВУ, неделю
заменяют и удаляют, и копия в индексе пережила бы и замену, и отзыв доступа.

ДОСТУП — ТЕ ЖЕ ДВА ГЕЙТА, ЧТО У РУЧЕК РАЗДЕЛА (baiga/routes.py), в том же
порядке: access.can_open_section, затем QR-подтверждение сессии тому, кого
спрашивает замок. До этого модуля /api/baiga был единственным путём к строкам;
помощник — второй, и без тех же гейтов он стал бы обходом замка. Гейт вики
(wiki/routes.py) этого не заменяет: набор должностей под её замком другой.

«КРОМЕ ТЕЗ КЦ» — помощник пространства вики, выданного отделу «Тез КЦ», списков
не читает никому, в том числе супер-админу: у этой вики свои клиенты и свои
водители, и вопрос про Байгу там задан не по адресу.

ЧТО УХОДИТ МОДЕЛИ. Только строки НАЗВАННЫХ водителей (не больше MAX_PEOPLE):
ФИО как в файле, номер ВУ, зачёт, место, сумма, поездки, приз, город и парк.
Список целиком, чужие строки и ID водителя модели не отдаются. Водителя обязан
назвать сам вопрос (номер ВУ, ID, фамилия) или разговор, который он продолжает, —
«покажи первую десятку» сюда не относится: это выгрузка, и она живёт в разделе.

ЧТО РЕШАЕТ КОД, А НЕ МОДЕЛЬ (тот же вывод, что у всего помощника — см. шапку
wiki/ai/answer.py): о какой неделе спросили и какой список на неё отвечает; кто
из водителей назван; что водителя в списке НЕТ. Последнее — тоже ответ: без
него помощник отвечал бы «в статьях этого нет», хотя ответ лежит в соседнем
разделе.

ГЛАВНЫЙ ВРАГ — ЛИШНЯЯ СТРОКА. Фамилий в неделе восемь сотен, и «Мороз на улице,
рейтинг упадёт?» или «оператор Ахметова заняла первое место» совпадают с
водителями списка одним словом. Поэтому слово-фамилия называет водителя, только
когда вопрос это подтверждает: в нём есть слово «Байга», либо фамилия подана как
ФИО («водитель Жусипов», «Жусипов А.»), либо в вопросе нет посторонних слов.
Разбор проверен состязательно на сотне вопросов операторов — правила ниже
появились из тех, что сработали зря, и из тех, что зря промолчали.

Разбор реплик (analyze) и сборка фрагментов (build_rows) базы не касаются:
SQL — в queries.py, сюда он приходит двумя функциями (find, load).
"""

import re
from datetime import date, timedelta

from . import access, parse, queries

SECTION_TITLE = 'Списки Байги'
SOURCE_KIND = 'baiga'
SOURCE_TAB = 'baiga'

# Ветка поиска помощника «не найдено, а взято» — та же, что у справочников
# (wiki/directory.DIRECTORY_BRANCH): порог близости к таким фрагментам не
# относится, а переспрашивать, когда водитель назван, не о чем.
BRANCH = 4

# id фрагментов отрицательные, как у справочника (у настоящих кусков он из
# BIGSERIAL), но из своего диапазона: в одном ответе встречаются оба.
_CHUNK_BASE = 1000

# Отделы, помощник пространств которых списков не читает («кроме Тез КЦ»).
EXCLUDED_DEPARTMENT_CODES = ('tez',)

# Сколько водителей уходит модели. Однофамильцев в неделе бывает до четырёх
# (замер файла 21–27.09.2026: 832 фамилии на 900 строк); шесть покрывают и
# двух названных водителей с однофамильцами.
MAX_PEOPLE = 6
# Сколько слов вопроса проверяется как фамилия и сколько водителей под них
# читается из базы до отбора.
MAX_NAME_WORDS = 8
CANDIDATE_LIMIT = 300
# Сколько недель назвать, когда спрошенной в разделе нет.
WEEKS_LISTED = 6
# Сколько прошлых вопросов человека помнит разговор о водителе. Модели уходят
# три (store.recent_turns), а разговор длиннее: «какое место» → «сколько
# заработал» → «а на прошлой неделе» → «какой приз» — и на пятом вопросе водитель
# терялся (замер на стенде 05.10.2026). Разговор всё равно обрывает первая же
# реплика на другую тему, так что дюжина — предел памяти, а не её длина.
PRIOR_TURNS = 12

WEEK_DAYS = 7


# ─────────────────────────────────────────────────────────────────────────────
# Доступ
# ─────────────────────────────────────────────────────────────────────────────

def reader_context(cursor, user_id, *, sensitive_access_granted):
    """Контекст доступа человека, которому помощник вправе читать списки, или None.

    Те же два гейта и тот же контекст, что у baiga_route: раздел открыт, и
    сессия подтверждена QR-кодом, если замок его спрашивает. sensitive_access_granted
    — (user_id, cursor=...) -> bool, ключ портала (bot_schedule2).
    """
    ctx = queries.load_access_context(cursor, user_id)
    if not ctx or not access.can_open_section(ctx):
        return None
    if access.requires_sensitive_qr(ctx) and not sensitive_access_granted(user_id, cursor=cursor):
        return None
    return ctx


def space_reads_lists(cursor, space_id):
    """Читает ли списки помощник ЭТОГО пространства вики.

    Нет пространства — нет и помощника (routes_ai.effective_space), читать некому.
    """
    if not space_id:
        return False
    from wiki import structure as wiki_structure

    closed = wiki_structure.space_ids_for_departments(cursor, EXCLUDED_DEPARTMENT_CODES)
    return space_id not in set(closed)


# ─────────────────────────────────────────────────────────────────────────────
# Слова вопроса
# ─────────────────────────────────────────────────────────────────────────────
#
# Всё ниже сверяется со СВЁРНУТЫМ текстом (parse.fold_text: нижний регистр,
# казахские буквы и «ё» — к русским двойникам), поэтому «бәйге» здесь «байге»,
# а «жүлде» — «жулде».
#
# Формы перечислены поимённо, без «слово + что угодно». Причина — фамилии:
# «Байгабулов», «Орынбаев», «Неделько» начинаются со слов этого словаря, и
# шаблон «байг…» объявил бы фамилию водителя словом «Байга» — водитель перестал
# бы находиться, а вопрос о нём стал бы «вопросом про Байгу».

_L = 'a-zа-я0-9'

_W_BAIGA = r'байг(?:а|и|е|у|ой|ою|ам|ами|ах)?|байге(?:де|ден|ге|нин|ни|си|син|мен)|baiga|bayga|baige'
_W_PLACE = (r'мест(?:о|а|е|у|ом|ах)?|позици[а-я]*|рейтинг[а-я]*|занял[а-я]*|топ(?:е|а|у)?'
            r'|орын(?:ды|да|га|нан|ы)?|орн(?:ы|ын|ына|ында)')
_W_PRIZE = (r'приз(?:а|у|ом|е|ы|ов|ами|ах)?|призов[а-я]{2,4}|призер(?:а|у|ом|ы|ов)?|выигр[а-я]*'
            r'|наград(?:а|ы|у|е|ой)?|жулде(?:си|ни|ге|син|лер)?|сыйлык(?:ты)?|сыйлыг(?:ы|ын)'
            r'|утыс(?:ы)?|утты|утып')
_W_EARNED = (r'заработ[а-я]*|набрал[а-я]*|сумм(?:а|ы|у|е|ой)|тапты|тапкан|табыс(?:ы|ын|ка)?'
             r'|жинады|жинаган')
_W_TRIPS = r'поезд(?:ка|ки|ку|ок|ках|ками)'
_W_WEEK = r'недел(?:я|и|е|ю|ей|ь|ям|ях|ями)|апта(?:да|дагы|нын|га|сы|сында|дан)?'
_W_LIST = (r'список|списк(?:а|е|у|ом|и|ов|ах)|зачет(?:а|е|у|ом|ы|ов|ах)?'
           r'|итог(?:и|ов|ам|ах|е)?|результат[а-я]*')
# Город и парк водителя — тоже из его строки: «а в каком он парке?».
_W_WHERE = r'город(?:а|е|у|ом)?|парк(?:а|е|у|ом|и)?|таксопарк(?:а|е|у|ом|и)?'
# Кто и что — не тема, но и не фамилия.
_W_NOUNS = (r'водител[а-я]*|жургизуши[а-я]*|фио|фамили[а-я]*|имя|имени|удостоверени[а-я]*'
            r'|номер(?:а|у|ом|е|ов)?|ву|айди|id|тенге|тг|kzt|раздел(?:а|е|у)?|данны[а-я]{1,2}'
            r'|информаци[а-я]*')


def _bounded(pattern):
    return re.compile(r'(?<![%s])(?:%s)(?![%s])' % (_L, pattern, _L))


# Тема вопроса — трёх степеней, и чем слабее слово, тем строже спрос с того,
# как назван водитель (accept):
#   сильная  — сама «Байга»: вопрос про акцию, водителя называет любое слово;
#   средняя  — место, приз, заработок: бывает и про другое («место подачи»);
#   слабая   — неделя, поездки, список, итоги, город, парк: обычные слова вики.
_STRONG = _bounded(_W_BAIGA)
_MEDIUM = _bounded('|'.join((_W_PLACE, _W_PRIZE, _W_EARNED)))
_WEAK = _bounded('|'.join((_W_TRIPS, _W_WEEK, _W_LIST, _W_WHERE)))
_VOCABULARY = re.compile(r'^(?:%s)$' % '|'.join(
    (_W_BAIGA, _W_PLACE, _W_PRIZE, _W_EARNED, _W_TRIPS, _W_WEEK, _W_LIST, _W_WHERE, _W_NOUNS)))
# Слово, после которого идёт ФИО: «водитель Жусипов», «ФИО Жусипов А.».
_PERSON_MARK = re.compile(r'^(?:водител[а-я]*|жургизуши[а-я]*|фио|фамили[а-я]*)$')

# Служебные слова — в том написании, как их набирают (нижний регистр, «ё» как
# «е»), ДО свёртки казахских букв. Свёртка здесь навредила бы: казахское «кім»
# («кто») после неё совпадает с фамилией «Ким».
_STOP = frozenset((
    'а и в во на по за из от до у к ко с со о об при для про под над без через между '
    'не ни да нет ли же бы то вот уж уже еще только тоже также или но что чтобы как так там тут '
    'здесь где куда когда кто кого кому кем чей чья чье какой какая какое какие какого какому '
    'каком какую каким сколько скольки почему зачем '
    'он она оно они его ее ему ей им их ним нем нему него нее ней них я мне меня мой моя мое мои '
    'мы нам нас нами наш вы вам вас вами ваш ты тебе тебя тобой ими ними мной себя себе собой '
    'тому этому этим тем '
    'этот эта это эти этой этом эту этого этих тот та те той том ту того такой такая такое таким '
    'такого свой своя свое был была было были есть будет будут стал стала '
    'очень сейчас сегодня вчера завтра теперь потом пока тогда '
    'пожалуйста плиз привет здравствуйте добрый день утро вечер спасибо '
    'ну ок окей хорошо ладно понял поняла понятно ясно точно итого вообще денег деньги '
    'подскажи подскажите скажи скажите покажи покажите найди найдите найти проверь проверьте '
    'посмотри посмотрите узнать узнай уточни уточните нужно надо можно хочу хотел интересует '
    'получил получила получит получает попал попала вошел вошла вышел вышла вышло занимает '
    'сделал сделала совершил совершила выполнил выполнила дали выдали выплатили положен положена '
    'говорит спрашивает просит уточняет интересуется звонит пишет утверждает хочет сказал сказала '
    'прошлой прошлая прошлую прошлом текущей текущая текущую последней последняя последнюю '
    'позапрошлой позапрошлая позапрошлую предыдущей предыдущая предыдущую '
    'каждый весь вся все всего всех '
    # казахские — с буквами и так, как их набирают без казахской раскладки
    'және мен бен пен үшін ушин бұл бул осы сол ол ал не кім кімге кімнің кімді кімнен кімде '
    'кимге кимнин кимди кимнен кимде қай кай қандай кандай қанша канша '
    'қалай калай нешінші нешинши неше бар жоқ жок ма ме ба бе па пе еді еди екен деп туралы '
    'бойынша алды алған алган өткен откен айтыңызшы айтынызшы айтшы көрсет корсет сәлем салем '
    'рақмет рахмет ақша акша'
).split())

# Слова вопроса об УСЛОВИЯХ акции, а не о водителе: «а когда приз?», «а сколько
# всего призовых мест?», «а какие призы есть?». Такой вопрос после разговора о
# водителе его строку за собой не тянет (continues).
_GENERAL = frozenset((
    'когда где как почему зачем кто всего такое вообще какие бывают условия условиях можно '
    'нужно надо призов призы призовых призеров победителей участников '
    'қашан кашан қалай калай неге қайда кайда'
).split())

# Слова, которые фамилией водителя не бывают: сервисы, тарифы, города. Сверяются
# с точностью до падежа (same_surname): «Яндекса», «в Астане», «Караганды».
# Нужны правилу «незнакомая фамилия» — знакомую узнаёт база — и разбору
# посторонних слов. К ним добавляются слова названий зачётов загруженных недель.
_NOT_NAMES = frozenset((
    'яндекс yandex про pro go такси taxi uber убер индрайв indrive флит fleet crm срм '
    'тоо ип wolt вольт glovo глово курьер комфорт эконом бизнес доставка тез tez лимонопад '
    'казахстан алматы астана шымкент караганда актобе тараз павлодар оскемен каменогорск '
    'семей атырау костанай кызылорда уральск орал петропавловск актау темиртау туркестан '
    'кокшетау талдыкорган экибастуз рудный жанаозен жезказган балхаш кентау каскелен конаев '
    'капшагай сатпаев степногорск риддер щучинск талгар есик'
).split())


# ─────────────────────────────────────────────────────────────────────────────
# Номер ВУ и ID водителя в тексте вопроса
# ─────────────────────────────────────────────────────────────────────────────

_ALPHA = 'A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі'

_HEX_ID = re.compile(r'(?<![0-9A-Za-z])[0-9a-fA-F]{32}(?![0-9A-Za-z])')

# «ВУ AB123456», «в/у: 1234567890», «удостоверение № AB 123456», «ВУ AB 123 456».
_LICENSE_MARKED = re.compile(
    r'(?<![%(a)s0-9])(?:в\s?/\s?у|ву|удостоверени[а-я]*|прав(?:а|ами|ах))(?![%(a)s0-9])'
    r'(?:\s*(?:номер[а-я]*|№|#|:|-|—))*\s*'
    r'(?P<value>[A-Za-zА-Яа-яЁё]{0,4}\s?(?:\d[0-9A-Za-zА-Яа-яЁё]{4,}|\d{1,4}(?:\s\d{2,4}){1,3}(?!\d)))'
    % {'a': _ALPHA}, re.I)

# Номер как в файле: две–четыре буквы и шесть-семь цифр, слитно или через
# пробел («AB 123456»). Замер файла 21–27.09.2026: 889 из 900 — «AA999999».
_LICENSE_PLAIN = re.compile(
    r'(?<![%(a)s0-9])(?P<letters>[A-Za-zА-Яа-яЁё]{2,4})(?P<gap>[ \-]?)(?P<digits>\d{6,7})(?![%(a)s0-9])'
    % {'a': _ALPHA})

# Нестандартные номера (в том же файле — 11): буквы с цифрами слитно и номера,
# записанные числом. Находит только то, что есть в списке, — «нет в списке» про
# такое значение не говорим: это может быть телефон или сумма.
_KEY_MIXED = re.compile(r'(?<![%(a)s0-9])(?=[%(a)s]*\d)(?=\d*[%(a)s])[%(a)s0-9]{6,40}(?![%(a)s0-9])'
                        % {'a': _ALPHA})
_KEY_DIGITS = re.compile(r'(?<![\d+])\d{8,12}(?!\d)')

# Слова перед цифрами, которые буквами номера не являются: «ID 1234567».
_KEY_MARK_WORDS = frozenset(('ву', 'id', 'ид'))
_STANDARD_KEY = re.compile(r'^[A-Z]{2,4}\d{6,7}$')


# ─────────────────────────────────────────────────────────────────────────────
# Неделя в тексте вопроса
# ─────────────────────────────────────────────────────────────────────────────

_MONTH_STEMS = (
    ('январ', 1), ('феврал', 2), ('март', 3), ('апрел', 4), ('ма[йя]', 5), ('июн', 6),
    ('июл', 7), ('август', 8), ('сентябр', 9), ('октябр', 10), ('ноябр', 11), ('декабр', 12),
    # казахские, уже свёрнутые: қаңтар, ақпан, наурыз, сәуір, мамыр, маусым,
    # шілде, тамыз, қыркүйек, қазан, қараша, желтоқсан
    ('кантар', 1), ('акпан', 2), ('наурыз', 3), ('сауир', 4), ('мамыр', 5), ('маусым', 6),
    ('шилде', 7), ('тамыз', 8), ('кыркуйек', 9), ('казан', 10), ('караша', 11), ('желтоксан', 12),
)
_MONTHS = '|'.join(stem for stem, _number in _MONTH_STEMS)

_DATE_FULL = re.compile(r'(?<![\d./])(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?![./]?\d)')
# Без года — только «21.09» и «21/09»: месяц двумя цифрами, иначе «1.5 ставки»
# стало бы первым мая.
_DATE_SHORT = re.compile(r'(?<![\d.,/])(\d{1,2})[./](\d{2})(?![./]?\d|\s*%)')
_DATE_TEXT = re.compile(r'(?<![\d.])(\d{1,2})\s+(%s)[а-я]*(?:\s+(\d{4}))?(?!\d)' % _MONTHS)
# Месяц без числа («а в сентябре?», «а в мае?») неделю не называет — но и
# продолжать разговор прежней неделей после него нельзя.
_MONTH_ALONE = _bounded(r'(?:%s|мае)[а-я]{0,2}' % _MONTHS)

# Номер недели назван твёрдо: «39-я неделя», «неделя № 39», «39-шы апта».
_WEEK_NUMBER = re.compile(
    r'(?<![\d.])(\d{1,2})\s*-?\s*(?:я|ю|й|ой|ую|ей)\s+недел[а-я]*'
    r'|недел(?:я|и|е|ю)\s*(?:№|#|номер[а-я]*)\s*(\d{1,2})(?![\d.])'
    r'|(?<![\d.])(\d{1,2})\s*-?\s*(?:ши|шы|инши|ыншы|нши|ншы)\s+апта[а-я]*')
# …и нетвёрдо: «на 39 неделе», «за 39 неделю», «неделя 39», «39 аптада». Так же
# пишут срок и место («3 неделя подряд», «неделя 3 место»), поэтому число
# считается номером, только если такая неделя загружена (resolve_week). «За 1
# неделю» — срок всегда: первая неделя года лежит в разделе весь год, и «сколько
# заработал за 1 неделю» иначе отвечало бы январём.
_WEEK_NUMBER_WEAK = re.compile(
    r'(?<![\d.])(?!(?<=за )1(?!\d))(\d{1,2})\s+недел(?:я|е|ю)(?![а-я])'
    r'|(?<![а-я])неделя\s+(\d{1,2})(?![\d.]|\s*мест)'
    r'|(?<![\d.])(\d{1,2})\s*-?\s*апта(?:да|дагы|нын|сында)(?![а-я])')

_TO = r'(?:[\s-]+то)?'            # «на этой-то неделе»
_WEEK_BEFORE_LAST = _bounded(r'позапрошл[а-я]+\s+недел[а-я]*|(?:две|2|пару)\s+недел[а-я]*\s+назад')
_WEEK_LAST = _bounded(r'(?:прошл[а-я]+|предыдущ[а-я]+|минувш[а-я]+)%s\s+недел[а-я]*'
                      r'|неделю\s+назад|откен\s+апта[а-я]*|алдынгы\s+апта[а-я]*' % _TO)
_WEEK_CURRENT = _bounded(r'(?:эт(?:а|у|ой|и)|текущ[а-я]+|нынешн[а-я]+)%s\s+недел[а-я]*'
                         r'|(?:осы|бул)\s+апта[а-я]*' % _TO)
# То же без слова «неделя» — так спрашивают только посреди разговора о неделях:
# «а на прошлой?», «а за позапрошлую?».
_BARE_BEFORE_LAST = _bounded(r'позапрошл(?:ой|ую|ая)')
_BARE_LAST = _bounded(r'прошл(?:ой|ую|ая)|предыдущ(?:ей|ую|ая)')
_BARE_CURRENT = _bounded(r'текущ(?:ей|ую|ая)')
# Неделя, отсчитанная от недели разговора: «а неделей раньше?».
_WEEK_SHIFT = _bounded(r'(?:неделей|на\s+неделю)\s+раньше|(?:за\s+)?неделю\s+до\s+(?:этого|того)')
# Время названо, но какое — не понять: прежней неделей такой вопрос не продолжают.
_PERIOD_UNKNOWN = _bounded(r'раньше|ранее|прежде|давно')

_WORD = re.compile(r'[^\W\d_]+', re.UNICODE)
_FREE_NUMBER = re.compile(r'\d')

# Дата без года дальше этого срока вперёд — прошлогодняя: в октябре «28.12» —
# декабрь прошлого года, а «13.10» — на этой неделе.
_FUTURE_DAYS = 60


def _make_date(day, month, year):
    year = int(year)
    if year < 100:
        year += 2000
    try:
        return date(year, int(month), int(day))
    except ValueError:
        return None


def _nearest(day, month, today):
    for year in (today.year, today.year - 1):
        value = _make_date(day, month, year)
        if value and value <= today + timedelta(days=_FUTURE_DAYS):
            return value
    return None


def _monday(day):
    return day - timedelta(days=day.weekday())


def _mask(masked, start, end):
    masked[start:end] = ' ' * (end - start)


def _week_of(day, kind):
    start = _monday(day)
    return {'kind': kind, 'day': day, 'start': start, 'end': start + timedelta(days=WEEK_DAYS - 1)}


def _parse_week(masked, today):
    """Что реплика говорит о неделе. Разобранное гасится в masked: цифры даты не
    должны читаться ни номером ВУ, ни «числом в вопросе».

        asked    неделя названа: дата, твёрдый номер, «на этой/прошлой неделе»
        number   число при слове «неделя» — номер, если такая неделя загружена
        bare     «прошлой», «позапрошлую» без слова «неделя»
        shift    на сколько недель раньше недели разговора
        unknown  время названо, а какое — не разобрать («в сентябре», «раньше»)
    """
    text = ''.join(masked)
    found = []
    for match in _DATE_FULL.finditer(text):
        found.append((match.start(), match.end(), _make_date(*match.groups())))
    for start, end, _value in found:
        _mask(masked, start, end)
    text = ''.join(masked)
    for match in _DATE_TEXT.finditer(text):
        month = next(number for stem, number in _MONTH_STEMS if re.match(stem, match.group(2)))
        value = (_make_date(match.group(1), month, match.group(3)) if match.group(3)
                 else _nearest(match.group(1), month, today))
        found.append((match.start(), match.end(), value))
        _mask(masked, match.start(), match.end())
    text = ''.join(masked)
    for match in _DATE_SHORT.finditer(text):
        value = _nearest(match.group(1), match.group(2), today)
        if value:
            found.append((match.start(), match.end(), value))
            _mask(masked, match.start(), match.end())
    dated = sorted((start, value) for start, _end, value in found if value)

    def number_of(pattern):
        match = pattern.search(''.join(masked))
        if not match:
            return None
        value = int(next(group for group in match.groups() if group))
        if not 1 <= value <= 53:
            return None
        _mask(masked, match.start(), match.end())
        return value

    firm = number_of(_WEEK_NUMBER)
    weak = number_of(_WEEK_NUMBER_WEAK) if firm is None else None

    def first(patterns):
        for pattern, value in patterns:
            match = pattern.search(''.join(masked))
            if match:
                _mask(masked, match.start(), match.end())
                return value
        return None

    relative = first(((_WEEK_BEFORE_LAST, 2), (_WEEK_LAST, 1), (_WEEK_CURRENT, 0)))
    shift = first(((_WEEK_SHIFT, 1),)) or 0
    bare = first(((_BARE_BEFORE_LAST, 2), (_BARE_LAST, 1), (_BARE_CURRENT, 0)))
    rest = ''.join(masked)
    unknown = bool(_PERIOD_UNKNOWN.search(rest) or _MONTH_ALONE.search(rest))

    kinds = ('current', 'previous', 'before_previous')
    # Названная дата точнее номера, номер — точнее «прошлой»: «на прошлой
    # неделе, 21–27.09» — это про 21.09.
    if dated:
        asked = _week_of(dated[0][1], 'date')
    elif firm is not None:
        asked = {'kind': 'number', 'number': firm}
    elif relative is not None:
        asked = _week_of(today - timedelta(days=WEEK_DAYS * relative), kinds[relative])
    else:
        asked = None
    return {
        'asked': asked,
        'number': weak if asked is None else None,
        'bare': (_week_of(today - timedelta(days=WEEK_DAYS * bare), kinds[bare])
                 if bare is not None and asked is None else None),
        'shift': shift if asked is None else 0,
        'unknown': unknown and asked is None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Разбор реплики
# ─────────────────────────────────────────────────────────────────────────────

def _license(raw, pos, *, explicit):
    key = parse.license_key(raw)
    # label — как назвать номер в ответе «нет в списке»: обычный номер — в том
    # виде, в каком он записан в файле (латиницей, без пробела).
    label = key if _STANDARD_KEY.match(key) else parse.clean_text(raw).upper()
    return {'key': key, 'label': label, 'pos': pos, 'explicit': explicit}


def _parse_keys(text, masked):
    """Номера ВУ и ID водителя из текста: ([номера], [ID]). Съеденное гасится."""
    licenses, drivers = [], []

    def taken(start, end):
        return not ''.join(masked[start:end]).strip()

    for match in _HEX_ID.finditer(text):
        drivers.append({'key': match.group(0).lower(), 'pos': match.start()})
        _mask(masked, match.start(), match.end())
    for match in _LICENSE_MARKED.finditer(text):
        start, end = match.span('value')
        if taken(start, end):
            continue
        licenses.append(_license(match.group('value'), start, explicit=True))
        _mask(masked, match.start(), match.end())
    for match in _LICENSE_PLAIN.finditer(text):
        if taken(match.start(), match.end()):
            continue
        letters, gap = match.group('letters'), match.group('gap')
        if gap:
            typed = letters.lower()
            if typed in _KEY_MARK_WORDS or typed in _STOP or len(letters) != 2:
                continue                         # «ID 1234567», «НА 500000», «за 150000»
            if not (letters.isascii() or letters.isupper()):
                continue                         # «ип 123456» строчными — слово и число
        found = _license(letters + match.group('digits'), match.start(), explicit=False)
        # «Нет в списке» говорим только про номер обычного вида — и, если он
        # набран через пробел, только латиницей: «ИП 123456», «СВ 123456» —
        # слово и число, а не серия и номер.
        found['explicit'] = bool(_STANDARD_KEY.match(found['key'])
                                 and (not gap or letters.isascii()))
        licenses.append(found)
        _mask(masked, match.start(), match.end())
    for pattern in (_KEY_MIXED, _KEY_DIGITS):
        for match in pattern.finditer(text):
            if taken(match.start(), match.end()):
                continue
            licenses.append(_license(match.group(0), match.start(), explicit=False))
            # Нестандартный ID тоже бывает таким (файл их принимает).
            drivers.append({'key': parse.driver_key(match.group(0)), 'pos': match.start()})
            _mask(masked, match.start(), match.end())
    return licenses, drivers


def analyze(text, *, today):
    """Разбор одной реплики — без базы.

    tokens — буквенные слова по порядку: kind 'stop' (служебное или из словаря
    темы), 'initial' («А.» — заглавная буква с точкой) и 'name' (всё остальное:
    фамилией его делает только совпадение со списком).
    """
    text = parse.clean_text(text)
    folded = parse.fold_text(text)
    if len(folded) != len(text):        # редкая буква меняет длину при lower()
        text = folded
    masked = list(folded)
    licenses, drivers = _parse_keys(text, masked)
    week = _parse_week(masked, today)

    rest = ''.join(masked)
    spans = [match.span() for match in _WORD.finditer(rest)]
    words = [text[start:end] for start, end in spans if end - start > 1]
    # Заглавная буква говорит «это имя», только пока ею пишут имена: вопрос,
    # набранный целиком заглавными, о своих словах так ничего не сообщает.
    shouting = len(words) >= 3 and all(word.isupper() for word in words)
    starts = {start for start, _end in spans}
    tokens = []
    for start, end in spans:
        original = text[start:end]
        typed = original.lower().replace('ё', 'е')
        fold = folded[start:end]
        stop = typed in _STOP
        if len(fold) == 1:
            # Инициал — заглавная буква с точкой. «т. е.», «д. 5», «г. Алматы» —
            # сокращения, и пишут их строчными.
            kind = 'initial' if (text[end:end + 1] == '.' and original.isupper()) else 'stop'
        elif stop or _VOCABULARY.match(fold):
            kind = 'stop'
        else:
            kind = 'name'
        tokens.append({'kind': kind, 'fold': fold, 'text': original, 'typed': typed, 'pos': start,
                       'capital': original[:1].isupper() and not shouting,
                       'mark': bool(_PERSON_MARK.match(fold)),
                       'stopword': stop and len(fold) > 1,
                       # «Ким-Иванов»: следующее слово приклеено дефисом.
                       'hyphen': text[end:end + 1] == '-' and end + 1 in starts})
    # Фамилии «Ли», «Нам», «Мен», «Им» совпадают со служебными словами. Именем
    # такое слово делает подача: заглавная буква и инициал следом («Ли А.») или
    # слово «водитель» перед ним.
    for index, token in enumerate(tokens):
        if token['stopword'] and token['capital'] and (
                (index + 1 < len(tokens) and tokens[index + 1]['kind'] == 'initial')
                or (index > 0 and tokens[index - 1]['mark'])):
            token['kind'] = 'name'
    return {
        'strong': bool(_STRONG.search(folded)),
        'medium': bool(_MEDIUM.search(folded)),
        'weak': bool(_WEAK.search(folded)) or week['asked'] is not None,
        'general': any(token['typed'] in _GENERAL for token in tokens),
        'week': week,
        'licenses': licenses,
        'drivers': drivers,
        'tokens': tokens,
        # Число, оставшееся после дат, недель и номеров: «за 3 место» — признак
        # вопроса об условиях акции, а не о водителе.
        'numbers': bool(_FREE_NUMBER.search(rest)),
    }


def topic(analysis):
    """Степень темы реплики: 3 — «Байга», 2 — место/приз/заработок, 1 — слабое
    слово (неделя, поездки, список, город, парк), 0 — ничего."""
    if analysis['strong']:
        return 3
    if analysis['medium']:
        return 2
    return 1 if analysis['weak'] else 0


def _name_indexes(analysis):
    return [index for index, token in enumerate(analysis['tokens']) if token['kind'] == 'name']


def _names_someone(analysis):
    """Есть ли в реплике хоть что-то, что может оказаться водителем."""
    return bool(analysis['licenses'] or analysis['drivers'] or _name_indexes(analysis))


def _may_name(analysis):
    """Может ли реплика начать разговор о водителе: в ней есть слово темы и
    номер или слово, которое может оказаться фамилией."""
    return bool(topic(analysis)) and _names_someone(analysis)


def _worth_asking(own, priors):
    """Стоит ли идти в базу — решается по тексту, до всякого доступа.

    Большинству вопросов вики списки не нужны, и база для них не тронута. Условие
    — необходимое для conversation: водителя называет либо сам вопрос, либо
    реплика разговора, который он продолжает. Достаточным его делает база —
    слово оказывается фамилией из списка или нет, — а судить о слове по
    заглавной букве здесь нельзя: «сколько поездок у жусипова за неделю» пишут
    и так.
    """
    return _may_name(own) or any(_may_name(turn) for turn in priors)


# ─────────────────────────────────────────────────────────────────────────────
# Фамилия
# ─────────────────────────────────────────────────────────────────────────────

# Падежные окончания фамилии: «у Жусипова», «Ахметовым», «Жүсіповтың».
_TAILS = frozenset((
    'а у е ы и я ю ой ом ым ем ою ей '
    'тын дын нын тин дин нин ка га ке ге ты ды ны ти ди ни та да те де тан дан нан тен ден нен'
).split())
# Женская фамилия в списке («Иванова») и её формы в вопросе: «Ивановой»,
# «Иванову»; без окончания — мужская форма той же фамилии.
_TAILS_FEMININE = frozenset(('', 'ой', 'ою', 'у', 'ы', 'е'))
# Фамилия, у которой при склонении меняется последняя буква: окончание фамилии
# → её окончания в вопросе. «Ковалевский» — «у Ковалевского»; «Жумабай» — «у
# Жумабая», «Коваль» — «Ковалю» (фамилии на «-бай/-ай/-ей» массовые).
_TAILS_REPLACED = {
    'ий': ('ого', 'ому', 'им', 'ом', 'ем'),
    'ый': ('ого', 'ому', 'ым', 'ом'),
    'ой': ('ого', 'ому', 'ым', 'ом'),
    'ая': ('ой', 'ую'),
    'й': ('я', 'ю', 'ем', 'е'),
    'ь': ('я', 'ю', 'ем', 'е'),
}


def same_surname(word, surname):
    """Слово вопроса — эта фамилия, с точностью до падежа.

    Не «начинается с»: «Ахмет» и «Ахметов», «Ким» и «Кимбаев» — разные люди.
    Фамилии из двух букв («Ли») сверяются только целиком.
    """
    if word == surname:
        return True
    if len(word) < 3 or len(surname) < 3:
        return False
    common = 0
    for left, right in zip(word, surname):
        if left != right:
            break
        common += 1
    tail, rest = word[common:], surname[common:]
    if not rest:
        return tail in _TAILS
    if rest == 'а' and common >= 3 and tail in _TAILS_FEMININE:
        return True
    for ending, tails in _TAILS_REPLACED.items():
        stem = surname[:-len(ending)]
        if (surname.endswith(ending) and len(stem) >= 3 and word.startswith(stem)
                and word[len(stem):] in tails):
            return True
    return False


def _surname_guesses(word):
    """Фамилии, которыми может оказаться слово вопроса: оно само и оно же без
    падежного окончания. То же правило, что same_surname, развёрнутое в список:
    по нему база отдаёт только однофамильцев, а не всех на «Ахмет…»."""
    guesses = {word}
    if len(word) >= 3:
        guesses.update(word[:cut] for cut in range(3, len(word)) if word[cut:] in _TAILS)
        guesses.update(word[:cut] + 'а' for cut in range(3, len(word) + 1)
                       if word[cut:] in _TAILS_FEMININE)
        guesses.update(word[:len(word) - len(tail)] + ending
                       for ending, tails in _TAILS_REPLACED.items() for tail in tails
                       if word.endswith(tail) and len(word) - len(tail) >= 3)
    return guesses


_PLAIN_WORD = re.compile(r'^[a-zа-я]+$')


def names_regex(words):
    """Регулярное выражение «фамилия — одна из этих» для search_text или None.

    search_text начинается с ФИО, а ФИО — с фамилии; у двойной («ким-иванов»)
    подходит любая часть. Слова — только из букв свёрнутого алфавита, поэтому
    экранировать в выражении нечего: всё прочее в него просто не попадает.
    """
    guesses = sorted({guess for word in words if _PLAIN_WORD.match(word)
                      for guess in _surname_guesses(word)})
    if not guesses:
        return None
    return r'^(?:[a-zа-я]+-)*(?:%s)(?:-[a-zа-я]+)*(?:[^a-zа-я-]|$)' % '|'.join(guesses)


def _surname_forms(name):
    """Фамилия из ФИО списка — целиком и по частям двойной («Ким-Иванов»)."""
    head = parse.fold_text(name).split(' ', 1)[0]
    parts = [part for part in re.split(r'[^a-zа-я]+', head) if part]
    return set(parts) | ({''.join(parts)} if len(parts) > 1 else set())


def _initials(name):
    folded = parse.fold_text(name)
    rest = folded.split(' ', 1)[1] if ' ' in folded else ''
    return [word[0] for word in re.findall(r'[a-zа-я]+', rest)]


def _typed_initials(tokens, index):
    """Инициалы, набранные сразу за фамилией: «Жусипов А.Б.» → ['а', 'б']."""
    found = []
    for token in tokens[index + 1:index + 3]:
        if token['kind'] != 'initial':
            break
        found.append(token['fold'])
    return found


def _words(value):
    return set(re.findall(r'[a-zа-я]+', parse.fold_text(value)))


def _is_one_of(word, words):
    """Слово — одно из этих, с точностью до падежа («Астане» — «Астана»)."""
    return any(same_surname(word, other) for other in words)


# ─────────────────────────────────────────────────────────────────────────────
# Кого назвали
# ─────────────────────────────────────────────────────────────────────────────

def _explicit_names(analysis, names):
    """Слова, которые вопрос сам подаёт как ФИО, — [(индексы слов, уверенно ли)].

    Нужны одному: сказать «такого водителя в списке нет». Знакомую фамилию
    узнаёт база, а про незнакомую код знает только то, как её подали, — и
    подаёт её заглавная буква:
      * уверенно — с инициалами («Петров И.») и двумя словами после «водитель»,
        «ФИО», «фамилия» («водитель Петров Иван»);
      * неуверенно — одно заглавное слово после «водитель» или посреди вопроса
        («какое место занял Петров»): так пишут и город, и сервис, и акцию,
        поэтому фрагмент об этом честно оговорён.
    Первое слово вопроса с заглавной пишут всегда, а строчное слово после
    «водитель» — чаще глагол («водитель говорит»): фамилией их не объявляем.
    """
    tokens = analysis['tokens']
    found, used = [], set()
    for index, token in enumerate(tokens):
        if not token['mark']:
            continue
        run = []
        for follower in range(index + 1, min(index + 4, len(tokens))):
            if follower not in names or not tokens[follower]['capital']:
                break
            run.append(follower)
        if run:
            found.append((run, len(run) > 1 or bool(_typed_initials(tokens, run[-1]))))
            used.update(run)
    for index in names:
        if index not in used and tokens[index]['capital'] and _typed_initials(tokens, index):
            found.append(([index], True))
            used.add(index)
    loose = [index for index in names if index not in used]
    if (loose and len(loose) <= 3 and loose[0] > 0 and not analysis['numbers']
            and all(tokens[index]['capital'] for index in loose)):
        found.append((loose, False))
    return found


def identify(analysis, find, *, places=frozenset()):
    """Кого назвали в реплике — без суждения, принять ли это (accept).

    find(driver_keys=…, license_keys=…) и find(name_regex=…) — водители из
    списков (queries.assistant_candidates). Возвращает:
        hits     [{'pos', 'people', 'key', 'marked'}] — найденные, по месту в тексте;
                 key — назван номером ВУ или ID; marked — фамилия подана как ФИО
        missing  [{'pos', 'kind': 'license' | 'name', 'label', 'sure'}]
        strays   слова реплики, не объяснённые ни темой, ни ФИО, ни городом или
                 парком названного водителя
    """
    tokens = analysis['tokens']
    hits, missing = [], []

    license_keys = sorted({item['key'] for item in analysis['licenses'] if item['key']})
    driver_keys = sorted({item['key'] for item in analysis['drivers'] if item['key']})
    keyed = find(driver_keys=driver_keys, license_keys=license_keys) if (license_keys or driver_keys) else []
    for item in analysis['licenses']:
        people = [row for row in keyed if row['license_key'] and row['license_key'] == item['key']]
        if people:
            hits.append({'pos': item['pos'], 'people': people, 'key': True, 'marked': True})
        elif item['explicit']:
            missing.append({'pos': item['pos'], 'kind': 'license', 'label': item['label'], 'sure': True})
    for item in analysis['drivers']:
        people = [row for row in keyed if row['driver_key'] == item['key']]
        if people:
            hits.append({'pos': item['pos'], 'people': people, 'key': True, 'marked': True})

    # Города, сервисы и тарифы фамилией не бывают — в любом падеже.
    plain = [index for index in _name_indexes(analysis)
             if not _is_one_of(tokens[index]['fold'], places)]
    # Заглавные — первыми: в длинном вопросе фамилия не должна выпасть за предел.
    names = sorted(plain, key=lambda index: (not tokens[index]['capital'], index))[:MAX_NAME_WORDS]
    matched, second_halves = set(), set()
    pattern = names_regex([tokens[index]['fold'] for index in names])
    if pattern:
        named = [dict(row, surnames=_surname_forms(row['driver_name']))
                 for row in find(name_regex=pattern)]
        for index in sorted(names):
            if index in second_halves:
                continue
            word = tokens[index]['fold']
            # Двойная фамилия, набранная через дефис, — одна фамилия: «Ким-Иванов»
            # не должен находить всех Кимов и всех Ивановых — ни когда такой
            # водитель в списке есть, ни когда его нет.
            pair = index + 1 if (tokens[index]['hyphen'] and index + 1 in names) else None
            if pair is not None:
                second_halves.add(pair)
                word += tokens[pair]['fold']
            people = [row for row in named
                      if any(same_surname(word, surname) for surname in row['surnames'])]
            last = pair if pair is not None else index
            typed = _typed_initials(tokens, last)
            if typed and people:
                # Инициалы из вопроса сужают однофамильцев — но только если под
                # них кто-то подходит: опечатка в инициале не прячет водителя.
                fitting = [row for row in people
                           if all(position >= len(_initials(row['driver_name']))
                                  or _initials(row['driver_name'])[position] == letter
                                  for position, letter in enumerate(typed))]
                people = fitting or people
            if people:
                matched.update((index, last))
                hits.append({'pos': tokens[index]['pos'], 'people': people, 'key': False,
                             'token': last,
                             'marked': bool(typed) or (index > 0 and tokens[index - 1]['mark'])})

    # Имя и отчество стоят вплотную к фамилии: «Жусипов Алмас Бекович», «Алмас
    # Жусипов». Фамилией их не делает ничто, но и посторонними словами они не
    # являются — если сходятся с инициалами водителя из списка: «шины менял»
    # совпадает с фамилией Шин, но «менял» не имя водителя Шин О.
    claimed = set(matched)
    for hit in hits:
        index = hit.get('token')
        if index is None:
            continue
        initials = [_initials(row['driver_name']) for row in hit['people']]

        def fits(near, place):
            token = tokens[near] if 0 <= near < len(tokens) else None
            if token is None or token['kind'] not in ('name', 'initial'):
                return False
            known = [letters[place] for letters in initials if len(letters) > place]
            if known:
                return token['fold'][0] in known
            return token['kind'] == 'initial' or token['capital']

        if fits(index + 1, 0):
            claimed.add(index + 1)
            if fits(index + 2, 1):
                claimed.add(index + 2)
        elif fits(index - 1, 0) and tokens[index - 1]['kind'] == 'name':
            claimed.add(index - 1)
    # Город, парк и зачёт названного водителя — тоже не посторонние: «Мороз из
    # парка „Глобал“ какое место».
    own_words = set()
    for hit in hits:
        for row in hit['people']:
            own_words |= _words(row.get('city')) | _words(row.get('park')) | _words(row.get('zachet'))
    free = [index for index in plain
            if index not in claimed and not _is_one_of(tokens[index]['fold'], own_words)]
    for run, sure in _explicit_names(analysis, free):
        claimed.update(run)
        # Как человек набрал: «Петров Иван», «Ким-Заде».
        label = ''.join(tokens[index]['text'] + ('-' if tokens[index]['hyphen'] else ' ')
                        for index in run).rstrip(' -')
        missing.append({'pos': tokens[run[0]]['pos'], 'kind': 'name', 'sure': sure, 'label': label})
    strays = [index for index in _name_indexes(analysis)
              if index not in claimed
              and not _is_one_of(tokens[index]['fold'], places)
              and not _is_one_of(tokens[index]['fold'], own_words)]

    hits.sort(key=lambda item: item['pos'])
    missing.sort(key=lambda item: item['pos'])
    return {'hits': hits, 'missing': missing, 'strays': strays}


def accept(analysis, found, *, conversation=False):
    """Вправе ли реплика назвать водителя: {'hits', 'missing'} или None.

    Слово, совпавшее с фамилией из списка, само по себе ничего не значит:
    «оператор Ахметова заняла первое место в рейтинге», «ИП Ахметов сумма
    налога», «шины менял, сумма» совпадают с водителями Ахметовым и Шином.
    Чем слабее тема реплики, тем чище должна быть подача:

        «Байга» в реплике         водителя называет любое совпавшее слово
        место, приз, заработок    номер ВУ; фамилия, поданная как ФИО («водитель
                                  Жусипов», «Жусипов А.»); либо реплика без
                                  посторонних слов («Жусипов какое место занял»)
        неделя, поездки, список,  только реплика без посторонних слов
        разговор о Байге          («а Петров?», «итоги водителя ВУ AB123456»)

    conversation — реплика продолжает разговор о водителе списка: тогда назвать
    другого водителя можно и без слова темы, но числа и посторонние слова в ней
    уже означают другой вопрос.
    """
    level = topic(analysis)
    if not level and not conversation:
        return None
    clean = not found['strays'] and (level > 0 or not analysis['numbers'])

    def by_key():
        return level >= 2 or clean

    def by_name(marked):
        return level == 3 or (level == 2 and marked) or clean

    hits = [hit for hit in found['hits']
            if (by_key() if hit['key'] else by_name(hit['marked']))]
    missing = [item for item in found['missing']
               if (by_key() if item['kind'] == 'license'
                   else by_name(True) if item['sure'] else clean)]
    if not hits and not missing:
        return None
    return {'hits': hits, 'missing': missing}


def continues(analysis):
    """Продолжает ли реплика без своего водителя разговор о прежнем.

    «а приз какой?», «ну а сколько денег он заработал?», «а на прошлой?», «а в
    каком он парке?» — да: в реплике есть слово темы или неделя, и ничто не
    уводит её в сторону. Уводят: слова вопроса об условиях («когда», «какие»,
    «всего»), число («за 3 место»), заглавное незнакомое слово (другой человек
    или другая тема) и время, которое не разобрать («в сентябре», «раньше»).
    Обычные слова («дали», «точно», «сделал») разговор не рвут.
    """
    week = analysis['week']
    if not (topic(analysis) or week['bare'] or week['shift']):
        return False
    if analysis['general'] or analysis['numbers'] or week['unknown']:
        return False
    return not any(token['kind'] == 'name' and token['capital'] for token in analysis['tokens'])


def _week_of_turn(analysis, state):
    """Неделя, о которой реплика, — своя или недели разговора."""
    week = analysis['week']
    kept = state['week'] if state else None
    if week['asked'] is not None:
        return week['asked']
    if week['number'] is not None:
        return {'kind': 'number', 'number': week['number'], 'weak': True, 'instead': kept}
    if state is not None and week['shift']:
        return {'kind': 'shift', 'base': kept, 'back': week['shift']}
    if state is not None and week['bare'] is not None:
        return week['bare']
    return kept


def conversation(own, priors, find, *, places=frozenset()):
    """О ком и о какой неделе вопрос: {'subject', 'week'} или None — списки молчат.

    priors — прошлые вопросы человека по порядку, own — нынешний. Разговор о
    водителе живёт от реплики, которая его назвала (accept), через реплики,
    которые его продолжают (continues), и обрывается первой репликой на другую
    тему: «а приз какой?» после вопроса о возврате уже ни о ком.

    Реплики разбираются ПО ОДНОЙ и по порядку, а не склейкой: в склейке дата из
    позапрошлой реплики побеждала «прошлую неделю» из последней, а вопрос
    читался дважды.
    """
    state = None
    for turn in list(priors) + [own]:
        subject = accept(turn, identify(turn, find, places=places), conversation=state is not None) \
            if (_names_someone(turn) and (topic(turn) or state is not None)) else None
        if subject is not None:
            state = {'subject': subject, 'week': _week_of_turn(turn, state)}
        elif state is not None and continues(turn):
            state = {'subject': state['subject'], 'week': _week_of_turn(turn, state)}
        else:
            state = None
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Неделя: что спросили и какой список отвечает
# ─────────────────────────────────────────────────────────────────────────────

def resolve_week(asked, weeks):
    """Список, которым отвечаем, — {'status', 'target', 'asked'}.

    weeks — загруженные недели, свежие сверху (queries.list_weeks), не пусто.
        latest    неделю не называли — последний загруженный список
        asked     спрошенная неделя загружена
        not_yet   спрошенная неделя новее последней загруженной: её списка ещё
                  нет; отвечаем последним загруженным и говорим об этом
        missing   спрошенной недели в разделе нет (и не будет: она в прошлом) —
                  другой неделей не подменяем
    """
    latest = weeks[0]
    if asked is None:
        return {'status': 'latest', 'target': latest, 'asked': None}
    if asked['kind'] == 'number':
        for week in weeks:
            if week.get('week_number') == asked['number']:
                return {'status': 'asked', 'target': week, 'asked': asked}
        if asked.get('weak'):
            # «3 неделя подряд», «неделя 3 место»: такой недели нет — значит,
            # число было не номером, и неделя остаётся прежней.
            return resolve_week(asked.get('instead'), weeks)
        return {'status': 'missing', 'target': None, 'asked': asked}
    if asked['kind'] == 'shift':
        base = resolve_week(asked['base'], weeks)
        if base['target'] is None:
            return base
        day = base['target']['period_start'] - timedelta(days=WEEK_DAYS * asked['back'])
        asked = _week_of(day, 'date')
    for week in weeks:
        if week['period_start'] <= asked['day'] <= week['period_end']:
            return {'status': 'asked', 'target': week, 'asked': asked}
    if asked['day'] > latest['period_end']:
        return {'status': 'not_yet', 'target': latest, 'asked': asked}
    return {'status': 'missing', 'target': None, 'asked': asked}


# ─────────────────────────────────────────────────────────────────────────────
# Текст фрагментов
# ─────────────────────────────────────────────────────────────────────────────
#
# Два правила текста, и оба — про проверки помощника, а не про стиль.
#
# 1. Каждое число ответа обязано стоять здесь буквально (answer.ungrounded_numbers
#    смотрит только в текст фрагментов): место, сумма, поездки, приз, даты недели.
# 2. Фрагмент — итог ПРОШЕДШЕЙ недели, и пометок свежести ему не положено
#    (wiki/ai/currency.py пропускает source_kind 'baiga'): иначе неделя с датой
#    в прошлом или зачёт с неудачным названием подписывались бы «срок истёк».

def _dmy(value):
    return value.strftime('%d.%m.%Y')


def _period(start, end):
    return '%s – %s' % (_dmy(start), _dmy(end))


def _week_text(week):
    text = 'неделю %s' % _period(week['period_start'], week['period_end'])
    return text + (' (неделя № %d)' % week['week_number'] if week.get('week_number') else '')


def _asked_text(asked):
    if asked['kind'] == 'number':
        return 'неделю № %d' % asked['number']
    return 'неделю %s' % _period(asked['start'], asked['end'])


def _money(value):
    return '{:,}'.format(int(value)).replace(',', ' ') + ' ₸'


def _prize_text(row):
    if not row.get('has_prize'):
        return 'приза нет'
    name = str(row.get('prize_name') or '').strip()
    return 'приз %s' % name if name else 'приз есть, сумма не указана'


def _who(row):
    name = str(row.get('driver_name') or '').strip()
    license_value = str(row.get('license') or '').strip()
    return '%s (%s)' % (name, 'номер ВУ %s' % license_value if license_value
                        else 'номер ВУ в списке не указан')


def _result_text(row):
    return 'зачёт «%s», %d место, сумма за неделю %s, поездок %d, %s' % (
        row['zachet'], row['position'], _money(row['amount']), row['trips'], _prize_text(row))


def _where_text(row):
    parts = []
    if row.get('city'):
        parts.append('Город: %s.' % row['city'])
    if row.get('park'):
        parts.append('Таксопарк: «%s».' % row['park'])
    return ' '.join(parts)


def _fragment(index, *, heading, text, week=None, ref_key=None, evidence=None):
    return {
        'chunk_id': -(_CHUNK_BASE + index + 1),
        'article_id': None,
        'chunk_idx': 0,
        'title': SECTION_TITLE,
        'slug': '',
        'heading_path': heading,
        'text': text,
        # Строка водителя без строки недели: опора источника и цитата под
        # ответом (answer._evidence). None — весь текст.
        'evidence': evidence,
        'requires_ack': False,
        'historical': False,
        'similarity': None,
        'found_by': [BRANCH],
        'directory_hit': True,
        'source_kind': SOURCE_KIND,
        'tab': SOURCE_TAB,
        # Чип под ответом открывает раздел на этой неделе и на этом водителе.
        'ref_id': week['id'] if week else None,
        'ref_key': ref_key,
        'ref_city': None,
        'space_id': None,
    }


def _ref_key(row):
    """Чем чип под ответом ищет водителя в разделе: номером ВУ в том виде, в
    каком он лежит в поиске раздела (без пробелов и дефисов, латиницей), — номер
    «ZZ-123456» в написании файла поиск раздела не находит. Нет номера — ID."""
    return str(row.get('license_key') or '').strip() or str(row.get('driver_key') or '').strip() or None


def _weeks_note(resolution, weeks):
    """Фрагмент о самой неделе — когда спрошенной в разделе нет."""
    asked, latest = resolution['asked'], weeks[0]
    if resolution['status'] == 'not_yet':
        return ('Списка Байги за %s в разделе пока нет: список загружают, когда неделя прошла. '
                'Последний загруженный список — за %s.' % (_asked_text(asked), _week_text(latest)))
    if resolution['status'] == 'missing':
        listed = '; '.join(_period(week['period_start'], week['period_end'])
                           for week in weeks[:WEEKS_LISTED])
        more = len(weeks) - WEEKS_LISTED
        return ('Списка Байги за %s в разделе нет. Загруженные недели: %s%s.'
                % (_asked_text(asked), listed, ' и ещё %d' % more if more > 0 else ''))
    return None


def _person_fragment(index, person, resolution, weeks):
    """Фрагмент одного водителя: его строка в списке недели — или «в списке нет»
    и последний список, где он был."""
    target = resolution['target']
    week_by_start = {week['period_start']: week for week in weeks}
    mine = [row for row in person['rows'] if row['period_start'] == target['period_start']]
    if mine:
        lines = ['Список Байги за %s%s.' % (
            _week_text(target),
            ' — последний загруженный' if resolution['status'] in ('latest', 'not_yet') else '')]
        results = ['%s: %s.' % (_who(row), _result_text(row)) for row in mine]
        lines.extend(results)
        where = _where_text(mine[0])
        if where:
            lines.append(where)
        return _fragment(index, week=target, ref_key=_ref_key(mine[0]), text='\n'.join(lines),
                         evidence='\n'.join(results),
                         heading='Неделя %s › %s' % (
                             _period(target['period_start'], target['period_end']), mine[0]['zachet']))

    last = person['rows'][0]                     # строки идут от свежей недели
    lines = ['В списке Байги за %s водителя %s нет.' % (_week_text(target), _who(last))]
    seen = week_by_start.get(last['period_start'])
    if seen is not None:
        lines.append('Последний список, в котором он есть, — за %s: %s.' % (
            _week_text(seen), '; '.join(
                _result_text(row) for row in person['rows']
                if row['period_start'] == last['period_start'])))
    return _fragment(index, week=seen, ref_key=_ref_key(last), text='\n'.join(lines),
                     heading='Неделя %s › нет в списке' % _period(
                         target['period_start'], target['period_end']))


def _missing_fragment(index, item, resolution, weeks):
    target = resolution['target']
    others = len(weeks) - 1
    if item['kind'] == 'license':
        lines = ['В списке Байги за %s водителя с номером ВУ %s нет.' % (_week_text(target), item['label'])]
        if others > 0:
            lines.append('В остальных загруженных списках его тоже нет.')
    elif item['sure']:
        lines = ['В списке Байги за %s водителя «%s» нет — поиск шёл по фамилии.' % (
            _week_text(target), item['label'])]
        if others > 0:
            lines.append('В остальных загруженных списках такой фамилии тоже нет.')
        lines.append('Точнее всего водителя находит номер ВУ.')
    else:
        lines = ['В списке Байги за %s водителя с фамилией «%s» нет%s.' % (
            _week_text(target), item['label'],
            ', в остальных загруженных списках — тоже' if others > 0 else ''),
            'Если «%s» — не фамилия водителя, этот фрагмент к вопросу не относится.' % item['label']]
    return _fragment(index, week=target, text='\n'.join(lines),
                     ref_key=item['label'] if item['kind'] == 'license' else None,
                     heading='Неделя %s › нет в списке' % _period(
                         target['period_start'], target['period_end']))


# ─────────────────────────────────────────────────────────────────────────────
# Сборка
# ─────────────────────────────────────────────────────────────────────────────

def _places(weeks):
    """Слова, которые фамилией не бывают: сервисы и города (_NOT_NAMES) и слова
    названий зачётов загруженных недель («Алматы-Каскелен»). Зачёты берутся из
    самих недель и отдельного запроса не стоят."""
    words = set(_NOT_NAMES)
    for week in weeks:
        for sheet in week.get('sheets') or ():
            words.update(word for word in _words((sheet or {}).get('name')) if len(word) > 2)
    return frozenset(words)


def _people(subject, load):
    """Названные водители со строками: [{'rows': […]}] в порядке упоминания.

    Человек — это и ID, и номер ВУ: у водителя, заведённого в парке заново, ID
    новый, а ВУ прежний; у другого номер ВУ в одной из недель записан иначе или
    пуст, а ID тот же. Строки, связанные ЛЮБЫМ из двух ключей, — строки одного
    человека (иначе неделя, где номер записан по-другому, выпадала, и про
    водителя из списка говорилось «в списке нет»).
    """
    driver_keys, license_keys, order = set(), set(), []
    for hit in subject['hits']:
        for row in hit['people']:
            if row['driver_key'] not in order:
                order.append(row['driver_key'])
            driver_keys.add(row['driver_key'])
            if row['license_key']:
                license_keys.add(row['license_key'])
    if not order:
        return []
    rows = load(driver_keys=sorted(driver_keys), license_keys=sorted(license_keys))

    group_of = {}                                # ключ → номер группы

    def keys_of(row):
        return ['id:%s' % row['driver_key']] + (['vu:%s' % row['license_key']] if row['license_key'] else [])

    groups = {}
    for count, row in enumerate(rows):
        linked = sorted({group_of[key] for key in keys_of(row) if key in group_of})
        number = linked[0] if linked else count
        groups.setdefault(number, [])
        for other in linked[1:]:                 # строка связала две группы — это один человек
            groups[number].extend(groups.pop(other))
            for key, value in list(group_of.items()):
                if value == other:
                    group_of[key] = number
        groups[number].append(row)
        for key in keys_of(row):
            group_of[key] = number

    people, seen = [], set()
    for driver_key in order:
        number = group_of.get('id:%s' % driver_key)
        if number is None or number in seen:
            continue
        seen.add(number)
        people.append({'rows': sorted(groups[number], key=lambda row: (
            -row['period_start'].toordinal(), row['sheet_order'], row['position']))})
    return people


def build_rows(own, priors, *, weeks, find, load):
    """Фрагменты помощника из разобранных реплик. База — только через find и
    load, поэтому тесты гоняют сборку на списке в памяти.

    Порядок: сначала «спрошенной недели нет», потом водители, потом те, кого в
    списках не оказалось.
    """
    state = conversation(own, priors, find, places=_places(weeks))
    if state is None:
        return []
    if not weeks:
        return [_fragment(0, heading='Недели',
                          text='В разделе «Списки Байги» пока нет ни одной загруженной недели.')]

    subject = state['subject']
    resolution = resolve_week(state['week'], weeks)
    fragments = []
    note = _weeks_note(resolution, weeks)
    if note:
        fragments.append(_fragment(0, heading='Недели', text=note, week=resolution['target']))
    if resolution['target'] is None:
        return fragments

    target_start = resolution['target']['period_start']
    people = _people(subject, load)
    # Сначала те, кто в спрошенном списке есть: при однофамильцах ответ — о них.
    people.sort(key=lambda person: not any(row['period_start'] == target_start
                                           for row in person['rows']))
    for person in people[:MAX_PEOPLE]:
        fragments.append(_person_fragment(len(fragments), person, resolution, weeks))
    if len(people) > MAX_PEOPLE:
        fragments.append(_fragment(
            len(fragments), heading='Недели', week=resolution['target'],
            text='Под вопрос подходят %d водителей, показаны первые %d. Чтобы найти нужного, '
                 'спросите по номеру ВУ.' % (len(people), MAX_PEOPLE)))
    for item in subject['missing']:
        fragments.append(_missing_fragment(len(fragments), item, resolution, weeks))
    return fragments


def ai_rows(cursor, *, user_id, question, space_id, sensitive_access_granted,
            prior=(), today=None):
    """Фрагменты «Списков Байги» к вопросу помощника — или [].

    prior — прошлые вопросы человека в этом разговоре, от старого к новому.

    Порядок проверок — от дешёвого к дорогому и от права к данным: сначала
    разбор текста (большинству вопросов вики списки не нужны, и база для них
    не тронута), потом пространство и доступ, и только потом — строки.
    """
    if today is None:
        from wiki.ai import currency

        today = currency.today()
    own = analyze(question, today=today)
    turns = [text for text in (prior or ()) if parse.clean_text(text)]
    priors = [analyze(text, today=today) for text in turns[-PRIOR_TURNS:]]
    if not _worth_asking(own, priors):
        return []
    if not space_reads_lists(cursor, space_id):
        return []
    if reader_context(cursor, user_id, sensitive_access_granted=sensitive_access_granted) is None:
        return []

    weeks = queries.list_weeks(cursor, parse.CAMPAIGN)

    def find(**keys):
        return queries.assistant_candidates(cursor, limit=CANDIDATE_LIMIT, **keys)

    def load(**keys):
        return queries.assistant_rows(cursor, **keys)

    return build_rows(own, priors, weeks=weeks, find=find, load=load)
