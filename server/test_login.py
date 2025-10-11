#!/usr/bin/env python3
"""
자동 생성된 로그인 테스트 코드
AI 기반 UI 요소 분석으로 생성됨
"""

import asyncio
import json
import sys
from datetime import datetime
from playwright.async_api import async_playwright, Page, Browser

class TestLogger:
    """테스트 실행 로그를 관리하는 클래스"""
    
    def __init__(self):
        self.logs = []
        self.start_time = datetime.now()
    
    def log(self, level: str, message: str, details: dict = None):
        """로그 기록"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message,
            "details": details or {}
        }
        self.logs.append(log_entry)
        
        # 콘솔에도 출력
        timestamp = datetime.now().strftime("%H:%M:%S")
        emoji = {"INFO": "ℹ️", "SUCCESS": "✅", "ERROR": "❌", "WARNING": "⚠️"}.get(level, "📝")
        print(f"[{timestamp}] {emoji} {message}")
        
        if details:
            for key, value in details.items():
                print(f"    {key}: {value}")
    
    def get_summary(self):
        """테스트 실행 요약 반환"""
        total_time = (datetime.now() - self.start_time).total_seconds()
        success_count = len([log for log in self.logs if log["level"] == "SUCCESS"])
        error_count = len([log for log in self.logs if log["level"] == "ERROR"])
        
        return {
            "total_time": f"{total_time:.2f}초",
            "total_steps": len(self.logs),
            "success_count": success_count,
            "error_count": error_count,
            "success_rate": f"{(success_count / len(self.logs) * 100):.1f}%" if self.logs else "0%"
        }

class LoginTester:
    """로그인 기능 자동 테스트 클래스"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.logger = TestLogger()
        self.browser = None
        self.page = None
    
    async def setup(self):
        """테스트 환경 설정"""
        self.logger.log("INFO", "테스트 환경 설정 중...")
        
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(
            headless=True,
            args=['--disable-web-security', '--disable-features=VizDisplayCompositor']
        )
        
        self.page = await self.browser.new_page()
        
        # 페이지 로딩 대기 시간 설정
        self.page.set_default_timeout(10000)
        
        self.logger.log("SUCCESS", "브라우저 시작 완료")
    
    async def teardown(self):
        """테스트 환경 정리"""
        if self.browser:
            await self.browser.close()
            self.logger.log("INFO", "브라우저 종료 완료")
    
    async def navigate_to_login_page(self):
        """로그인 페이지로 이동"""
        self.logger.log("INFO", f"로그인 페이지 접속 중: {self.base_url}")
        
        try:
            await self.page.goto(self.base_url, wait_until='domcontentloaded')
            
            # 페이지 제목 확인
            title = await self.page.title()
            self.logger.log("SUCCESS", "로그인 페이지 로딩 완료", {"title": title})
            
            return True
        except Exception as e:
            self.logger.log("ERROR", f"페이지 로딩 실패: {str(e)}")
            return False
    
    async def fill_login_form(self, username: str, password: str):
        """로그인 폼 입력"""
        self.logger.log("INFO", "로그인 폼 입력 시작", {
            "username": username,
            "password": "***숨김***"
        })
        
        try:
            # 사용자명 입력
            await self.page.fill('#username', username)
            self.logger.log("SUCCESS", "사용자명 입력 완료")
            
            # 비밀번호 입력
            await self.page.fill('#password', password)
            self.logger.log("SUCCESS", "비밀번호 입력 완료")
            
            return True
        except Exception as e:
            self.logger.log("ERROR", f"폼 입력 실패: {str(e)}")
            return False
    
    async def click_login_button(self):
        """로그인 버튼 클릭"""
        self.logger.log("INFO", "로그인 버튼 클릭 중...")
        
        try:
            await self.page.click('button:has-text(" Login")')
            self.logger.log("SUCCESS", "로그인 버튼 클릭 완료")
            
            # 페이지 변화 대기 (최대 5초)
            await self.page.wait_for_load_state('networkidle', timeout=5000)
            
            return True
        except Exception as e:
            self.logger.log("ERROR", f"로그인 버튼 클릭 실패: {str(e)}")
            return False
    
    async def verify_login_result(self, expected_success: bool = True):
        """로그인 결과 검증"""
        self.logger.log("INFO", "로그인 결과 검증 중...")
        
        try:
            current_url = self.page.url
            page_content = await self.page.content()
            
            if expected_success:
                # 성공 시 URL 변화나 성공 메시지 확인
                if "secure" in current_url or "welcome" in page_content.lower():
                    self.logger.log("SUCCESS", "로그인 성공 확인됨", {"url": current_url})
                    return True
                else:
                    self.logger.log("ERROR", "로그인 성공이 예상되었으나 실패", {"url": current_url})
                    return False
            else:
                # 실패 시 에러 메시지나 동일 페이지 확인
                if "invalid" in page_content.lower() or current_url == self.base_url:
                    self.logger.log("SUCCESS", "예상된 로그인 실패 확인됨")
                    return True
                else:
                    self.logger.log("ERROR", "로그인 실패가 예상되었으나 성공", {"url": current_url})
                    return False
                    
        except Exception as e:
            self.logger.log("ERROR", f"결과 검증 실패: {str(e)}")
            return False

