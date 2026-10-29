# -*- coding: utf-8 -*-
"""Парк в тексте фрагмента: назван запрошенный, чужой или «все парки». Чистая логика.

Зачем. Акции, телефоны и адреса в вики лежат общими статьями построчно: «№: 3;
Парк: Jana Алматы; …», «Таксопарк: Стабильный; Номер WhatsApp: …», «Парк: Все
парки, кроме Eki Dongelek, Anytime, Ноль такси». Партнёрский ИИ отвечает за один
парк, и строка про чужой парк в его ответе — ошибка водителю. Отсюда четыре
вердикта на фрагмент (статья + заголовок + текст):

    named      запрошенный парк назван — отдаём, даже если рядом названы другие;
    general    парков не названо вовсе — общая информация, отдаём;
    all_parks  «все парки», «парки партнёры Такси Про», «кроме …» — отдаём как
               есть: владелец 29.10.2026 — «если уверенность низкая, отдавать
               чуть больше информации, ИИ сама догадается»;
    other      названы только чужие парки — не отдаём.

Как узнаём парк. Названия берутся из справочника парков пространства
(wiki_taxi_parks) и дополняются известными написаниями из статей
(EXTRA_ALIASES: «Такси 24», «2Донгелек», «Tenge Taxi», «Глобал»). Слова, которые в
обычной речи парком не являются («честный», «ноль», «центр», «халык» — он же банк),
засчитываются только рядом со словом «парк/таксопарк/такси» и не перед «банк».
Парки, которых в справочнике нет, но которые встречаются в акциях («Каспий»,
«Anytime»), для фильтра — чужие.
"""

import re

from wiki import directory as wiki_directory
from wiki import text as wiki_text

NAMED = 'named'
GENERAL = 'general'
ALL_PARKS = 'all_parks'
OTHER = 'other'
KEEP = frozenset((NAMED, GENERAL, ALL_PARKS))

# Другие написания парков по slug справочника. Латиница и кириллица, сокращения
# и формы, встречающиеся в статьях вики (сверено по проду 29.10.2026).
EXTRA_ALIASES = {
    'itaxi': ('itaxi', 'i taxi', 'ай такси', 'айтакси'),
    'itaxi-vip': ('itaxi vip', 'i taxi vip', 'ай такси вип', 'айтакси вип'),
    'taxi24': ('taxi24', 'taxi 24', 'такси24', 'такси 24'),
    'departament': ('департамент', 'departament', 'department'),
    'chestnyy-taksopark': ('честный', 'честный таксопарк', 'честные бонусы', 'chestny', 'chestnyy'),
    'global': ('global', 'глобал', 'global taxi', 'глобал такси'),
    'ipartner': ('ipartner', 'i partner', 'ай партнер', 'айпартнер'),
    'biznes-partner': ('бизнес партнер', 'business partner', 'вц бизнес', 'бизнес'),
    'halyk': ('халык', 'halyk', 'халик'),
    'qazaq': ('qazaq', 'казак'),
    'amanat': ('аманат', 'amanat'),
    'stabilnyy': ('стабильный', 'stabilny', 'stabilnyy'),
    'jana-taksi': ('jana', 'jana taxi', 'jana такси', 'жана такси', 'жана', 'джана'),
    '2dongelek': ('2dongelek', '2 dongelek', 'eki dongelek', 'екі дөңгелек', '2донгелек',
                  '2 донгелек', 'донгелек', 'еки донгелек', 'dongelek'),
    'tenge-taksi': ('тенге такси', 'tenge taxi', 'tenge', 'теңге такси'),
    'salam-taksi': ('salam', 'salam taxi', 'салам', 'салам такси'),
    'nol-taksi': ('ноль такси', 'нол такси', 'nol taxi', 'ноль'),
    'hokage-wolt': ('hokage', 'wolt', 'хокаге', 'вольт'),
    'centr-registraciy-regiony': ('центр регистраций', 'центр регистрации', 'центр рег', 'црр',
                                  'центр'),
}

# Названия, которые в обычной речи парком не являются: засчитываются только
# рядом со словом «парк/таксопарк/такси» и не перед «банк» («Халык банк»).
AMBIGUOUS = frozenset((
    'честный', 'ноль', 'нол', 'стабильный', 'департамент', 'department', 'бизнес',
    'центр', 'аманат', 'amanat', 'халык', 'halyk', 'халик', 'салам', 'salam', 'tenge',
    'жана', 'вольт', 'казак', 'партнер', 'регионы', 'regiony',
))

# Слова названий, которые сами по себе парком не бывают никогда: «тенге» — валюта
# в каждой второй акции, «такси» и «вип» — общие. Остаются только составные формы
# («тенге такси», «itaxi vip»).
NEVER_ALONE = frozenset(('тенге', 'такси', 'taxi', 'vip', 'вип', 'яна', 'про', 'pro'))

