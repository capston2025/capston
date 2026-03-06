import { expect, test } from "@playwright/test";
import {
  clickNextPaginationIfExists,
  findCreditSelect,
  findFirstSelectWithMinOptions,
  gotoHome,
} from "../helpers/app";

test.describe("full_filter_matrix", () => {
  test("rail_full_15_credit_options_enumeration", async ({ page }) => {
    await gotoHome(page);
    const credit = await findCreditSelect(page);
    test.skip(!credit, "학점 필터 없음");
    if (!credit) return;
    const options = (await credit.locator("option").allTextContents())
      .map((v) => v.trim())
      .filter(Boolean);
    expect(options.length).toBeGreaterThan(1);
  });

  test("rail_full_16_credit_filter_1_2_3_transitions", async ({ page }) => {
    await gotoHome(page);
    const credit = await findCreditSelect(page);
    test.skip(!credit, "학점 필터 없음");
    if (!credit) return;

    const options = (await credit.locator("option").allTextContents()).map((v) => v.trim());
    const targets = ["1학점", "2학점", "3학점"].filter((t) => options.some((o) => o.includes(t)));
    test.skip(targets.length === 0, "1/2/3학점 옵션 없음");
    if (!targets.length) return;

    for (const target of targets) {
      await credit.selectOption({ label: target });
      await page.waitForTimeout(600);
      const value = await credit.inputValue();
      expect(value).toBeTruthy();
    }
  });

  test("rail_full_17_secondary_select_changes_value", async ({ page }) => {
    await gotoHome(page);
    const select = await findFirstSelectWithMinOptions(page, 3);
    test.skip(!select, "다중 옵션 select 없음");
    if (!select) return;
    const options = await select.locator("option").allTextContents();
    const target = options.map((v) => v.trim()).find((v) => v && !/전체|all/i.test(v));
    test.skip(!target, "타겟 옵션 없음");
    if (!target) return;
    await select.selectOption({ label: target });
    await page.waitForTimeout(500);
    const value = await select.inputValue();
    expect(value).toBeTruthy();
  });

  test("rail_full_18_pagination_persistence_smoke", async ({ page }) => {
    await gotoHome(page);
    const credit = await findCreditSelect(page);
    test.skip(!credit, "학점 필터 없음");
    if (!credit) return;
    const options = await credit.locator("option").allTextContents();
    const target = options.map((v) => v.trim()).find((v) => /1\s*학점|2\s*학점|3\s*학점/.test(v));
    test.skip(!target, "학점 옵션 없음");
    if (!target) return;

    await credit.selectOption({ label: target });
    await page.waitForTimeout(700);
    const moved = await clickNextPaginationIfExists(page);
    test.skip(!moved, "페이지네이션 없음");
    if (!moved) return;

    const after = await credit.inputValue();
    expect(after).toBeTruthy();
  });

  test("rail_full_19_responsive_mobile_smoke", async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    await gotoHome(page);
    const hasCore =
      (await page.locator("button:has-text('담기')").count()) > 0 ||
      (await page.locator("button:has-text('바로 추가')").count()) > 0 ||
      (await page.locator("button:has-text('로그인'), a:has-text('로그인')").count()) > 0 ||
      (await page.locator("text=내 시간표").count()) > 0;
    expect(hasCore).toBeTruthy();
    await context.close();
  });

  test("rail_full_20_timetable_panel_presence", async ({ page }) => {
    await gotoHome(page);
    const text = await page.locator("body").innerText();
    const hasPanel = /내 시간표|1교시|월|화|수|목|금/.test(text);
    expect(hasPanel).toBeTruthy();
  });
});
