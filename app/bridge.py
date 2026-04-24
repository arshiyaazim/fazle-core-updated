"""
Fazle Core — Bridge Client
Wraps HTTP calls to both QR WhatsApp bridges.
"""
import logging
import httpx
from app.config import get_settings

log = logging.getLogger("fazle.bridge")


class BridgeClient:
    def __init__(self, base_url: str, label: str):
        self.base_url = base_url.rstrip("/")
        self.label = label
        self._client = httpx.AsyncClient(timeout=15.0)

    async def _set_send(self, allow: bool):
        try:
            await self._client.post(
                f"{self.base_url}/api/send-control",
                json={"allow": allow},
                timeout=5.0,
            )
        except Exception:
            pass

    async def send(self, jid: str, text: str) -> bool:
        """Send a single message. Auto-toggles send permission."""
        if not jid.endswith("@s.whatsapp.net") and not jid.endswith("@g.us"):
            jid = jid + "@s.whatsapp.net"
        try:
            await self._set_send(True)
            r = await self._client.post(
                f"{self.base_url}/api/send",
                json={"recipient": jid, "message": text},
            )
            return r.status_code == 200
        except Exception as e:
            log.error(f"[{self.label}] send error to {jid}: {e}")
            return False
        finally:
            await self._set_send(False)

    async def send_multi(self, jid: str, messages: list[str]) -> bool:
        if not jid.endswith("@s.whatsapp.net") and not jid.endswith("@g.us"):
            jid = jid + "@s.whatsapp.net"
        try:
            await self._set_send(True)
            for msg in messages:
                await self._client.post(
                    f"{self.base_url}/api/send",
                    json={"recipient": jid, "message": msg},
                )
            return True
        except Exception as e:
            log.error(f"[{self.label}] send_multi error: {e}")
            return False
        finally:
            await self._set_send(False)

    async def status(self) -> dict:
        try:
            r = await self._client.get(f"{self.base_url}/api/send-status", timeout=5.0)
            return r.json()
        except Exception:
            return {"allowed": False, "error": "unreachable"}

    async def close(self):
        await self._client.aclose()


# Singleton instances — created once at startup
_bridge1: BridgeClient | None = None
_bridge2: BridgeClient | None = None


def get_bridge1() -> BridgeClient:
    global _bridge1
    if _bridge1 is None:
        s = get_settings()
        _bridge1 = BridgeClient(s.bridge1_url, "BR1")
    return _bridge1


def get_bridge2() -> BridgeClient:
    global _bridge2
    if _bridge2 is None:
        s = get_settings()
        _bridge2 = BridgeClient(s.bridge2_url, "BR2")
    return _bridge2
