"""Имена авторов Wazzup: нормализация, автоподсказка привязки, ключ автора.

Вынесено из bot_schedule2.py, потому что забор переписки «Потока»
(potok_sync) живёт вне Flask-приложения, а ключ автора у него строится из
имени — тем же правилом, что и подсказка привязки.
"""

# Казахские буквы → русские аналоги: «Тестбаев Нұрасыл» в Wazzup и «Тестбаев
# Нурасыл» в users — один человек, подсказка не должна спотыкаться об алфавит.
# Мягкий и твёрдый знаки просто выбрасываем: то же имя пишут и «Әділхан», и
# «Адильхан» — казахское написание мягкого знака не знает, русское его вставляет.
KAZ_TRANS = str.maketrans({
    'ә': 'а', 'ғ': 'г', 'қ': 'к', 'ң': 'н', 'ө': 'о', 'ұ': 'у', 'ү': 'у',
    'һ': 'х', 'і': 'и', 'ь': None, 'ъ': None,
})

# Окно чатов Wazzup подписывает отправленное через API как «API • Имя»;
# в самом поле authorName префикса нет, но на всякий случай снимаем и его.
_DISPLAY_PREFIXES = ('api • ', 'api •', 'api - ')


def normalize_name(value):
    """Нормализация имени: регистр, ё, казахские буквы, служебные префиксы, пробелы."""
    s = str(value or '').lower().replace('ё', 'е').translate(KAZ_TRANS)
    s = ' '.join(s.split())
    for prefix in _DISPLAY_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    return s


def suggest_user(author_name, operators):
    """Кандидат по имени: точное совпадение нормализованных имён, либо все слова
    автора входят в слова ФИО оператора (у нас ФИО полнее, чем ник в Wazzup).
    Двусмысленность (>1 кандидата) — подсказки нет."""
    norm = normalize_name(author_name)
    if not norm:
        return None
    exact = [op for op in operators if normalize_name(op['name']) == norm]
    if len(exact) == 1:
        return exact[0]
    tokens = set(norm.split())
    if len(tokens) < 2:
        return None
    partial = [op for op in operators
               if tokens <= set(normalize_name(op['name']).split())]
    return partial[0] if len(partial) == 1 else None


def author_key(account, author_id=None, author_name=None):
    """Ключ автора исходящего сообщения для wazzup_operator_map.author_id.

    У исторического аккаунта («op») это authorId из вебхука — внутренний id
    пользователя Wazzup. У «Потока» история приходит из окна чатов, где id
    автора нет, только имя; чтобы привязка одного человека не разъезжалась
    между историей и возможным вебхуком, у такого аккаунта ключ ВСЕГДА
    строится из нормализованного имени: 'potok:жолмаганбет ардак'.
    None — автора нет (входящее или безымянная отправка через API)."""
    if account == 'op':
        return str(author_id) if author_id is not None else None
    name = normalize_name(author_name)
    return f'{account}:{name}' if name else None
