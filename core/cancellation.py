class OperationCancelled(RuntimeError):
    pass


class CancellationToken:
    def __init__(self):
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled

    def is_canceled(self):
        return self._cancelled

    def raise_if_cancelled(self):
        if self._cancelled:
            raise OperationCancelled("Операция отменена")
