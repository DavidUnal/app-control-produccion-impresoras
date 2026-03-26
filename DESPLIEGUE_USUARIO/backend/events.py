# backend/events.py
from __future__ import annotations
import asyncio, json
from typing import Set
from starlette.websockets import WebSocket, WebSocketDisconnect

class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active.add(ws)
        await ws.send_json({"type": "hello", "payload": {"version": "1.0"}})

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self.active.discard(ws)

    async def broadcast(self, message: dict):
        data = json.dumps(message, ensure_ascii=False)
        for ws in list(self.active):
            try:
                await ws.send_text(data)
            except Exception:
                await self.disconnect(ws)

# Manager global
manager = ConnectionManager()

# ---- Soporte para llamar desde endpoints síncronos ----
_LOOP: asyncio.AbstractEventLoop | None = None

def set_loop(loop: asyncio.AbstractEventLoop):
    """Se llama en startup para capturar el event loop del servidor."""
    global _LOOP
    _LOOP = loop

def notify(message: dict):
    """Fire-and-forget: agenda el broadcast desde endpoints sync."""
    if _LOOP is not None:
        asyncio.run_coroutine_threadsafe(manager.broadcast(message), _LOOP)
