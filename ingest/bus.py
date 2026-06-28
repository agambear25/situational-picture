"""
Message bus abstraction. NullBus for hermetic eval; NatsBus for production.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class NullBus:
    """No-op bus — used by the eval harness and any hermetic run."""

    def publish(self, subject: str, payload: dict) -> None:
        logger.debug("NullBus drop: %s %s", subject, payload)


class NatsBus:
    """Synchronous wrapper over NATS JetStream publish. Lazy-imports nats."""

    def __init__(self, servers: list[str], stream: str = "cop_events"):
        self._servers = servers
        self._stream = stream
        self._nc = None

    def _ensure(self):
        if self._nc is None:
            import asyncio
            import nats  # lazy
            self._loop = asyncio.new_event_loop()
            self._nc = self._loop.run_until_complete(nats.connect(servers=self._servers))

    def publish(self, subject: str, payload: dict) -> None:
        import json
        self._ensure()
        self._loop.run_until_complete(
            self._nc.publish(subject, json.dumps(payload, default=str).encode())
        )
