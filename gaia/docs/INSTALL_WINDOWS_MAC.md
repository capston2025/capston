# GAIA 설치 가이드 (Windows/macOS)

## 공통
```bash
python -m pip install -e .
python -m playwright install chromium
python -m gaia.cli --help
```

## macOS
- 포트 점유 확인:
```bash
lsof -ti tcp:8001 | xargs kill -9
```
- 실행:
```bash
python -m gaia.cli
```

## Windows (PowerShell)
- UTF-8 권장:
```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```
- 실행:
```powershell
python -m pip install -e .
python -m playwright install chromium
python -m gaia.cli
```

## 자주 발생하는 오류
- `No module named gaia.cli`: 프로젝트 루트에서 `python -m pip install -e .` 재실행
- `No module named fastapi`: 의존성 재설치
- `MCP host 자동 시작 실패`: 포트 8001 점유 프로세스 종료 후 재시작
