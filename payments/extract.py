"""Автозаполнение из документа (владелец 17.11.2026, п. 5: «загрузка документов с автоподставкой»).

Три документа:

* **Счёт на оплату** — в форме заявки: номер и дата счёта, сумма, позиции, поставщик (по БИН —
  из справочника), его банковские реквизиты, договор, на который счёт ссылается, НДС.
* **Договор** — в справочнике «Договоры»: номер, даты, поставщик и наша компания (по БИН),
  сумма и лимит, периодичность, предмет.
* **Выписка КГД** «Сведения по контрагентам» (п. 8, выбор владельца): НДС и режим поставщика
  (ОУР / СНР). Сам сервис КГД закрыт капчей; токен API КГД выдаёт по обращению компании, —
  пока его нет, бухгалтер скачивает выписку сам, а раздел читает её.

Документ читает Gemini на Vertex той же дорогой, что весь проект (ai_feedback.service: сервисный
аккаунт, цепочка моделей, переход на следующую при перегрузке); ответ — строгий JSON по схеме.
Ответу модели не верят на слово: `normalize` сводит его к проверенным значениям (БИН — 12 цифр,
ИИК — KZ и 18 знаков, даты — настоящие, суммы — положительные), остальное отбрасывает. В базу
модуль ничего не пишет: поля заполняются в форме, человек проверяет их и сохраняет сам — ИИ за
человека не отправляет (правило раздела).
"""

import base64
import json
import logging
import os
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx

log = logging.getLogger(__name__)

KIND_INVOICE = 'invoice'
KIND_CONTRACT = 'contract'
KIND_KGD = 'kgd'
KINDS = (KIND_INVOICE, KIND_CONTRACT, KIND_KGD)
KIND_LABELS = {KIND_INVOICE: 'Счёт на оплату', KIND_CONTRACT: 'Договор', KIND_KGD: 'Выписка КГД'}

MAX_BYTES = 10 * 1024 * 1024
MIME_TYPES = {
    'application/pdf': 'application/pdf', 'image/jpeg': 'image/jpeg', 'image/jpg': 'image/jpeg',
    'image/png': 'image/png', 'image/webp': 'image/webp', 'image/heic': 'image/heic', 'image/heif': 'image/heif',
}
_EXTENSION_MIME = {'.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                   '.webp': 'image/webp', '.heic': 'image/heic', '.heif': 'image/heif'}

DEFAULT_MODEL_CHAIN = ('gemini-2.5-flash', 'gemini-2.5-flash-lite')
ATTEMPT_TIMEOUT_SEC = float(os.getenv('PAYMENTS_EXTRACT_TIMEOUT', '') or 40)
# Человек ждёт с открытой формой: весь проход по цепочке — не дольше этого.
TOTAL_BUDGET_SEC = 60
MAX_ITEMS = 50
TEXT_MAX = 300


class ExtractError(Exception):
    def __init__(self, message, *, status=422, code='PAYMENT_EXTRACT_FAILED'):
        super().__init__(message)
        self.status = status
        self.code = code


# ─── Что просим у модели ─────────────────────────────────────────────────────

def _s(description):
    return {'type': 'STRING', 'nullable': True, 'description': description}


def _n(description):
    return {'type': 'NUMBER', 'nullable': True, 'description': description}


def _b(description):
    return {'type': 'BOOLEAN', 'nullable': True, 'description': description}


SCHEMAS = {
    KIND_INVOICE: {'type': 'OBJECT', 'properties': {
        'document_kind': _s('Что это за документ: invoice — счёт на оплату, other — что-то другое'),
        'invoice_number': _s('Номер счёта на оплату'),
        'invoice_date': _s('Дата счёта в формате YYYY-MM-DD'),
        'supplier_name': _s('Поставщик (кто выставил счёт) — как в документе'),
        'supplier_bin': _s('БИН или ИИН поставщика — 12 цифр'),
        'supplier_iik': _s('ИИК (IBAN) поставщика — KZ и 18 знаков'),
        'supplier_bank': _s('Банк поставщика'),
        'supplier_bik': _s('БИК банка поставщика'),
        'supplier_kbe': _s('КБе поставщика — 2 цифры'),
        'buyer_name': _s('Покупатель (кому выставлен счёт)'),
        'buyer_bin': _s('БИН покупателя — 12 цифр'),
        'contract_number': _s('Номер договора, если счёт на него ссылается'),
        'total_amount': _n('Итого к оплате, число'),
        'vat_included': _b('Есть ли в счёте НДС: true — с НДС, false — без НДС, null — не сказано'),
        'vat_amount': _n('Сумма НДС, число'),
        'payment_purpose': _s('Назначение платежа, если указано'),
        'items': {'type': 'ARRAY', 'nullable': True, 'items': {'type': 'OBJECT', 'properties': {
            'name': _s('Наименование товара или услуги'),
            'quantity': _n('Количество'),
            'unit': _s('Единица измерения'),
            'unit_price': _n('Цена за единицу'),
            'total': _n('Сумма строки'),
        }}},
    }},
    KIND_CONTRACT: {'type': 'OBJECT', 'properties': {
        'document_kind': _s('Что это за документ: contract — договор, other — что-то другое'),
        'number': _s('Номер договора'),
        'signed_on': _s('Дата подписания (заключения) в формате YYYY-MM-DD'),
        'starts_on': _s('Дата начала действия в формате YYYY-MM-DD'),
        'ends_on': _s('Дата окончания действия в формате YYYY-MM-DD; бессрочный — null'),
        'supplier_name': _s('Поставщик / исполнитель — как в документе'),
        'supplier_bin': _s('БИН или ИИН поставщика — 12 цифр'),
        'buyer_name': _s('Заказчик / покупатель — как в документе'),
        'buyer_bin': _s('БИН заказчика — 12 цифр'),
        'amount': _n('Сумма договора (за период, если платёж периодический), число'),
        'amount_limit': _n('Предельная (общая) сумма договора, если оговорена, число'),
        'periodicity': _s('Периодичность оплаты: monthly, quarterly, yearly или null'),
        'subject': _s('Предмет договора — коротко, одной фразой'),
        'vat_included': _b('Цена с НДС: true, без НДС: false, не сказано: null'),
    }},
    KIND_KGD: {'type': 'OBJECT', 'properties': {
        'document_kind': _s('Что это за документ: kgd — сведения КГД о налогоплательщике, other — другое'),
        'bin': _s('ИИН/БИН налогоплательщика — 12 цифр'),
        'name': _s('Наименование налогоплательщика'),
        'tax_regime': _s('Режим налогообложения дословно, как в документе'),
        'vat_payer': _b('Состоит ли на регистрационном учёте по НДС: true / false / null'),
        'vat_registered_on': _s('Дата постановки на учёт по НДС, YYYY-MM-DD'),
        'vat_removed_on': _s('Дата снятия с учёта по НДС, YYYY-MM-DD'),
        'statement_date': _s('Дата, на которую сформированы сведения, YYYY-MM-DD'),
    }},
}

PROMPTS = {
    KIND_INVOICE: 'Перед тобой счёт на оплату от казахстанской компании (на русском или казахском). '
                  'Выпиши поля строго по схеме. Чего в документе нет — null, ничего не придумывай. '
                  'Суммы — числом без пробелов и знака валюты. Позиции — построчно, как в таблице счёта.',
    KIND_CONTRACT: 'Перед тобой договор казахстанской компании (на русском или казахском). Выпиши поля '
                   'строго по схеме. Чего в документе нет — null, ничего не придумывай. Поставщик — тот, '
                   'кто оказывает услуги или поставляет товар; заказчик — тот, кто платит.',
    KIND_KGD: 'Перед тобой сведения Комитета государственных доходов РК о налогоплательщике (выписка '
              '«Сведения по контрагентам»). Выпиши поля строго по схеме. Чего в документе нет — null.',
}


# ─── Проверка ответа модели ──────────────────────────────────────────────────

def digits(value):
    return re.sub(r'\D+', '', str(value or ''))


def clean_bin(value):
    text = digits(value)
    return text if len(text) == 12 else None


def clean_iik(value):
    text = re.sub(r'\s+', '', str(value or '')).upper()
    return text if re.fullmatch(r'KZ[0-9A-Z]{18}', text) else None


def clean_bik(value):
    text = re.sub(r'\s+', '', str(value or '')).upper()
    return text if re.fullmatch(r'[A-Z0-9]{8}(?:[A-Z0-9]{3})?', text) else None


def clean_kbe(value):
    text = digits(value)
    return text if len(text) == 2 else None


def clean_text(value, limit=TEXT_MAX):
    text = ' '.join(str(value or '').split())
    if not text or text.lower() in ('null', 'none', '—', '-'):
        return None
    return text[:limit]


def clean_date(value):
    text = str(value or '').strip()
    if not text:
        return None
    for pattern in ('%Y-%m-%d', '%d.%m.%Y', '%d.%m.%y', '%d/%m/%Y'):
        try:
            parsed = datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
        return parsed.isoformat() if 2000 <= parsed.year <= 2100 else None
    return None


def clean_money(value):
    if value in (None, ''):
        return None
    try:
        amount = Decimal(str(value).replace(' ', '').replace(' ', '').replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None
    if amount <= 0 or amount > Decimal('1e12'):
        return None
    return str(amount.quantize(Decimal('0.01')))


def clean_quantity(value):
    if value in (None, ''):
        return None
    try:
        quantity = Decimal(str(value).replace(' ', '').replace(',', '.'))
    except (InvalidOperation, ValueError):
        return None
    return str(quantity.normalize()) if Decimal('0') < quantity < Decimal('1e7') else None


def clean_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value or '').strip().lower()
    if text in ('true', 'да', 'yes', '1'):
        return True
    if text in ('false', 'нет', 'no', '0'):
        return False
    return None


# Режим налогообложения словами выписки → ОУР / СНР (п. 8: «С НДС / без НДС, СНР, ОУР»).
_SNR_WORDS = ('специальн', 'упрощ', 'снр', 'патент', 'самозанят', 'розничн', 'фиксированн', 'крестьян', 'фермер',
              'кфх', 'сельскохозяйств', 'арнаулы')
_OUR_WORDS = ('общеустановлен', 'оур', 'жалпыға бірдей')


def regime_of(text):
    """(код, пояснение) — 'our' / 'snr' и вид СНР словами выписки; не понять — (None, None)."""
    raw = clean_text(text, 200)
    if not raw:
        return None, None
    low = raw.lower()
    if any(word in low for word in _OUR_WORDS):
        return 'our', None
    if any(word in low for word in _SNR_WORDS):
        # «Специальный налоговый режим на основе упрощённой декларации» → «на основе упрощённой
        # декларации»: слово «СНР» и так стоит рядом с пояснением.
        note = re.sub(r'^\s*(специальн\w*\s+налогов\w*\s+режим\w*|снр)[\s,:;—-]*', '', raw, flags=re.IGNORECASE).strip()
        return 'snr', (note or None)
    return None, None


def periodicity_of(value):
    text = str(value or '').strip().lower()
    if text in ('monthly', 'quarterly', 'yearly'):
        return text
    if 'месяч' in text or 'month' in text:
        return 'monthly'
    if 'квартал' in text or 'quarter' in text:
        return 'quarterly'
    if 'год' in text or 'year' in text or 'annual' in text:
        return 'yearly'
    return None


def normalize(kind, raw):
    """Ответ модели → только проверенные поля. Пустое значение в результат не попадает."""
    raw = raw if isinstance(raw, dict) else {}
    result = {}

    def put(key, value):
        if value not in (None, '', []):
            result[key] = value

    if kind == KIND_INVOICE:
        put('invoice_number', clean_text(raw.get('invoice_number'), 100))
        put('invoice_date', clean_date(raw.get('invoice_date')))
        put('supplier_name', clean_text(raw.get('supplier_name'), 200))
        put('supplier_bin', clean_bin(raw.get('supplier_bin')))
        put('supplier_iik', clean_iik(raw.get('supplier_iik')))
        put('supplier_bank', clean_text(raw.get('supplier_bank'), 200))
        put('supplier_bik', clean_bik(raw.get('supplier_bik')))
        put('supplier_kbe', clean_kbe(raw.get('supplier_kbe')))
        put('buyer_name', clean_text(raw.get('buyer_name'), 200))
        put('buyer_bin', clean_bin(raw.get('buyer_bin')))
        put('contract_number', clean_text(raw.get('contract_number'), 100))
        put('total_amount', clean_money(raw.get('total_amount')))
        vat = clean_bool(raw.get('vat_included'))
        if vat is not None:
            result['vat_included'] = vat
        put('vat_amount', clean_money(raw.get('vat_amount')))
        put('payment_purpose', clean_text(raw.get('payment_purpose'), 1000))
        items = []
        for item in (raw.get('items') or [])[:MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            name = clean_text(item.get('name'), 300)
            price = clean_money(item.get('unit_price'))
            quantity = clean_quantity(item.get('quantity')) or ('1' if price else None)
            if not name or not price:
                continue
            items.append({'name': name, 'quantity': quantity, 'unit': clean_text(item.get('unit'), 32),
                          'unit_price': price})
        put('items', items)
    elif kind == KIND_CONTRACT:
        put('number', clean_text(raw.get('number'), 100))
        for key in ('signed_on', 'starts_on', 'ends_on'):
            put(key, clean_date(raw.get(key)))
        if result.get('starts_on') and result.get('ends_on') and result['ends_on'] < result['starts_on']:
            result.pop('ends_on')
        put('supplier_name', clean_text(raw.get('supplier_name'), 200))
        put('supplier_bin', clean_bin(raw.get('supplier_bin')))
        put('buyer_name', clean_text(raw.get('buyer_name'), 200))
        put('buyer_bin', clean_bin(raw.get('buyer_bin')))
        put('amount', clean_money(raw.get('amount')))
        put('amount_limit', clean_money(raw.get('amount_limit')))
        put('periodicity', periodicity_of(raw.get('periodicity')))
        put('subject', clean_text(raw.get('subject'), 2000))
        vat = clean_bool(raw.get('vat_included'))
        if vat is not None:
            result['vat_included'] = vat
    elif kind == KIND_KGD:
        put('bin', clean_bin(raw.get('bin')))
        put('name', clean_text(raw.get('name'), 300))
        regime, note = regime_of(raw.get('tax_regime'))
        put('tax_regime', regime)
        put('tax_regime_note', note)
        put('tax_regime_text', clean_text(raw.get('tax_regime'), 200))
        vat = clean_bool(raw.get('vat_payer'))
        if vat is None and raw.get('vat_removed_on') and not raw.get('vat_registered_on'):
            vat = False
        if vat is not None:
            result['vat_payer'] = vat
        for key in ('vat_registered_on', 'vat_removed_on', 'statement_date'):
            put(key, clean_date(raw.get(key)))
    kind_seen = str(raw.get('document_kind') or '').strip().lower()
    result['_other_document'] = bool(kind_seen and kind_seen not in (kind, '', 'null'))
    return result


# ─── Сопоставление со справочниками ──────────────────────────────────────────

def match(cursor, kind, fields):
    """Что из прочитанного уже есть в справочниках: поставщик (по БИН, затем по названию), наша
    компания (по БИН), договор поставщика (по номеру), его реквизиты (по ИИК). Номера строк — в
    форму, чтобы выбор подставился сам."""
    found = {}
    supplier_bin = fields.get('supplier_bin') or fields.get('bin')
    supplier_name = fields.get('supplier_name') or fields.get('name')
    counterparty = None
    if supplier_bin:
        cursor.execute("SELECT id, name FROM payment_counterparties WHERE bin = %s ORDER BY is_active DESC, id "
                       "LIMIT 1", (supplier_bin,))
        counterparty = cursor.fetchone()
    if not counterparty and supplier_name and kind != KIND_KGD:
        key = ' '.join(supplier_name.lower().replace('«', '').replace('»', '').replace('"', '').split())
        cursor.execute(
            "SELECT id, name FROM payment_counterparties WHERE lower(regexp_replace(name, '[«»\"]', '', 'g')) = %s "
            "OR lower(regexp_replace(COALESCE(legal_name, ''), '[«»\"]', '', 'g')) = %s "
            "ORDER BY is_active DESC, id LIMIT 1", (key, key))
        counterparty = cursor.fetchone()
    if counterparty:
        found['counterparty_id'], found['counterparty_name'] = counterparty[0], counterparty[1]
    buyer_bin = fields.get('buyer_bin')
    if buyer_bin:
        cursor.execute("SELECT id, name FROM payment_legal_entities WHERE bin = %s AND is_active "
                       "ORDER BY id LIMIT 1", (buyer_bin,))
        entity = cursor.fetchone()
        if entity:
            found['legal_entity_id'], found['legal_entity_name'] = entity[0], entity[1]
    if counterparty and fields.get('supplier_iik'):
        cursor.execute("SELECT id FROM payment_counterparty_accounts WHERE counterparty_id = %s "
                       "AND upper(replace(iik, ' ', '')) = %s AND is_active ORDER BY is_default DESC, id LIMIT 1",
                       (counterparty[0], fields['supplier_iik']))
        account = cursor.fetchone()
        if account:
            found['counterparty_account_id'] = account[0]
    number = fields.get('contract_number') if kind == KIND_INVOICE else None
    if counterparty and number:
        cursor.execute(
            "SELECT id, number, status FROM payment_contracts WHERE counterparty_id = %s "
            "AND lower(regexp_replace(number, '[[:space:]№#]+', '', 'g')) = lower(regexp_replace(%s, '[[:space:]№#]+', '', 'g')) "
            "ORDER BY (status = 'active') DESC, id DESC LIMIT 1", (counterparty[0], number))
        contract = cursor.fetchone()
        if contract:
            found['contract_id'], found['contract_status'] = contract[0], contract[2]
    return found


# ─── Вызов модели ────────────────────────────────────────────────────────────

def mime_of(filename, content_type):
    mime = MIME_TYPES.get(str(content_type or '').lower())
    if mime:
        return mime
    ext = os.path.splitext(str(filename or '').lower())[1]
    return _EXTENSION_MIME.get(ext)


def _model_chain():
    raw = os.getenv('PAYMENTS_EXTRACT_MODEL_CHAIN', '')
    models = [m.strip() for m in raw.split(',') if m.strip()] if raw else []
    return models or list(DEFAULT_MODEL_CHAIN)


def build_payload(kind, data, mime, model, thinking_off=False, safety=None):
    config = {
        'temperature': 0,
        'maxOutputTokens': 8192,
        'responseMimeType': 'application/json',
        'responseSchema': SCHEMAS[kind],
    }
    if thinking_off:
        config['thinkingConfig'] = {'thinkingBudget': 0}
    payload = {
        'contents': [{'role': 'user', 'parts': [
            {'text': PROMPTS[kind]},
            {'inlineData': {'mimeType': mime, 'data': base64.b64encode(data).decode('ascii')}},
        ]}],
        'generationConfig': config,
    }
    if safety:
        payload['safetySettings'] = safety
    return payload


def _post(url, headers, payload):
    with httpx.Client(timeout=ATTEMPT_TIMEOUT_SEC) as client:
        response = client.post(url, json=payload, headers=headers)
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body


def _candidate_text(body):
    try:
        candidates = body.get('candidates') or []
        if not candidates:
            return ''
        parts = (candidates[0].get('content') or {}).get('parts') or []
        return ''.join(str(p.get('text') or '') for p in parts if isinstance(p, dict))
    except AttributeError:
        return ''


def read_document(kind, data, *, filename=None, content_type=None, post=None, budget_sec=TOTAL_BUDGET_SEC):
    """Документ → (сырой ответ модели, модель). ExtractError — прочитать не вышло."""
    if kind not in KINDS:
        raise ExtractError('Не знаю такого документа', status=400, code='PAYMENT_EXTRACT_KIND')
    if not data:
        raise ExtractError('Файл пустой', status=400, code='PAYMENT_FILES_MISSING')
    if len(data) > MAX_BYTES:
        raise ExtractError('Файл больше %s МБ — автозаполнение его не читает' % (MAX_BYTES // (1024 * 1024)),
                           status=413, code='PAYMENT_EXTRACT_TOO_BIG')
    mime = mime_of(filename, content_type)
    if not mime:
        raise ExtractError('Автозаполнение читает PDF и фото (JPG, PNG, HEIC)', status=415,
                           code='PAYMENT_EXTRACT_TYPE')
    from ai_feedback import service as ai   # лениво: ai_feedback тянет database/loguru

    if not (getattr(ai, 'GEMINI_API_KEY', None) or os.getenv('GOOGLE_APPLICATION_CREDENTIALS_CONTENT')):
        raise ExtractError('Автозаполнение недоступно: ИИ на сервере не настроен', status=503,
                           code='PAYMENT_EXTRACT_OFF')
    started = time.monotonic()
    last = ''
    for model in _model_chain():
        if time.monotonic() - started > budget_sec:
            last = last or 'истёк бюджет времени'
            break
        url, headers = ai.gemini_endpoint(model)
        payload = build_payload(kind, data, mime, model, thinking_off=model.startswith(ai.GEMINI_THINKING_OFF_MODELS),
                                safety=ai.safety_settings)
        try:
            status, body = (post or _post)(url, headers, payload)
        except httpx.HTTPError as exc:
            last = '%s: %s' % (model, type(exc).__name__)
            log.warning('Оплата счетов, автозаполнение: %s', last)
            continue
        if status in ai.GEMINI_FALLBACK_STATUS or status != 200:
            last = '%s: HTTP %s' % (model, status)
            log.warning('Оплата счетов, автозаполнение: %s', last)
            continue
        text = _candidate_text(body)
        if not text.strip():
            last = '%s: пустой ответ' % model
            continue
        try:
            parsed = json.loads(ai._extract_json_block(text))
        except ValueError:
            last = '%s: ответ не JSON' % model
            continue
        return parsed, model
    log.warning('Оплата счетов, автозаполнение не удалось: %s', last)
    raise ExtractError('Документ прочитать не вышло — заполните поля вручную', status=502,
                       code='PAYMENT_EXTRACT_FAILED')
