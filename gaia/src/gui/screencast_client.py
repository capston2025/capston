"""
CDP screencast WebSocket client for the GUI preview.
"""

import asyncio
import json
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Optional

import websockets
from PySide6.QtCore import QThread, Signal


class ScreencastClient(QThread):
    """Receive browser screencast frames over WebSocket."""

    frame_received = Signal(str)
    connection_status_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self, ws_url: str = "ws://localhost:8001/ws/screencast", parent=None):
        super().__init__(parent)
        self.ws_url = ws_url
        self._running = False
        self._websocket = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def run(self):
        """Thread entrypoint."""
        self._running = True
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._connect_and_listen())
        except Exception as exc:
            self.error_occurred.emit(f"Screencast error: {exc}")
        finally:
            self._loop = None
            loop.close()

    async def _connect_and_listen(self):
        retry_count = 0
        max_retries = 5

        while self._running and retry_count < max_retries:
            try:
                async with websockets.connect(self.ws_url) as websocket:
                    self._websocket = websocket
                    self.connection_status_changed.emit(True)
                    print(f"[Screencast] Connected to {self.ws_url}")
                    retry_count = 0

                    await websocket.send("get_current_frame")

                    while self._running:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                            data = json.loads(message)

                            if data.get("type") == "screencast_frame":
                                frame_base64 = data.get("frame")
                                if frame_base64:
                                    self.frame_received.emit(frame_base64)

                        except asyncio.TimeoutError:
                            await websocket.send("ping")
                        except websockets.exceptions.ConnectionClosed:
                            print("[Screencast] Connection closed by server")
                            break
                        except Exception as exc:
                            print(f"[Screencast] Error receiving frame: {exc}")
                            break

            except (ConnectionRefusedError, OSError) as exc:
                retry_count += 1
                self.connection_status_changed.emit(False)
                print(f"[Screencast] Connection failed (attempt {retry_count}/{max_retries}): {exc}")

                if retry_count < max_retries:
                    await asyncio.sleep(min(2**retry_count, 10))
                else:
                    self.error_occurred.emit(f"Failed to connect after {max_retries} attempts")
                    break

            except Exception as exc:
                self.error_occurred.emit(f"Unexpected error: {exc}")
                break

        self.connection_status_changed.emit(False)
        self._websocket = None

    def stop(self):
        """Stop the websocket client thread safely."""
        self._running = False

        if self._websocket and self._loop and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._websocket.close(), self._loop)
                future.result(timeout=2)
            except FutureTimeoutError:
                pass
            except Exception:
                pass

        self.wait(3000)
