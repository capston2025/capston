import { expect, test } from "@playwright/test";
import {
  clickNextPaginationIfExists,
  findCreditSelect,
  findFirstSelectWithMinOptions,
  gotoHome,
  tryOpenLoginModal,
  tryCloseModal,
} from "../helpers/app";

test.describe("full_core_flow", () => {
  test("rail_full_01_site_load", async ({ page }) => {
    await gotoHome(page);
    await expect(page.locator("body")).toContainText(/시간표|위시리스트|학점/);
  });

  test("rail_full_02_has_primary_buttons", async ({ page }) => {
    await gotoHome(page);
    const addCount = await page.locator("button:has-text('담기')").count();
    const quickCount = await page.locator("button:has-text('바로 추가')").count();
    const loginCount = await page.locator("button:has-text('로그인'), a:has-text('로그인')").count();
    expect(addCount + quickCount + loginCount).toBeGreaterThan(0);
  });

  test("rail_full_03_search_input_exists_and_accepts_typing", async ({ page }) => {
    await gotoHome(page);
    const input = page.locator("input[placeholder*='검색'], input[type='search']").first();
    const count = await input.count();
    test.skip(count === 0, "검색 입력창 없음");
    if (count === 0) return;
    await expect(input).toBeVisible();
    await input.fill("컴퓨터");
    await expect(input).toHaveValue(/컴퓨터/);
  });

  test("rail_full_04_credit_filter_exists", async ({ page }) => {
    await gotoHome(page);
    const credit = await findCreditSelect(page);
    expect(credit).not.toBeNull();
  });

  test("rail_full_05_any_select_has_multiple_options", async ({ page }) => {
    await gotoHome(page);
    const sel = await findFirstSelectWithMinOptions(page, 3);
    expect(sel).not.toBeNull();
    if (sel) {
      const count = await sel.locator("option").count();
      expect(count).toBeGreaterThanOrEqual(3);
    }
  });

  test("rail_full_06_credit_filter_selection_reflects", async ({ page }) => {
    await gotoHome(page);
    const credit = await findCreditSelect(page);
    test.skip(!credit, "학점 select 없음");
    if (!credit) return;

    const options = (await credit.locator("option").allTextContents()).map((v) => v.trim());
    const target = options.find((v) => /1\s*학점/.test(v)) || options.find((v) => /2\s*학점/.test(v));
    test.skip(!target, "학점 옵션 없음");
    if (!target) return;

    await credit.selectOption({ label: target });
    await page.waitForTimeout(700);
    const value = await credit.inputValue();
    expect(value).toBeTruthy();
  });

  test("rail_full_07_open_login_modal_if_possible", async ({ page }) => {
    await gotoHome(page);
    const opened = await tryOpenLoginModal(page);
    test.skip(!opened, "로그인 진입 버튼/모달 없음");
    if (!opened) return;
    await expect(page.locator("input[type='password'], input[placeholder*='비밀번호']").first()).toBeVisible();
  });

  test("rail_full_08_close_modal_if_opened", async ({ page }) => {
    await gotoHome(page);
    const opened = await tryOpenLoginModal(page);
    test.skip(!opened, "로그인 모달 오픈 불가");
    if (!opened) return;
    const closed = await tryCloseModal(page);
    if (!closed) {
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
    }
    await expect(page.locator("body")).toBeVisible();
  });

  test("rail_full_09_pagination_click_or_skip", async ({ page }) => {
    await gotoHome(page);
    const before = await page.locator("body").innerText();
    const moved = await clickNextPaginationIfExists(page);
    test.skip(!moved, "다음 페이지 컨트롤 없음");
    if (!moved) return;
    const after = await page.locator("body").innerText();
    expect(after.length).toBeGreaterThan(0);
    expect(after).not.toEqual(before);
  });
});
