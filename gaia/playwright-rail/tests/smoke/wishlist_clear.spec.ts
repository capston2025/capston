import { expect, test } from "@playwright/test";

test("rail_smoke_wishlist_clear", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const expandButton = page.getByRole("button", { name: /위시리스트 확장 보기|위시리스트/i }).first();
  if ((await expandButton.count()) > 0) {
    try {
      await expandButton.click({ timeout: 5_000 });
      await page.waitForTimeout(500);
    } catch {
      // no-op
    }
  }

  for (let i = 0; i < 20; i += 1) {
    const removeBtn = page.getByRole("button", { name: /제거|삭제|remove/i }).first();
    if ((await removeBtn.count()) === 0) {
      break;
    }
    try {
      await removeBtn.click({ timeout: 3_000 });
      await page.waitForTimeout(200);
    } catch {
      break;
    }
  }

  const bodyText = await page.locator("body").innerText();
  const emptied =
    /총\s*0개/.test(bodyText) ||
    /0학점/.test(bodyText) ||
    /비어있/.test(bodyText);
  expect(emptied).toBeTruthy();
});
