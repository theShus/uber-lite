"""SIGTERM handling: readiness goes to 503 first, then uvicorn drains and stops.

uvicorn installs its own SIGTERM handler. We wrap it, so the pod reports "unready"
the moment the signal arrives. With SHUTDOWN_DELAY_SECONDS > 0 the process keeps
serving that long before uvicorn stops accepting connections, which closes the
endpoint-removal race without a preStop hook (11.3). The default is 0, because
the chart's preStop sleep already covers it.
"""

import asyncio
import logging
import signal
import threading

from .health import Health

log = logging.getLogger("lifecycle")


def install_shutdown_hook(health: Health, delay_seconds: float = 0.0) -> None:
    if threading.current_thread() is not threading.main_thread():
        return  # test clients run the app in a worker thread; signals only work in the main thread
    loop = asyncio.get_running_loop()
    previous = signal.getsignal(signal.SIGTERM)

    def on_sigterm(signum, frame):
        if health.shutting_down:
            return
        health.shutting_down = True
        log.info("SIGTERM received: readiness is now 503", extra={"delay_s": delay_seconds})
        if callable(previous):
            if delay_seconds > 0:
                loop.call_soon_threadsafe(loop.call_later, delay_seconds, previous, signum, frame)
            else:
                previous(signum, frame)

    signal.signal(signal.SIGTERM, on_sigterm)
