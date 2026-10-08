"""Own background tasks explicitly so cog reloads do not leave orphaned work."""

import asyncio


class TaskSupervisor:
    def __init__(self, logger):
        self.logger = logger
        self.tasks: set[asyncio.Task] = set()
        self.closed = False

    def spawn(self, coroutine, *, name: str):
        if self.closed:
            coroutine.close()
            raise RuntimeError("PRIME AI background task supervisor is closed")
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task):
        self.tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.logger.error(
                "PRIME AI background task %s failed.", task.get_name(),
                exc_info=(type(error), error, error.__traceback__),
            )

    async def close(self):
        self.closed = True
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.difference_update(tasks)