# 테스트 시나리오들
TEST_SCENARIOS = [
    {
        "id": "TC_001",
        "priority": "High",
        "scenario": "정상 로그인 테스트",
        "username": "tomsmith",
        "password": "SuperSecretPassword!",
        "expected_success": True
    },
    {
        "id": "TC_002", 
        "priority": "High",
        "scenario": "잘못된 비밀번호로 로그인 실패 테스트",
        "username": "tomsmith",
        "password": "wrongpassword",
        "expected_success": False
    },
    {
        "id": "TC_003",
        "priority": "Medium", 
        "scenario": "존재하지 않는 사용자명으로 로그인 실패 테스트",
        "username": "nonexistentuser",
        "password": "password123",
        "expected_success": False
    },
    {
        "id": "TC_004",
        "priority": "Medium",
        "scenario": "빈 필드로 로그인 실패 테스트", 
        "username": "",
        "password": "",
        "expected_success": False
    }
]

async def run_all_tests():
    """모든 테스트 시나리오 실행"""
    print("=" * 60)
    print("🧪 AI 기반 자동 로그인 테스트 시작")
    print("=" * 60)
    print()
    
    base_url = "https://the-internet.herokuapp.com/login"
    tester = LoginTester(base_url)
    
    # 전체 결과 수집
    all_results = []
    
    try:
        # 테스트 환경 설정
        await tester.setup()
        
        # 각 시나리오 실행
        for i, scenario in enumerate(TEST_SCENARIOS, 1):
            print(f"📋 테스트 시나리오 {i}/4: {scenario['id']}")
            print(f"   설명: {scenario['scenario']}")
            print(f"   우선순위: {scenario['priority']}")
            print()
            
            scenario_start_time = datetime.now()
            scenario_success = True
            
            # 1. 페이지 이동
            if not await tester.navigate_to_login_page():
                scenario_success = False
            
            # 2. 폼 입력 (성공한 경우에만)
            if scenario_success:
                if not await tester.fill_login_form(scenario['username'], scenario['password']):
                    scenario_success = False
            
            # 3. 로그인 버튼 클릭 (성공한 경우에만)
            if scenario_success:
                if not await tester.click_login_button():
                    scenario_success = False
            
            # 4. 결과 검증 (성공한 경우에만)
            if scenario_success:
                if not await tester.verify_login_result(scenario['expected_success']):
                    scenario_success = False
            
            # 시나리오 결과 기록
            scenario_time = (datetime.now() - scenario_start_time).total_seconds()
            result = {
                "scenario_id": scenario['id'],
                "scenario_name": scenario['scenario'],
                "success": scenario_success,
                "execution_time": f"{scenario_time:.2f}초"
            }
            all_results.append(result)
            
            print(f"   결과: {'✅ 성공' if scenario_success else '❌ 실패'}")
            print(f"   실행시간: {scenario_time:.2f}초")
            print("-" * 40)
            print()
        
    finally:
        await tester.teardown()
    
    # 최종 결과 리포트
    print("=" * 60)
    print("📊 테스트 실행 결과 요약")
    print("=" * 60)
    
    summary = tester.logger.get_summary()
    successful_scenarios = len([r for r in all_results if r['success']])
    total_scenarios = len(all_results)
    
    print(f"🎯 시나리오 성공률: {successful_scenarios}/{total_scenarios} ({(successful_scenarios/total_scenarios*100):.1f}%)")
    print(f"⏱️  총 실행시간: {summary['total_time']}")
    print(f"📈 총 실행단계: {summary['total_steps']}개")
    print(f"✅ 성공단계: {summary['success_count']}개")
    print(f"❌ 실패단계: {summary['error_count']}개")
    print()
    
    print("📋 시나리오별 상세 결과:")
    for result in all_results:
        status = "✅ 성공" if result['success'] else "❌ 실패"
        print(f"  {result['scenario_id']}: {status} ({result['execution_time']})")
    
    print()
    print("🎉 테스트 실행 완료!")
    
    # JSON 로그 파일 저장
    log_data = {
        "test_summary": summary,
        "scenario_results": all_results,
        "detailed_logs": tester.logger.logs
    }
    
    with open('test_execution_log.json', 'w', encoding='utf-8') as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)
    
    print(f"📁 상세 로그가 test_execution_log.json에 저장되었습니다.")

if __name__ == "__main__":
    # 테스트 실행
    asyncio.run(run_all_tests())