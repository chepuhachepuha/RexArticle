"""Единые операции над локальными и облачными ресурсами."""
import os
import shutil

from core.yandex_disk import YandexDiskClient, is_yandex_uri


def copy_resource(source: str, destination: str, *, yandex_client=None) -> str:
    """Копирует локальный файл или скачивает yadisk URI в destination."""
    if is_yandex_uri(source):
        client = yandex_client or YandexDiskClient()
        return client.download_uri(source, destination)
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    shutil.copy2(source, destination)
    return destination
