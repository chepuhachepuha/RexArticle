"""Unified operations over local and cloud resources."""
import os
import shutil

from core.yandex_disk import YandexDiskClient, is_yandex_uri


def copy_resource(source: str, destination: str, *, yandex_client=None, cancel_token=None) -> str:
    """Copy/download a resource atomically, with cooperative cancellation."""
    if cancel_token:
        cancel_token.raise_if_cancelled()
    if is_yandex_uri(source):
        client = yandex_client or YandexDiskClient()
        result = client.download_uri(source, destination)
        if cancel_token:
            cancel_token.raise_if_cancelled()
        return result

    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    temporary = destination + ".part"
    try:
        with open(source, "rb") as input_file, open(temporary, "wb") as output_file:
            while True:
                if cancel_token:
                    cancel_token.raise_if_cancelled()
                chunk = input_file.read(1024 * 1024)
                if not chunk:
                    break
                output_file.write(chunk)
            output_file.flush()
            os.fsync(output_file.fileno())
        shutil.copystat(source, temporary)
        if cancel_token:
            cancel_token.raise_if_cancelled()
        os.replace(temporary, destination)
        return destination
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise