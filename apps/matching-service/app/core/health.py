"""Liveness, readiness and metrics endpoints.

/healthz: the process can serve. It never checks dependencies (08.2).
/readyz:  every registered dependency check passes AND the process isn't shutting down.
"""

import asyncio
import inspect
import time
from collections.abc import Callable

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


class Health:
    def __init__(self, check_timeout: float = 2.0, cache_seconds: float = 2.0):
        self._checks: dict[str, tuple[Callable, bool]] = {}
        self.shutting_down = False
        self.check_timeout = check_timeout
        self.cache_seconds = cache_seconds
        self._cached: tuple[float, bool, dict[str, str]] | None = None

    def add_check(self, name: str, check: Callable) -> None:
        """A non-blocking check: returns normally (or an awaitable that does) when the dependency is usable."""
        self._checks[name] = (check, False)

    def add_blocking_check(self, name: str, check: Callable[[], None]) -> None:
        """A check that blocks (a synchronous client call). It runs in a worker thread."""
        self._checks[name] = (check, True)

    async def _run(self, check: Callable, blocking: bool) -> str:
        async def call():
            if blocking:
                return await asyncio.to_thread(check)
            result = check()
            if inspect.isawaitable(result):
                return await result
            return result

        try:
            await asyncio.wait_for(call(), self.check_timeout)
            return "ok"
        except Exception as exc:  # any failure means "not ready", with the reason
            return f"failed: {type(exc).__name__}: {exc}"[:200]

    async def readiness(self) -> tuple[bool, dict[str, str]]:
        if self.shutting_down:
            return False, {"shutdown": "in progress"}
        now = time.monotonic()
        if self._cached and now - self._cached[0] < self.cache_seconds:
            return self._cached[1], self._cached[2]
        names = list(self._checks)
        results = await asyncio.gather(*(self._run(*self._checks[n]) for n in names))
        details = dict(zip(names, results, strict=True))
        ok = all(r == "ok" for r in results)
        self._cached = (now, ok, details)
        return ok, details


def health_router(health: Health) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @router.get("/readyz")
    async def readyz():
        ok, details = await health.readiness()
        body = {"status": "ready" if ok else "unready", "checks": details}
        return JSONResponse(body, status_code=200 if ok else 503)

    @router.get("/metrics")
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return router
