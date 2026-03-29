"""Quick test runner for the evidence_reacquire fix."""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from gaia.terminal import run_chat_terminal_once

url = "https://inuu-timetable.vercel.app/"
query = "포용사회와문화탐방1 과목의 '바로 추가' 버튼을 눌러서 내 시간표에 반영되는지 테스트하고, 이미 추가되어 있던 경우 삭제 후 다시 추가되는지 확인"

code, summary = run_chat_terminal_once(url=url, query=query)

print("\n" + "=" * 60)
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
print("=" * 60)
print(f"Exit code: {code}")
