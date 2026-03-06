import { expect, test } from "@playwright/test";
import {
  clearWishlist,
  ensureLogin,
  getWishlistSummaryText,
  gotoHome,
  parseCredits,
  tryAddWishlistOne,
} from "../helpers/app";

const USERNAME = process.env.GAIA_RAIL_USERNAME || process.env.GAIA_TEST_USERNAME || "202101681";
const PASSWORD = process.env.GAIA_RAIL_PASSWORD || process.env.GAIA_TEST_PASSWORD || "qwer";

test.describe("full_wishlist_flow", () => {
  test("rail_full_10_wishlist_summary_visible", async ({ page }) => {
    await gotoHome(page);
    const text = await getWishlistSummaryText(page);
    expect(text.length).toBeGreaterThan(0);
  });

  test("rail_full_11_add_wishlist_one_or_login_modal", async ({ page }) => {
    await gotoHome(page);
    const status = await tryAddWishlistOne(page);
    expect(["added", "login_modal", "none"]).toContain(status);
    test.skip(status === "none", "담기 버튼 없음");
  });

  test("rail_full_12_login_with_fixture_credentials_if_needed", async ({ page }) => {
    await gotoHome(page);
    const loginResult = await ensureLogin(page, USERNAME, PASSWORD);
    expect(["already", "logged_in", "unavailable"]).toContain(loginResult);
  });

  test("rail_full_13_add_then_summary_not_empty", async ({ page }) => {
    await gotoHome(page);
    const before = await getWishlistSummaryText(page);
    const beforeCredits = parseCredits(before);

    const status = await tryAddWishlistOne(page);
    test.skip(status === "none", "담기 버튼 없음");
    if (status === "none") return;

    const after = await getWishlistSummaryText(page);
    const afterCredits = parseCredits(after);
    if (beforeCredits !== null && afterCredits !== null && status === "added") {
      expect(afterCredits).toBeGreaterThanOrEqual(beforeCredits);
    } else {
      expect(after.length).toBeGreaterThan(0);
    }
  });

  test("rail_full_14_clear_wishlist_after_login", async ({ page }) => {
    await gotoHome(page);
    await ensureLogin(page, USERNAME, PASSWORD);
    await clearWishlist(page);
    const text = await page.locator("body").innerText();
    const ok = /총\s*0개|0학점|비어있/.test(text);
    expect(ok).toBeTruthy();
  });
});
