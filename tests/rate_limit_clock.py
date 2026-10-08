"""Manually advanced monotonic time for admission tests; no real quota sleeps."""

import asyncio


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []
        self._changed = asyncio.Event()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in self._sleepers:
            if deadline <= self.now and not future.done():
                future.set_result(None)

    async def sleep(self, seconds: float) -> None:
        future = asyncio.get_running_loop().create_future()
        entry = (self.now + seconds, future)
        self._sleepers.append(entry)
        self._changed.set()
        try:
            await future
        finally:
            self._sleepers.remove(entry)
            self._changed.set()

    async def wait_for_sleepers(self, count: int = 1) -> None:
        async def wait() -> None:
            while sum(not future.done() for _, future in self._sleepers) < count:
                self._changed.clear()
                await self._changed.wait()

        await asyncio.wait_for(wait(), timeout=2)
