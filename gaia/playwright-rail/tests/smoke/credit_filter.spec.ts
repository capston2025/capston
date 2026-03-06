import { expect, test } from "@playwright/test";

async function findCreditSelect(page: any) {
  const selects = page.locator("select");
  const count = await selects.count();
  for (let i = 0; i < count; i += 1) {
    const sel = selects.nth(i);
    const optionText = (await sel.locator("option").allTextContents()).join(" ");
    if (/학점|credit/i.test(optionText)) {
      return sel;
    }
  }
  return null;
}

test("rail_smoke_credit_filter_semantic", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const creditSelect = await findCreditSelect(page);
  expect(creditSelect).not.toBeNull();
  if (!creditSelect) {
    return;
  }

  const options = await creditSelect.locator("option").allTextContents();
  const target = options.find((v) => /1\s*학점/.test(v)) || options.find((v) => /2\s*학점/.test(v));
  expect(Boolean(target)).toBeTruthy();
  if (!target) {
    return;
  }

  await creditSelect.selectOption({ label: target.trim() });
  await page.waitForTimeout(800);

  const bodyText = await page.locator("body").innerText();
  expect(bodyText).toContain(target.trim());
});
