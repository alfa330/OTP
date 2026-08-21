# -*- coding: utf-8 -*-
"""Карточка обращения картинкой (ТЗ #206).

Картинку нельзя проверить чтением строки, поэтому здесь проверяется то, из-за
чего она сломается молча: обрезанный текст, неизвестный знак, отсутствующий
глиф. «Посмотреть глазами» ловит это ровно один раз — на той тематике, которую
открыли.
"""

import io
import unittest

from PIL import Image, ImageFont

from crm import card, scenarios as sc

SAMPLE = {
    'iin': '060606060606', 'period': '2026-02', 'park': 'iTaxi', 'city': 'Алматы',
    'licence': 'AS100110', 'contact_number': '+7 747 352 42 48',
    'parcel_description': 'Коробка 30×20, документы',
    'order_date': '2026-08-18', 'last_try_at': '2026-08-17T12:38',
    'error_text': 'Ошибка 500 при сохранении подписанного документа',
    'what_to_check': 'Подписаны ли закрывающие документы за февраль',
    'device': 'Samsung A54, Android 14', 'browser': 'Google Chrome',
}


def answers_for(key):
    out = {}
    for item in sc.get(key)['steps']:
        name, kind = item['key'], item['kind']
        if kind == sc.ATTACHMENT:
            continue
        if name in SAMPLE:
            out[name] = SAMPLE[name]
        elif kind in (sc.YESNO, sc.YESNO_DATE):
            out[name] = 'yes'
        elif kind == sc.CHOICE:
            out[name] = item['options'][0]
        else:
            out[name] = 'текст'
    return out


def draw(key, answers=None, flags=()):
    answers = answers if answers is not None else answers_for(key)
    blocks = [block for block in sc.body_blocks(key, answers, flags=flags)
              if block['kind'] != sc.BLOCK_DATA]
    scenario = sc.get(key)
    return card.render_ticket_card(
        heading=scenario.get('group_title') or scenario['title'],
        subtitle='Обращение №42 · iTaxi Sapar',
        blocks=blocks,
    )


class EveryTopicDrawsTest(unittest.TestCase):
    def test_every_topic_renders(self):
        for scenario in sc.SCENARIOS:
            png = draw(scenario['key'])
            image = Image.open(io.BytesIO(png))
            self.assertEqual(image.format, 'PNG', scenario['key'])
            self.assertEqual(image.width, card.WIDTH * card.SCALE, scenario['key'])
            self.assertGreater(image.height, 100, scenario['key'])

    def test_picture_fits_telegram(self):
        """Предел Bot API — 10 МБ на фото и 10 000 точек по сумме сторон."""
        for scenario in sc.SCENARIOS:
            png = draw(scenario['key'])
            image = Image.open(io.BytesIO(png))
            self.assertLess(len(png), 2 * 1024 * 1024, scenario['key'])
            self.assertLess(image.width + image.height, 10000, scenario['key'])

    def test_taller_topic_gives_a_taller_picture(self):
        """Высота считается по содержимому: заготовленный холст с обрезкой давал
        бы разные поля снизу у разных тематик."""
        short = Image.open(io.BytesIO(draw('sapar_sign_status'))).height
        long = Image.open(io.BytesIO(draw('sapar_service_error'))).height
        self.assertGreater(long, short)


class NothingIsCutOffTest(unittest.TestCase):
    """Высота карточки считается в один проход, а рисуется в другой.

    Разойтись они могут от любой правки размеров, и тогда текст либо упрётся в
    край, либо повиснет над пустотой. И то, и другое видно только глазами — если
    не считать пиксели, чем этот класс и занимается.
    """

    def bottom_gap(self, png):
        image = Image.open(io.BytesIO(png)).convert('RGB')
        pixels = image.load()
        for y in range(image.height - 1, -1, -1):
            for x in range(image.width):
                if pixels[x, y] != card.CARD:
                    return image.height - 1 - y
        return image.height

    def test_bottom_margin_stays_within_the_padding(self):
        """Точного совпадения тут быть не может: у чипа заливка доходит до края
        своей коробки, а у строки текста чернила кончаются выше неё. Важно
        другое — что содержимое не упёрлось в край (обрезано) и что снизу не
        повис пустой блок (посчитали высоту не того).
        """
        floor = card.PAD * card.SCALE * 0.8
        ceiling = (card.PAD + 12) * card.SCALE
        for scenario in sc.SCENARIOS:
            gap = self.bottom_gap(draw(scenario['key']))
            self.assertTrue(floor <= gap <= ceiling,
                            '%s: поле снизу %s, ждали %s–%s'
                            % (scenario['key'], gap, floor, ceiling))


