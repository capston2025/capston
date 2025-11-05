"""
CDP 스크린캐스트 WebSocket 클라이언트
실시간 브라우저 화면을 WebSocket으로 수신하여 GUI에 표시합니다.
"""
import asyncio
import websockets
import json
from typing import Callable, Optional
from PySide6.QtCore import QThread, Signal


class ScreencastClient(QThread):
    """
    WebSocket을 통해 CDP 스크린캐스트 프레임을 수신하는 스레드
    """
    frame_received = Signal(str)  # base64 인코딩된 JPEG 프레임
    connection_status_changed = Signal(bool)  # True: 연결됨, False: 연결 끊김
    error_occurred = Signal(str)

    def __init__(self, ws_url: str = "ws://localhost:8001/ws/screencast", parent=None):
        super().__init__(parent)
        self.ws_url = ws_url
        self._running = False
        self._websocket = None

    def run(self):
        """스레드 메인 루프 - WebSocket 연결 및 프레임 수신"""
        self._running = True

        # 새 이벤트 루프 생성 (스레드 안전)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._connect_and_listen())
        except Exception as e:
            self.error_occurred.emit(f"Screencast error: {str(e)}")
        finally:
            loop.close()

    async def _connect_and_listen(self):
        """WebSocket 연결 및 프레임 수신"""
        retry_count = 0
        max_retries = 5

        while self._running and retry_count < max_retries:
            try:
                async with websockets.connect(self.ws_url) as websocket:
                    self._websocket = websocket
                    self.connection_status_changed.emit(True)
                    print(f"[Screencast] Connected to {self.ws_url}")
                    retry_count = 0  # 연결 성공 시 재시도 카운트 초기화

                    # 첫 프레임 요청
                    await websocket.send("get_current_frame")

                    # 프레임 수신 루프
                    while self._running:
                        try:
                            message = await asyncio.wait_for(
                                websocket.recv(),
                                timeout=30.0  # 30초 타임아웃
                            )

                            data = json.loads(message)

                            if data.get('type') == 'screencast_frame':
                                frame_base64 = data.get('frame')
                                if frame_base64:
                                    # Qt 시그널로 프레임 전송
                                    self.frame_received.emit(frame_base64)

                        except asyncio.TimeoutError:
                            # 타임아웃 시 ping 전송
                            await websocket.send("ping")
                        except websockets.exceptions.ConnectionClosed:
                            print("[Screencast] Connection closed by server")
                            break
                        except Exception as e:
                            print(f"[Screencast] Error receiving frame: {e}")
                            break

            except (ConnectionRefusedError, OSError) as e:
                retry_count += 1
                self.connection_status_changed.emit(False)
                print(f"[Screencast] Connection failed (attempt {retry_count}/{max_retries}): {e}")

                if retry_count < max_retries:
                    # 재연결 대기 (지수 백오프)
                    wait_time = min(2 ** retry_count, 10)
                    await asyncio.sleep(wait_time)
                else:
                    self.error_occurred.emit(f"Failed to connect after {max_retries} attempts")
                    break

            except Exception as e:
                self.error_occurred.emit(f"Unexpected error: {str(e)}")
                break

        self.connection_status_changed.emit(False)
        self._websocket = None

    def stop(self):
        """WebSocket 연결 종료"""
        self._running = False
        if self._websocket:
            # asyncio 태스크로 종료 처리
            try:
                asyncio.create_task(self._websocket.close())
            except:
                pass
        self.wait()  # 스레드 종료 대기
