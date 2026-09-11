# -*- coding: utf-8 -*-
"""Автоматическая связка «пользователь amoCRM → сотрудник портала». Чистая логика.

Зачем отдельный модуль
----------------------
В amoCRM у сделки лежит числовой id ответственного. Пока учётка портала не была
администратором, справочник `/api/v4/users` отвечал 403, и связку приходилось
выводить окольным путём — по совпадению суточных объёмов сделок. С 11.09.2026 у
нас админский доступ, и справочник открыт: 59 пользователей с именами и
корпоративной почтой. Значит связку можно строить честно.

Почему не хватает простого сравнения имён
-----------------------------------------
Потому что в amoCRM людей заводят как попало, и это не исключение, а норма:

    «Nurmakhan 6323»        sagidollayev_nurmakhan2_co@   Сагидоллаев Нурмахан
    «ardak.xo»              jolmaganbet_ardaq_co@         Жолмағанбет Ардақ
    «k.dilnaz»              kyzdarbek_dilnaz_co@          Қыздарбек Дильназ
    «merey_sarseke»         sarseke_merey_co@             Сарсеке Мерей
    «Aisha Kissapova»       aisha_kisapova_co@            Кисапова Айша

Имя не спасает, а почта — спасает: она собрана из фамилии и имени. Но и её пишут
по-разному, потому что единой транслитерации нет: «ж» это и `zh`, и `j`; «х» — и
`h`, и `kh`; «қ» — и `k`, и `q`; «й» — и `y`, и `i`; «ев» — и `ev`, и `yev`.
Поэтому обе стороны сводятся к огрублённому виду, где эти пары неразличимы, а
остаток добирается похожестью строк.

Главное правило: **связываем только однозначное.** Если под почту подходит
больше одного сотрудника — решает человек. Ошибка здесь невидима: чужие цифры
встанут в чужую строку, и в отчёте это выглядит нормально.

Проверено на живых данных 11.09.2026: 59 пользователей amoCRM против 201
сотрудника портала — связалось 32, неоднозначных НОЛЬ. Несвязанные — служебные
учётки («Администратор», «ЯР», «Отток группа», «Фокус группа», «Маркетинг
iGroup») и уволенные, которых в портале уже нет.
"""

import re
from difflib import SequenceMatcher

# Кириллица (включая казахские буквы) в огрублённую латиницу. Буквы, которые в
# транслитерации путают, сведены заранее: й→i, ы→i, ч→c, ш→s, я→a, ю→u.
_CYRILLIC = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'j',
    'з': 'z', 'и': 'i', 'й': 'i', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
    'ч': 'c', 'ш': 's', 'щ': 's', 'ъ': '', 'ы': 'i', 'ь': '', 'э': 'e', 'ю': 'u',
    'я': 'a',
    'ә': 'a', 'ғ': 'g', 'қ': 'k', 'ң': 'n', 'ө': 'o', 'ұ': 'u', 'ү': 'u', 'һ': 'h',
    'і': 'i',
}

# Диграфы латиницы к тем же буквам, что дала кириллица.
_DIGRAPHS = (('zh', 'j'), ('kh', 'h'), ('ch', 'c'), ('sh', 's'), ('ts', 'c'),
             ('yu', 'u'), ('ya', 'a'), ('ye', 'e'), ('yi', 'i'))

# Куски почты, которые именем не являются.
_EMAIL_NOISE = frozenset(('co', 'adm', 'op', 'kz', 'com', 'ru'))

# Насколько похожими считаем два слова. 0,82 подобрано по живым данным: при нём
# «sagidollaev» и «sagidollayev» сходятся, а разные фамилии — нет.
SIMILARITY = 0.82


def rough(text):
    """Огрублённый вид строки: разнописания сводятся к одному.

    Двойные буквы схлопываются («Kissapova» и «Кисапова» дают одно), всё, кроме
    латинских букв, становится пробелом.
    """
    out = []
    for char in str(text or '').lower():
        out.append(_CYRILLIC.get(char, char))
    text = ''.join(out)
    for source, target in _DIGRAPHS:
        text = text.replace(source, target)
    text = text.replace('q', 'k').replace('w', 'v').replace('x', 'ks').replace('y', 'i')
    text = re.sub(r'(.)\1+', r'\1', text)
    text = re.sub(r'[^a-z]+', ' ', text)
    return ' '.join(text.split())


def name_words(value):
    """Слова ФИО в огрублённом виде."""
    return {word for word in rough(value).split() if len(word) > 1}


def email_words(email):
    """Слова имени из корпоративной почты.

    Цифры выбрасываем: в почте они означают однофамильца («nurmakhan2»), а не
    часть имени.
    """
    local = str(email or '').split('@')[0]
    local = re.sub(r'\d+', ' ', local)
    words = set()
    for part in re.split(r'[._\-\s]+', local):
        key = rough(part)
        if key and key not in _EMAIL_NOISE and len(key) > 1:
            words.add(key)
    return words


def _close(left, right):
    if left == right:
        return True
    if abs(len(left) - len(right)) > 2:
        return False
    return SequenceMatcher(None, left, right).ratio() >= SIMILARITY


def looks_same(source_words, person_words):
    """Совпадают ли два набора слов настолько, чтобы считать это одним человеком.

    Требуем минимум ДВА совпавших слова — фамилию и имя. Одного мало: «Дана»
    в отделе не одна, и по одному слову связка была бы лотереей.
    """
    if len(source_words) < 2 or len(person_words) < 2:
        return False
    used, hits = set(), 0
    for word in sorted(source_words, key=len, reverse=True):
        for candidate in person_words:
            if candidate in used:
                continue
            if _close(word, candidate):
                used.add(candidate)
                hits += 1
                break
    return hits >= 2


def prepare_people(people):
    """[{id, name, email}] → тот же список с посчитанными словами."""
    out = []
    for person in people or []:
        out.append({
            'id': person.get('id'),
            'name': person.get('name') or '',
            'email': (person.get('email') or '').strip().lower(),
            'words': name_words(person.get('name')),
        })
    return out


def match_user(amo_user, people):
    """Кому из сотрудников соответствует пользователь amoCRM.

    Возвращает (сотрудник, как совпало) или (None, причина отказа):
      'email'      почта в портале и в amoCRM совпала буквально
      'email_name' почта разобрана на имя и сошлась с ФИО
      'name'       сошлось отображаемое имя из amoCRM
      None + 'ambiguous' — подошло несколько человек, решает человек
      None + 'unknown'   — не подошёл никто
    """
    email = str(amo_user.get('email') or '').strip().lower()
    if email:
        for person in people:
            if person['email'] and person['email'] == email:
                return person, 'email'

    for words, how in ((email_words(email), 'email_name'),
                       (name_words(amo_user.get('name')), 'name')):
        if not words:
            continue
        found = [person for person in people if looks_same(words, person['words'])]
        if len(found) == 1:
            return found[0], how
        if len(found) > 1:
            return None, 'ambiguous'
    return None, 'unknown'


def auto_link(amo_users, people):
    """Связать справочник amoCRM с сотрудниками. Только однозначное.

    Возвращает {'links': {id_amo: {...}}, 'ambiguous': [...], 'unknown': [...]}.

    Один сотрудник может быть связан с ДВУМЯ учётками amoCRM — так бывает, когда
    человеку завели новую («Айша Кисапова» и «Aisha Kissapova» — это один
    человек). Это не ошибка и не повод отказаться: сделки обеих учёток честно
    складываются в одну строку отчёта.
    """
    ready = prepare_people(people)
    links, ambiguous, unknown = {}, [], []
    for user in amo_users or []:
        code = str(user.get('id') or '').strip()
        if not code:
            continue
        person, how = match_user(user, ready)
        record = {
            'amo_id': code,
            'amo_name': str(user.get('name') or '').strip(),
            'amo_email': str(user.get('email') or '').strip(),
        }
        if person:
            links[code] = dict(record, user_id=person['id'], user_name=person['name'],
                               how=how)
        elif how == 'ambiguous':
            ambiguous.append(record)
        else:
            unknown.append(record)
    return {'links': links, 'ambiguous': ambiguous, 'unknown': unknown}