class MarksAreKnownTest(unittest.TestCase):
    def test_every_mark_of_the_scenarios_has_a_picture(self):
        """Новый знак в crm.scenarios без стиля здесь рисовался бы зелёной
        галочкой — то есть «нет» выглядело бы как «да»."""
        self.assertTrue(set(sc.FACT_MARKS.values()) <= set(card.MARK_STYLE),
                        set(sc.FACT_MARKS.values()) - set(card.MARK_STYLE))

    def test_marks_differ_by_shape_and_not_only_by_colour(self):
        shapes = {mark: style[0] for mark, style in card.MARK_STYLE.items()}
        self.assertEqual(len(set(shapes.values())), len(shapes), shapes)


class FontCoversTheTextTest(unittest.TestCase):
    """Шрифт лежит в репозитории, и его покрытие — наша забота.

    Ненайденный глиф Pillow рисует пустым местом, а не квадратом: пропажа буквы
    в группе выглядит опечаткой, и никто не поймёт, что виноват шрифт.
    """

    def words(self):
        out = ['Требуется действие', 'Проверено оператором', 'Выполнено',
               'Не выполнено', 'Обращение №42', 'неизвестно', '?', '!']
        for scenario in sc.SCENARIOS:
            out += [scenario['title'], scenario.get('group_title') or '']
            for item in scenario['steps']:
                out += [item['label'], item.get('short') or '', item.get('action') or '']
                out += list(item.get('options') or [])
            out += list(scenario.get('checks') or [])
        out += list(sc.BODY_DATA_LABELS.values())
        out += list(sc.FLAG_LABELS.values())
        # Казахские буквы: ими пишут и парк, и город, и фамилию водителя.
        out.append('Әә Ғғ Ққ Ңң Өө Ұұ Үү Һһ Іі')
        return out

    def test_all_characters_have_a_glyph(self):
        for weight in ('Regular', 'Medium', 'Bold'):
            font = ImageFont.truetype(
                '%s/Roboto-%s.ttf' % (card.FONTS_DIR, weight), 24)
            missing = sorted({ch for text in self.words() for ch in text
                              if ch.strip() and font.getmask(ch).getbbox() is None})
            self.assertEqual(missing, [], '%s: нет глифов %s' % (weight, missing))


class LongValuesWrapTest(unittest.TestCase):
    def test_a_long_answer_makes_the_card_taller_not_wider(self):
        short = answers_for('sapar_service_error')
        long = dict(short, error_text='Ошибка ' * 60)
        first = Image.open(io.BytesIO(draw('sapar_service_error', short)))
        second = Image.open(io.BytesIO(draw('sapar_service_error', long)))
        self.assertEqual(first.width, second.width)
        self.assertGreater(second.height, first.height)

    def test_a_long_heading_wraps(self):
        blocks = sc.body_blocks('sapar_sign_status', answers_for('sapar_sign_status'))
        one = card.render_ticket_card(heading='Просьба', subtitle='Обращение №1',
                                      blocks=blocks)
        many = card.render_ticket_card(heading='Просьба ' * 20, subtitle='Обращение №1',
                                       blocks=blocks)
        self.assertGreater(Image.open(io.BytesIO(many)).height,
                           Image.open(io.BytesIO(one)).height)


class WarningIsVisibleTest(unittest.TestCase):
    def test_mass_outage_adds_a_strip(self):
        without = Image.open(io.BytesIO(draw('sapar_service_error'))).height
        with_flag = Image.open(io.BytesIO(
            draw('sapar_service_error', flags=[sc.FLAG_MASS_OUTAGE]))).height
        self.assertGreater(with_flag, without)


if __name__ == '__main__':
    unittest.main()
