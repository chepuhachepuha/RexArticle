"""
Модуль логирования.
Перехватывает stdout/stderr и пишет всё в файл + консоль.
"""
import sys
import os
import datetime
from typing import TextIO


class LogCapture:
    """
    Перехватчик stdout/stderr.
    Пишет в файл И в оригинальную консоль одновременно.
    """
    
    def __init__(self, log_file: str, original_stream: TextIO):
        self.log_file = log_file
        self.original_stream = original_stream
        self.encoding = getattr(original_stream, 'encoding', 'utf-8') or 'utf-8'
        
        # Создаём директорию если её нет
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    def write(self, text: str):
        """Записывает текст в файл и в консоль."""
        # 1. В оригинальную консоль
        try:
            self.original_stream.write(text)
            self.original_stream.flush()
        except Exception:
            pass
        
        # 2. В файл (с timestamp для каждой строки)
        if text.strip():
            try:
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    # Добавляем timestamp к каждой непустой строке
                    for line in text.splitlines(keepends=True):
                        if line.strip():
                            f.write(f"[{timestamp}] {line}")
                        else:
                            f.write(line)
            except Exception as e:
                # Если не можем писать в файл — молча пропускаем
                pass
        else:
            # Пустые строки тоже пишем (для форматирования)
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(text)
            except Exception:
                pass
    
    def flush(self):
        """Обязательный метод для file-like объектов."""
        try:
            self.original_stream.flush()
        except Exception:
            pass
    
    def isatty(self):
        """Для совместимости с некоторыми библиотеками."""
        return False


def setup_logging(log_dir: str = None, log_filename: str = "app.log"):
    if log_dir is None:
        from core.paths import get_data_dir
        log_dir = os.path.join(get_data_dir(), "logs")
    """
    Настраивает перехват stdout/stderr в файл логов.
    
    Вызывается один раз при старте приложения.
    
    Args:
        log_dir: Папка для логов
        log_filename: Имя файла лога
    """
    # Создаём папку для логов
    os.makedirs(log_dir, exist_ok=True)
    
    # Путь к файлу лога
    log_file = os.path.join(log_dir, log_filename)
    
    # Очищаем старый лог при каждом запуске (опционально)
    # Можно закомментировать если нужно сохранять историю
    try:
        if os.path.exists(log_file):
            # Архивируем старый лог
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(log_dir, f"app_{timestamp}.log")
            # Не архивируем если файл маленький (<100KB)
            if os.path.getsize(log_file) > 100 * 1024:
                os.rename(log_file, backup_file)
            else:
                os.remove(log_file)
    except Exception:
        pass
    
    # Устанавливаем перехватчики
    sys.stdout = LogCapture(log_file, sys.__stdout__)
    sys.stderr = LogCapture(log_file, sys.__stderr__)
    
    # Первое сообщение в лог
    print(f"=== Логирование запущено ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")


def get_log_file_path() -> str:
    """Возвращает путь к файлу логов."""
    from core.paths import get_data_dir
    return os.path.join(get_data_dir(), "logs", "app.log")