# Парки не из справочника, но встречающиеся в статьях: для фильтра — чужие.
FOREIGN_PARKS = {'каспий': 'Каспий', 'anytime': 'Anytime', 'энитайм': 'Anytime'}

# «Все парки», «парки партнёры Такси Про», «кроме …», «аналогично офферу».
_ALL_PARKS = re.compile(
    r'(?<!\w)(?:все парки|всех парк\w*|всем паркам|во всех парках|все таксопарки'
    r'|всех таксопарк\w*|любо\w* парк\w*|парк\w* партн\w*|партн\w* такси про'
    r'|парк\w* аналогично|по всем паркам|igroup|айгруп|все наши парки)(?!\w)')
_CONTEXT = re.compile(r'(?<!\w)(?:парк\w*|таксопарк\w*|такси|taxi|park)(?!\w)')
_BANK = re.compile(r'(?<!\w)(?:банк\w*|bank\w*|homebank|kaspi\w*|каспи)(?!\w)')
_NON_WORD = re.compile(r'[^\w]')
# Сколько знаков вокруг двусмысленного слова ищем слово-контекст.
WINDOW = 40
MIN_ALIAS = 3


def normalize(text):
    """Регистр, казахские буквы и ё — к русским; знаки — в пробелы той же длины.

    Длина сохраняется намеренно: вердикт смотрит на окно знаков вокруг
    совпадения, и позиции обязаны совпадать с исходным текстом."""
    return _NON_WORD.sub(' ', wiki_text.fold_kazakh(str(text or '').lower()))


def _compile(alias):
    words = alias.split()
    return re.compile(r'(?<!\w)' + r'\s+'.join(re.escape(word) for word in words) + r'(?!\w)')


def _aliases_for(park):
    names = set()
    for words in wiki_directory.park_name_words(park.get('name')):
        names.add(' '.join(words))
        if len(words) == 1:
            names |= wiki_directory.park_word_forms(words[0])
    names |= set(EXTRA_ALIASES.get(str(park.get('slug') or ''), ()))
    names.add(normalize(park.get('name')))
    cleaned = set()
    for name in names:
        value = ' '.join(normalize(name).split())
        if len(value) >= MIN_ALIAS and value not in NEVER_ALONE:
            cleaned.add(value)
    return cleaned


def _in_context(text, start, end):
    """Двусмысленное слово — парк, если рядом «парк/такси» и за ним не «банк»."""
    if _BANK.search(text[end:end + 16]):
        return False
    before = text[max(0, start - WINDOW):start]
    after = text[end:end + WINDOW]
    return bool(_CONTEXT.search(before) or _CONTEXT.search(after))


class ParkMatcher:
    """Парки пространства + их написания. Один на запрос, строится за миллисекунды."""

    def __init__(self, parks):
        self.names = {}
        self._patterns = []
        for park in parks:
            park_id = int(park['id'])
            self.names[park_id] = park.get('name')
            for alias in _aliases_for(park):
                self._patterns.append((park_id, _compile(alias), alias in AMBIGUOUS))
        for index, (alias, name) in enumerate(sorted(FOREIGN_PARKS.items()), start=1):
            self.names[-index] = name
            self._patterns.append((-index, _compile(alias), False))

    def mentions(self, text):
        """{id парка: название} — кто назван в тексте. Чужие из FOREIGN_PARKS — с id < 0."""
        norm = normalize(text)
        if not norm.strip():
            return {}
        hits = []
        for park_id, regex, ambiguous in self._patterns:
            for match in regex.finditer(norm):
                if ambiguous and not _in_context(norm, match.start(), match.end()):
                    continue
                hits.append((match.start(), match.end(), park_id))
        # «iTaxi VIP» подходит и под «iTaxi»: остаётся более длинное совпадение.
        hits.sort(key=lambda hit: (-(hit[1] - hit[0]), hit[0]))
        taken = []
        found = {}
        for start, end, park_id in hits:
            if any(start < t_end and end > t_start for t_start, t_end in taken):
                continue
            taken.append((start, end))
            found[park_id] = self.names.get(park_id)
        return found

    def classify(self, park_id, text):
        found = self.mentions(text)
        if int(park_id) in found:
            return NAMED
        if _ALL_PARKS.search(normalize(text)):
            return ALL_PARKS
        if found:
            return OTHER
        return GENERAL

    def others_in(self, park_id, text):
        """Названия чужих парков в тексте — для пометки «в вопросе другой парк»."""
        return sorted(name for pid, name in self.mentions(text).items()
                      if pid != int(park_id) and name)
