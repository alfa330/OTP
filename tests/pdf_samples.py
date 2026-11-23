# -*- coding: utf-8 -*-
"""Настоящие, безобидные PDF для тестов: одна пустая страница A4.

Проверка загрузок разбирает PDF целиком, и «%PDF-1.4» с нулями после него
теперь честно не принимается как повреждённый файл. Здесь — минимальные
документы, которые открывает любой просмотрщик: обычная таблица ссылок
(pdf, pdf_of_size), объекты, сжатые в поток ObjStm (pdf_with_object_stream), и
объект внутри данных чужого потока (pdf_with_object_in_stream_data).
extra — свои ключи в словарь страницы: так тест видит, докуда доходит обход.
"""
import zlib

_HEADER = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'


def _page(extra=b''):
    return b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] ' + extra + b' >>'


def _bodies(extra=b''):
    return [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>', _page(extra)]


def pdf(extra=b'', padding=0, more=()):
    """PDF с обычной таблицей ссылок. padding — строка-комментарий такой длины
    после заголовка; more — ещё объекты (4, 5, …), на которые никто не ссылается."""
    out = bytearray(_HEADER)
    if padding:
        out += b'%' + b'x' * (padding - 2) + b'\n'
    offsets = []
    for number, body in enumerate(_bodies(extra) + list(more), 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    return _with_table(out, offsets)


def _with_table(out, offsets):
    start = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(offsets) + 1)
    out += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(offsets) + 1, start)
    return bytes(out)


def pdf_with_object_in_stream_data(extra):
    """Объект 5 со словарём extra лежит внутри данных потока 4, а таблица
    ссылок указывает прямо на него: просмотрщик его найдёт."""
    out = bytearray(_HEADER)
    offsets = []
    for number, body in enumerate(_bodies(), 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    inner = b'5 0 obj\n<< ' + extra + b' >>\nendobj\n'
    offsets.append(len(out))
    out += b'4 0 obj\n<< /Length %d >>\nstream\n' % len(inner)
    offsets.append(len(out))
    out += inner + b'\nendstream\nendobj\n'
    return _with_table(out, offsets)


def pdf_of_size(size):
    """Настоящий PDF ровно в size байт — для проверок пределов размера."""
    data = pdf()
    if size == len(data):
        return data
    if size < len(data) + 2:
        raise ValueError('PDF меньше %d байт не собрать' % (len(data) + 2))
    padding = size - len(data)
    for _attempt in range(3):  # число после startxref растёт вместе с комментарием
        data = pdf(padding=padding)
        if len(data) == size:
            return data
        padding += size - len(data)
    raise ValueError('PDF ровно в %d байт не собрался' % size)


def pdf_with_object_stream(extra=b''):
    """PDF 1.5: каталог и страницы сжаты в поток объектов, таблица ссылок —
    тоже поток. В сырых байтах ключей страницы не видно."""
    bodies = _bodies(extra)
    offsets, content = [], b''
    for body in bodies:
        offsets.append(len(content))
        content += body + b'\n'
    header = b' '.join(b'%d %d' % (number, offset) for number, offset in enumerate(offsets, 1)) + b'\n'
    packed = zlib.compress(header + content)
    out = bytearray(_HEADER.replace(b'1.4', b'1.5'))
    stream_at = len(out)
    out += (b'4 0 obj\n<< /Type /ObjStm /N 3 /First %d /Filter /FlateDecode /Length %d >>\nstream\n'
            % (len(header), len(packed)) + packed + b'\nendstream\nendobj\n')
    xref_at = len(out)
    rows = [(0, 0, 65535), (2, 4, 0), (2, 4, 1), (2, 4, 2), (1, stream_at, 0), (1, xref_at, 0)]
    table = zlib.compress(b''.join(bytes((kind,)) + a.to_bytes(4, 'big') + b.to_bytes(2, 'big')
                                   for kind, a, b in rows))
    out += (b'5 0 obj\n<< /Type /XRef /Size 6 /W [1 4 2] /Root 1 0 R /Filter /FlateDecode /Length %d >>\n'
            b'stream\n' % len(table) + table + b'\nendstream\nendobj\n')
    out += b'startxref\n%d\n%%%%EOF\n' % xref_at
    return bytes(out)
