# QA Automation Desktop

PyQt6-based native client that extends the existing QA automation backend with full OS-level control. The scaffolding is split into UI, core coordination, services, and system integration layers so each phase of the port can land incrementally.

## Project structure

```
desktop_app/
├── app/
│   ├── __init__.py
│   ├── __main__.py
│   ├── main.py              # Application entry point
│   ├── core/
│   │   └── controller.py    # UI/controller wiring, web view coordination
│   ├── ui/
│   │   └── main_window.py   # Main window, drag & drop, embedded browser
│   ├── services/
│   │   ├── __init__.py
│   │   ├── backend_bridge.py# FastAPI 모듈을 직접 호출하는 브리지
│   │   └── pdf_service.py   # Checklist extraction facade (stub)
│   ├── system/
│   │   └── input_controller.py # PyAutoGUI wrapper for OS control
│   └── workers/
│       └── playwright_worker.py # Background automation worker (stub)
├── pyproject.toml
└── README.md
```

Each sub-package matches the Phase plan:

- **UI** – Qt widgets, including an embedded `QWebEngineView` so 테스트 진행 상황을 실시간으로 노출.
- **Core** – glue logic, dependency injection, worker lifecycle, 브라우저 URL 관리.
- **Services** – pure Python helpers reusing FastAPI code without HTTP calls.
- **System** – PyAutoGUI/pygetwindow adapters.
- **Workers** – long-running tasks executed off the UI thread (Playwright, AI calls).

## Getting started

```bash
cd desktop_app
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .
python -m app                        # Launch the UI
```

The current implementation stubs backend functionality so the UI can be verified without touching the server code. Replace the placeholders in `services/` and `workers/` as Phase 2+ land.

## Next steps

1. Replace `PDFService.extract_checklist` with FastAPI 모듈을 이용한 실제 PDF→체크리스트 변환 및 URL 추출.
2. `BackendBridge`를 이용해 DOM 분석 + 테스트 시나리오 생성을 비동기로 호출하고, 결과를 UI 체크리스트와 브라우저에 반영.
3. Playwright 워커를 임베디드 WebView와 동기화하여 DOM 하이라이트/로그 스트리밍을 구현.
4. `InputController`에 창 포커싱/스크롤/단축키 기능을 추가해 사용자가 보는 화면과 자동화 입력이 일치하도록 유지.
5. 기능이 안정되면 PyInstaller 스펙과 플랫폼별 배포 스크립트를 추가.
