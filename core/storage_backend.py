"""Unified operations over local and cloud resources."""
import os
import shutil
import time

from core.network_paths import is_network_path, network_operation
from core.yandex_disk import YandexDiskClient, is_yandex_uri


class NetworkReadError(OSError):
    """A mounted network file could not be read after all retries."""


def _copy_local_file(
    source, destination, temporary, *, cancel_token=None, preserve_metadata=True,
):
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
    if preserve_metadata:
        shutil.copystat(source, temporary)
    if cancel_token:
        cancel_token.raise_if_cancelled()
    os.replace(temporary, destination)


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

    temporary = destination + ".part"
    source_network = is_network_path(source)
    destination_network = is_network_path(destination)
    network = source_network or destination_network
    network_path = source if source_network else destination
    attempts = 4 if network else 1
    last_error = None
    with network_operation(network_path):
        os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
        for attempt in range(attempts):
            try:
                _copy_local_file(
                    source, destination, temporary,
                    cancel_token=cancel_token,
                    preserve_metadata=not network,
                )
                return destination
            except OSError as error:
                last_error = error
                try:
                    os.remove(temporary)
                except OSError:
                    pass
                if not network or attempt + 1 >= attempts:
                    break
                delay = 2 ** attempt
                print(
                    f"[WEBDAV] Ошибка чтения файла {source}: "
                    f"попытка {attempt + 1}/{attempts}, "
                    f"повтор через {delay} с ({error})"
                )
                time.sleep(delay)
            except Exception:
                try:
                    os.remove(temporary)
                except OSError:
                    pass
                raise
    if network:
        raise NetworkReadError(
            f"WebDAV-операция не завершена после {attempts} попыток: "
            f"{source} → {destination} ({last_error})"
        ) from last_error
    raise last_error
