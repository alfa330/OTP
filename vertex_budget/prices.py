"""Тарифы Vertex AI для подсчёта расхода дня: $ за 1 млн токенов, (вход, выход).

Цены — из каталога Cloud Billing: сервис Vertex AI (C7E2-9256-1C43), у Gemini TTS — Cloud
Text-to-Speech (02DA-B362-D983); глобальные, без надбавки за длинный контекст; сверено
23.11.2026. С ними подсчёт по метрикам сошёлся со счётом Google: 18.11.2026 — 54,90 $,
19.11.2026 — 32,36 $ (tests/test_vertex_budget.py, сверка со счётом).

«Мышление» Google берёт как выход, и метрика token_count кладёт его туда же.

Незнакомая модель считается по цене Pro (DEFAULT): лучше выключить раньше, чем пропустить
дорогую новинку. Её имя сторож пишет в журнал — тогда её надо дописать сюда.
"""

DEFAULT = (2.0, 12.0)

# По части имени — раньше семейств: «gemini-3.1-flash-tts-preview» начинается как Flash, а
# стоит как TTS; «gemini-2.5-flash-image» — как картинка.
_MARKERS = (
    ('tts', (1.0, 20.0)),             # текст на входе, звук на выходе
    ('native-audio', (3.0, 12.0)),    # Live: звук на входе и на выходе
    ('live', (3.0, 12.0)),
)
_IMAGE_PRO = (2.0, 120.0)             # Gemini 3 Pro Image: картинка на выходе 120 $
_IMAGE = (0.5, 60.0)                  # Flash Image: у 2.5 — 30 $, у 3.1 — 60 $; берём дороже

# По началу имени. Порядок важен: «-lite» раньше своей Flash.
_FAMILIES = (
    ('gemini-3.1-pro', (2.0, 12.0)),
    ('gemini-3.0-pro', (2.0, 12.0)),
    ('gemini-3-pro', (2.0, 12.0)),
    ('gemini-2.5-pro', (1.25, 10.0)),
    ('gemini-3.5-flash-lite', (0.30, 2.50)),
    ('gemini-3.1-flash-lite', (0.25, 1.50)),
    ('gemini-2.5-flash-lite', (0.10, 0.40)),
    ('gemini-2.0-flash-lite', (0.075, 0.30)),
    ('gemini-3.8-flash', (1.50, 7.50)),
    ('gemini-3.7-flash', (1.50, 7.50)),
    ('gemini-3.6-flash', (1.50, 7.50)),
    ('gemini-3.5-flash', (1.50, 9.00)),
    ('gemini-3.1-flash', (0.50, 3.00)),
    ('gemini-3-flash', (0.50, 3.00)),
    ('gemini-2.5-flash', (0.30, 2.50)),
    ('gemini-2.0-flash', (0.15, 0.60)),
    ('gemini-embedding', (0.15, 0.0)),
    ('text-multilingual-embedding', (0.025, 0.0)),
    ('text-embedding', (0.025, 0.0)),
    ('multimodalembedding', (0.20, 0.0)),
)


def price_for(model):
    """((вход, выход) в $ за 1 млн токенов, знакома ли модель)."""
    name = str(model or '').strip().lower()
    for marker, price in _MARKERS:
        if marker in name:
            return price, True
    if 'image' in name:
        return (_IMAGE_PRO if '-pro' in name else _IMAGE), True
    for prefix, price in _FAMILIES:
        if name.startswith(prefix):
            return price, True
    return DEFAULT, False


def cost_usd(model, kind, tokens):
    """Стоимость токенов одного вида. Всё, что не «input», — по цене выхода: она дороже."""
    (price_in, price_out), _known = price_for(model)
    rate = price_in if kind == 'input' else price_out
    return max(0, int(tokens or 0)) * rate / 1_000_000
