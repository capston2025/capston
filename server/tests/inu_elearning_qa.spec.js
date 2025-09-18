// 인천대학교 이러닝 시스템 웹 QA 테스트
// 작성일: 2025.09.18
// 작성자: 홍길동

import { test, expect } from '@playwright/test';

// 테스트 설정
const BASE_URL = 'https://cyber.inu.ac.kr';
const VALID_USERNAME = 'testuser'; // 실제 테스트용 계정으로 변경 필요
const VALID_PASSWORD = 'testpass'; // 실제 테스트용 비밀번호로 변경 필요
const INVALID_USERNAME = 'invaliduser';
const INVALID_PASSWORD = 'wrongpass';

test.describe('인천대학교 이러닝 시스템 QA 테스트', () => {
  
  test.beforeEach(async ({ page }) => {
    // 각 테스트 전에 메인 페이지로 이동
    await page.goto(BASE_URL);
  });

  test.describe('로그인 기능 테스트', () => {
    
    test('TC_001: 유효한 아이디/비밀번호로 로그인 성공', async ({ page }) => {
      // 로그인 페이지로 이동 또는 로그인 폼 찾기
      await page.click('text=로그인');
      
      // 아이디 입력
      await page.fill('#username', VALID_USERNAME);
      // 또는 다른 셀렉터: await page.fill('input[name="username"]', VALID_USERNAME);
      
      // 비밀번호 입력
      await page.fill('#password', VALID_PASSWORD);
      // 또는 다른 셀렉터: await page.fill('input[name="password"]', VALID_PASSWORD);
      
      // 로그인 버튼 클릭
      await page.click('button:has-text("로그인")');
      // 또는 다른 셀렉터: await page.click('#login-button');
      
      // 성공적으로 로그인되어 메인 페이지로 이동했는지 확인
      await expect(page).toHaveURL(/.*\/main|.*\/dashboard|.*\/home/);
      
      // 로그인 성공 후 나타나는 요소 확인 (예: 사용자 메뉴, 로그아웃 버튼 등)
      await expect(page.locator('text=로그아웃')).toBeVisible();
    });

    test('TC_002: 유효하지 않은 아이디로 로그인 실패', async ({ page }) => {
      // 로그인 페이지로 이동
      await page.click('text=로그인');
      
      // 잘못된 아이디 입력
      await page.fill('#username', INVALID_USERNAME);
      await page.fill('#password', VALID_PASSWORD);
      
      // 로그인 버튼 클릭
      await page.click('button:has-text("로그인")');
      
      // 오류 메시지 확인
      await expect(page.locator('text=아이디 또는 비밀번호가 올바르지 않습니다')).toBeVisible();
      // 또는: await expect(page.locator('.error-message')).toContainText('아이디 또는 비밀번호');
    });

    test('TC_003: 빈칸으로 로그인 시도 시 경고 메시지', async ({ page }) => {
      // 로그인 페이지로 이동
      await page.click('text=로그인');
      
      // 아무것도 입력하지 않고 로그인 버튼 클릭
      await page.click('button:has-text("로그인")');
      
      // 경고 메시지 확인
      await expect(page.locator('text=아이디와 비밀번호를 입력해주세요')).toBeVisible();
      // 또는: await expect(page.locator('.warning-message')).toContainText('입력해주세요');
    });

    test('TC_004: 유효하지 않은 비밀번호로 로그인 실패', async ({ page }) => {
      // 로그인 페이지로 이동
      await page.click('text=로그인');
      
      // 올바른 아이디, 잘못된 비밀번호 입력
      await page.fill('#username', VALID_USERNAME);
      await page.fill('#password', INVALID_PASSWORD);
      
      // 로그인 버튼 클릭
      await page.click('button:has-text("로그인")');
      
      // 오류 메시지 확인
      await expect(page.locator('text=아이디 또는 비밀번호가 올바르지 않습니다')).toBeVisible();
    });
  });

  test.describe('강좌 접근 테스트', () => {
    
    test.beforeEach(async ({ page }) => {
      // 강좌 접근 테스트를 위해 먼저 로그인
      await page.click('text=로그인');
      await page.fill('#username', VALID_USERNAME);
      await page.fill('#password', VALID_PASSWORD);
      await page.click('button:has-text("로그인")');
      
      // 로그인 완료 대기
      await expect(page.locator('text=로그아웃')).toBeVisible();
    });

    test('TC_005: 폭력예방교육 강좌 접근', async ({ page }) => {
      // 강좌 목록에서 특정 강좌 클릭
      const courseTitle = '2025년 "학과(부)생" 대상 폭력예방교육';
      await page.click(`text=${courseTitle}`);
      // 또는: await page.click('.course-item:has-text("폭력예방교육")');
      
      // 해당 강의실 페이지로 이동했는지 확인
      await expect(page).toHaveURL(/.*\/course|.*\/lecture/);
      
      // 강의실 페이지의 특정 요소 확인
      await expect(page.locator('text=강의실')).toBeVisible();
      // 또는: await expect(page.locator('.course-content')).toBeVisible();
    });
  });

  test.describe('페이지 이동 테스트', () => {
    
    test.beforeEach(async ({ page }) => {
      // 페이지 이동 테스트를 위해 먼저 로그인
      await page.click('text=로그인');
      await page.fill('#username', VALID_USERNAME);
      await page.fill('#password', VALID_PASSWORD);
      await page.click('button:has-text("로그인")');
      
      // 로그인 완료 대기
      await expect(page.locator('text=로그아웃')).toBeVisible();
    });

    test('TC_006: 강의실에서 홈 버튼 클릭하여 메인 페이지 이동', async ({ page }) => {
      // 먼저 강좌에 접근
      const courseTitle = '2025년 "학과(부)생" 대상 폭력예방교육';
      await page.click(`text=${courseTitle}`);
      
      // 강의실 페이지 로딩 대기
      await expect(page.locator('text=강의실')).toBeVisible();
      
      // 홈 버튼 클릭
      await page.click('text=홈');
      // 또는: await page.click('#home-button');
      // 또는: await page.click('.home-btn');
      
      // 메인 페이지로 돌아갔는지 확인
      await expect(page).toHaveURL(/.*\/main|.*\/home|\/$|\/$/);
    });

    test('TC_007: 로그인 후 뒤로가기 버튼 동작 확인', async ({ page }) => {
      // 현재 URL 저장
      const currentUrl = page.url();
      
      // 다른 페이지로 이동 (예: 강좌 목록)
      await page.click('text=강좌');
      
      // 뒤로가기 버튼 클릭
      await page.goBack();
      
      // 홈 화면으로 이동하거나 오류가 발생하지 않는지 확인
      // 이전 페이지로 돌아가지 않고 홈으로 이동하는 것이 정상 동작
      await expect(page).toHaveURL(/.*\/main|.*\/home/);
      
      // 또는 오류가 발생하지 않았는지 확인
      const errorElements = page.locator('.error, .alert-danger, [class*="error"]');
      await expect(errorElements).toHaveCount(0);
    });
  });

  test.describe('추가 안정성 테스트', () => {
    
    test('TC_008: 페이지 로딩 성능 확인', async ({ page }) => {
      const startTime = Date.now();
      
      await page.goto(BASE_URL);
      
      // 페이지 로딩이 5초 이내에 완료되는지 확인
      const loadTime = Date.now() - startTime;
      expect(loadTime).toBeLessThan(5000);
      
      // 주요 요소들이 로딩되었는지 확인
      await expect(page.locator('text=로그인')).toBeVisible({ timeout: 5000 });
    });

    test('TC_009: 반응형 디자인 확인 (모바일)', async ({ page }) => {
      // 모바일 뷰포트로 변경
      await page.setViewportSize({ width: 375, height: 667 });
      
      await page.goto(BASE_URL);
      
      // 모바일에서도 로그인 버튼이 보이는지 확인
      await expect(page.locator('text=로그인')).toBeVisible();
    });
  });
});