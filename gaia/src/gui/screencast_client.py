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
        """WebSocket 연결 및 프레임 수신.

        백엔드(Playwright agent)가 안 떠 있는 경우가 잦으므로 로그를 조용히 유지.
        최초 시도 한 번만 연결을 시도하고, 실패하면 조용히 끝낸다.
        """
        max_retries = 1  # 시끄러운 5회 재시도 → 한 번만 시도
        retry_count = 0
        connected_at_least_once = False

        while self._running and retry_count < max_retries:
            try:
                async with websockets.connect(
                    self.ws_url,
                    open_timeout=2.0,  # 빠르게 포기 (기본 10초 → 2초)
                ) as websocket:
                    self._websocket = websocket
                    self.connection_status_changed.emit(True)
                    if not connected_at_least_once:
                        print(f"[Screencast] Connected to {self.ws_url}")
                        connected_at_least_once = True
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
                            try:
                                await websocket.send("ping")
                            except Exception:
                                break
                        except websockets.exceptions.ConnectionClosed:
                            break
                        except Exception:
                            # 프레임 수신 실패는 조용히 끝
                            break

            except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
                # Playwright 에이전트가 안 떠 있는 정상 케이스 — 조용히 종료
                retry_count += 1
                self.connection_status_changed.emit(False)
                if retry_count >= max_retries:
                    # 재시도 다 썼으면 error_occurred는 emit하지 않음 (조용히)
                    break
                # (max_retries=1이라 여기 도달 안 함, 향후 늘릴 때를 위해 둠)
                await asyncio.sleep(2)

            except Exception:
                # 알 수 없는 에러도 조용히 종료
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
            except Exception:
                pass
        self.wait()  # 스레드 종료 대기
