"""Одна long-poll задача на попытку в процессе API; состояние хранится в БД."""
import threading
from functools import wraps

_running = set()
_lock = threading.Lock()


def single_attempt(function):
    @wraps(function)
    def run(*args):
        key = (function.__name__, args[-1])
        with _lock:
            if key in _running:
                return
            _running.add(key)
        try:
            return function(*args)
        finally:
            with _lock:
                _running.discard(key)
    return run
