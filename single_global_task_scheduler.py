import param
import panel as pn
import time
import threading
from typing import Callable


class SingleGlobalTaskRunner(param.Parameterized):
    """The GlobalTaskRunner creates a singleton instance for each key."""
    value = param.Parameter(doc="The most recent result", label="Last Result", constant=True)
    exception: Exception = param.ClassSelector(
        class_=Exception,
        allow_None=True,
        doc="The most recent exception, if any",
        label="Last Exception",
        constant=True,
    )
    worker: Callable = param.Callable(
        allow_None=False, doc="Function that generates a result"
    )
    seconds: float = param.Number(
        default=1.0, doc="Interval between worker calls", bounds=(0.001, None)
    )
    key: str = param.String(allow_None=False, constant=True)

    _single_global_task_runner_key = "__single_global_task_runners__"

    def __init__(self, key: str, **params):
        super().__init__(key=key, **params)
        if hasattr(self, "_thread") and self._thread.is_alive():
            return
        self._stop_thread = False
        self._thread = threading.Thread(target=self._task_runner, daemon=False)
        self._thread.start()
        self._log("Created")

    def __new__(cls, key, **kwargs):
        task_runners = pn.state.cache[cls._single_global_task_runner_key] = pn.state.cache.get(
            cls._single_global_task_runner_key, {}
        )
        task_runner = task_runners.get(key, None)

        if not task_runner:
            task_runner = super(SingleGlobalTaskRunner, cls).__new__(cls)
            task_runners[key] = task_runner

        return task_runner

    def _log(self, message):
        print(f"{id(self)} - {message}")

    def _task_runner(self):
        while not self._stop_thread:
            try:
                result = self.worker()
                with param.edit_constant(self):
                    self.value = result
                    self.exception = None
            except Exception as ex:
                with param.edit_constant(self):
                    self.exception = ex
            if not self._stop_thread:
                self._log("Sleeping")
                time.sleep(self.seconds)

        self._log("Task Runner Finished")

    def remove(self):
        """Securely stops and removes the GlobalThreadWorker."""
        self._log("Removing")
        self._stop_thread = True
        self._thread.join()

        cache = pn.state.cache.get(self._single_global_task_runner_key, {})
        if self.key in cache:
            del cache[self.key]
        self._log("Removed")

    @classmethod
    def remove_all(cls):
        """Securely stops and removes all GlobalThreadWorkers."""
        for gtw in list(pn.state.cache.get(cls._single_global_task_runner_key, {}).values()):
            gtw.remove()
        pn.state.cache[cls._single_global_task_runner_key] = {}
