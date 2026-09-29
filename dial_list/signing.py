# -*- coding: utf-8 -*-
"""Подписание документов водителем: чистые правила, без базы и сети.

Запрос владельца 29.09.2026: к базе обзвона добавляется обязательный ИИН, по
нему Sapar (tps-public.silt.kz) говорит, подписал ли водитель документы.
Подписавший засчитывается успешкой последнему оператору, кто с ним говорил не
меньше 10 секунд, и больше в список не попадает — обрабатывать его незачем.

Что здесь решается и почему так:

  * Какой месяц документов спрашивать. Документы за месяц приходят водителю в
    СЛЕДУЮЩЕМ месяце (замер 29.09.2026: августовские подписаны 08–26 сентября),
    поэтому база месяца P проверяется по документам месяца P − 1.

  * Общий статус. У водителя за месяц от 2 до 13 документов Яндекса (замер по
    38 водителям за 08.2026). Правило владельца — «брать минимальный по
    успешности»: Подписан + Не подписан + На проверке у Яндекса = Не подписан.
    Отсюда порядок SIGN_RANK: подписан > на проверке у Яндекса > не подписан >
    остальное. Успешка — только когда ВСЕ документы подписаны.

  * Решают только документы Яндекса (YandexDocuments). АВР парка
    (TaxiParkDocuments) приходит и тем, кому подписывать нечего, — см.
    crm/sapar.py и заметку про ArrivalStatus.

  * Когда водитель подписал. Sapar отдаёт время подписи водителем
    (DocSignDateByDriver) без часового пояса; это UTC: по 6 000 документам
    подписания резко растут в 04:00 по файлу (09:00 по Алматы), а меньше всего
    их в 22–00 (3–5 утра по Алматы). Момент подписания пакета — последняя из
    подписей: пока не подписан последний документ, водитель не подписал.
"""
import re
from datetime import date, datetime, timedelta, timezone

PERIOD_TZ = timezone(timedelta(hours=5))  # Asia/Almaty

# Общие статусы пакета документов водителя за месяц.
SIGNED = "signed"
PROCESSING = "processing"
UNSIGNED = "unsigned"
NOT_FORMED = "not_formed"
EXPIRED = "expired"
REJECTED = "rejected"
NO_DOCS = "no_docs"
# Не статусы Sapar, а состояния строки у нас: ИИН не указан и ещё не проверяли.
NO_IIN = "no_iin"
NOT_CHECKED = "not_checked"

# Статус документа Яндекса (ServiceAvrStatus) → общий статус. Неизвестное слово
# считается «не подписан»: успешкой документ становится только явным «подписан».
SAPAR_STATUS_GROUP = {
    "Подписано": SIGNED,
    "Signed": SIGNED,
    "НаПодписанииУЯндекса": PROCESSING,
    # «Идёт сохранение файла» — подпись уже поставлена, Sapar сохраняет файл.
    "ИдетСохранениеФайла": PROCESSING,
    "НаПодписанииУВодителя": UNSIGNED,
    "НеСформировано": NOT_FORMED,
    "СрокПодписанияИстек": EXPIRED,
    "Rejected": REJECTED,
    "Cancelled": REJECTED,
}

# Успешность: больше — лучше. Общий статус пакета — минимальный из документов.
SIGN_RANK = {
    SIGNED: 6,
    PROCESSING: 5,
    UNSIGNED: 4,
    NOT_FORMED: 3,
    EXPIRED: 2,
    REJECTED: 1,
}

# Подписи для интерфейса. Двойник — src/components/dial_list/signStatus.js,
# набор ключей сторожит тест.
SIGN_STATUS_LABELS = {
    SIGNED: "Подписал",
    PROCESSING: "На проверке у Яндекса",
    UNSIGNED: "Не подписал",
    NOT_FORMED: "Документы не сформированы",
    EXPIRED: "Срок подписания истёк",
    REJECTED: "Документы отклонены",
    NO_DOCS: "Документы не поступили",
    NOT_CHECKED: "Ещё не проверяли",
    NO_IIN: "Нет ИИН",
}

# Разговор, после которого подписание засчитывается оператору (правило владельца).
SUCCESS_MIN_BILLSEC = 10
# Как часто перепроверять водителя (правило владельца — каждые 3 часа).
RECHECK_HOURS = 3

_FRACTION_RE = re.compile(r"\.(\d+)")


def group_of(status):
    """Статус одного документа → общий статус (неизвестный — «не подписал»)."""
    return SAPAR_STATUS_GROUP.get(str(status or "").strip(), UNSIGNED)


def aggregate(documents):
    """Общий статус пакета документов Яндекса — минимальный по успешности."""
    groups = [group_of((d or {}).get("Status")) for d in (documents or [])]
    if not groups:
        return NO_DOCS
    return min(groups, key=lambda g: SIGN_RANK[g])


def parse_sapar_time(value):
    """'2026-09-10T01:19:48.21285' → datetime в UTC. Без пояса — UTC (см. шапку);
    с поясом или «Z» — как написано. Пусто или мусор — None."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # Дробь секунд у Sapar бывает любой длины, а fromisoformat старых версий
    # принимает только 3 или 6 цифр.
    text = _FRACTION_RE.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def signed_moment(documents, detected_at):
    """Когда водитель подписал пакет: последняя подпись среди документов. Нет
    времени хотя бы у одного — время, когда мы это увидели. Позже того, как
    увидели, подписать нельзя — это страховка от чужого часового пояса."""
    moments = [parse_sapar_time((d or {}).get("DocSignDateByDriver")) for d in (documents or [])]
    if moments and all(moments):
        return min(max(moments), detected_at)
    return detected_at


def document_rows(documents):
    """Что храним о документах у лида: номер, статус, время подписи. Без ФИО,
    ИИН и сумм — для карточки водителя этого достаточно."""
    rows = []
    for d in documents or []:
        d = d or {}
        moment = parse_sapar_time(d.get("DocSignDateByDriver"))
        rows.append({
            "id": d.get("ServiceId"),
            "status": str(d.get("Status") or ""),
            "group": group_of(d.get("Status")),
            "signed_at": moment.isoformat() if moment else None,
        })
    return rows


def month_start(day):
    return date(day.year, day.month, 1)


def shift_month(day, months):
    index = day.year * 12 + (day.month - 1) + int(months)
    return date(index // 12, index % 12 + 1, 1)


def doc_month_for(period):
    """База месяца P проверяется по документам месяца P − 1 → (месяц, год)."""
    previous = shift_month(month_start(period), -1)
    return previous.month, previous.year


def check_window(today):
    """Базы каких месяцев проверяем: текущего и прошлого. Прошлый — потому что
    Яндекс ставит свою подпись с задержкой, и «на проверке у Яндекса» в последние
    дни месяца становится «подписано» уже в следующем."""
    current = month_start(today)
    return shift_month(current, -1), current


def status_code(iin, sign_status, signed_at):
    """Статус строки для интерфейса: подписал / из Sapar / не проверяли / нет ИИН."""
    if signed_at is not None:
        return SIGNED
    if not str(iin or "").strip():
        return NO_IIN
    return sign_status or NOT_CHECKED
