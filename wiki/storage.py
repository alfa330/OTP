"""Файл в бакет и запись в wiki_files.

Одно место на всех, кто грузит. Тот же код жил замыканием в routes_import и
знал ровно один случай — картинку из редактора статьи; логотип парка приезжает
другим роутом с другим гейтом, а кладётся туда же. Второй экземпляр этих
десяти строк разошёлся бы с первым на первой же правке пути в бакете — и
разошёлся бы молча.

Здесь же картинка переводится в WebP (wiki/images.py). Место выбрано ровно по
той же причине: дверей загрузки три (редактор, импорт документа, логотип
парка), и правило «в бакете лежит WebP» не должно зависеть от того, в какую из
них вошли.
"""

from . import articles as wiki_articles
from . import images as wiki_images
from . import importer as wiki_importer


def store_file(cursor, gcs, *, data, filename, content_type, uploaded_by,
               article_id=None):
    """Кладёт файл и заводит запись: (file_id, постоянный адрес).

    (None, None) — хранилище не настроено (нет бакета); отвечать на это должен
    вызывающий, потому что текст ошибки у каждой двери свой.

    Адрес постоянный, а не подписанный: подпись живёт часы, а статья и
    справочник — годами. Подпись выдаёт роут /file/<id> на каждый запрос.
    """
    bucket = gcs['bucket_name']() if gcs.get('bucket_name') else None
    if not bucket:
        return None, None

    # Картинка ложится в бакет одним форматом. Не получилось (SVG, битый файл,
    # нет Pillow) — кладём как принесли: отказать в загрузке из-за конвертера
    # хуже, чем сохранить исходный формат. Заодно отсюда берутся размеры кадра:
    # колонки width/height в wiki_files есть с самого начала и до сих пор
    # заполнялись значением NULL.
    width = height = None
    converted = wiki_images.to_webp(data, content_type)
    if converted:
        data, content_type, width, height = converted
        # Имя меняем по ФАКТУ формата, а не по факту вызова: конвертер вправе
        # вернуть исходные байты (уже WebP, или пережатие вышло только в минус),
        # и назвать тогда файл «.webp» значило бы соврать про содержимое.
        if content_type == 'image/webp':
            filename = wiki_images.webp_name(filename)

    blob_path = wiki_importer.blob_path_for(filename, content_type)
    gcs['client']().bucket(bucket).blob(blob_path).upload_from_string(
        data, content_type=content_type or 'application/octet-stream')

    file_id = wiki_articles.register_file(
        cursor, article_id=article_id, bucket=bucket, blob_path=blob_path,
        original_name=str(filename or 'file')[:255],
        content_type=content_type, file_size=len(data),
        width=width, height=height, uploaded_by=uploaded_by,
    )
    return file_id, '/api/wiki/file/%s' % file_id
