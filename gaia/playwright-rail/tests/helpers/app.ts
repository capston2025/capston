import { expect, Page, test } from "@playwright/test";

export async function gotoHome(page: Page): Promise<void> {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.locator("body")).toBeVisible();
}

export async function clickIfVisible(page: Page, candidates: string[]): Promise<boolean> {
  for (const q of candidates) {
    const loc = page.locator(q).first();
    if ((await loc.count()) > 0) {
      try {
        await loc.click({ timeout: 4000 });
        return true;
      } catch {
        // continue
      }
    }
  }
  return false;
}

export async function tryOpenLoginModal(page: Page): Promise<boolean> {
  const clicked = await clickIfVisible(page, [
    "button:has-text('로그인')",
    "button:has-text('Login')",
    "a:has-text('로그인')",
    "a:has-text('Login')",
  ]);
  if (!clicked) return false;
  try {
    await page.locator("input[placeholder*='아이디'], input[placeholder*='ID'], input[type='password']").first().waitFor({ timeout: 5000 });
    return true;
  } catch {
    return false;
  }
}

export async function tryCloseModal(page: Page): Promise<boolean> {
  const ok = await clickIfVisible(page, [
    "button[aria-label*='닫기']",
    "button[aria-label*='close']",
    "button:has-text('닫기')",
    "button:has-text('취소')",
    "button:has-text('X')",
    "button:has-text('×')",
    "button[class*='close']",
    "[class*='close'] button",
  ]);
  if (!ok) return false;
  await page.waitForTimeout(500);
  return true;
}

export async function ensureLogin(page: Page, username: string, password: string): Promise<"already" | "logged_in" | "unavailable"> {
  const hasLogout = (await page.locator("button:has-text('로그아웃'), a:has-text('로그아웃')").count()) > 0;
  if (hasLogout) return "already";

  const opened = await tryOpenLoginModal(page);
  if (!opened) return "unavailable";

  const userInput = page.locator("input[placeholder*='아이디'], input[placeholder*='ID'], input[type='text']").first();
  const passInput = page.locator("input[type='password'], input[placeholder*='비밀번호']").first();
  await userInput.fill(username);
  await passInput.fill(password);

  const submitted = await clickIfVisible(page, [
    "button:has-text('로그인')",
    "button:has-text('Login')",
    "button[type='submit']",
  ]);
  if (!submitted) return "unavailable";
  await page.waitForTimeout(1200);

  return "logged_in";
}

export async function findCreditSelect(page: Page) {
  const selects = page.locator("select");
  const count = await selects.count();
  for (let i = 0; i < count; i += 1) {
    const sel = selects.nth(i);
    const all = (await sel.locator("option").allTextContents()).join(" ");
    if (/학점|credit/i.test(all)) return sel;
  }
  return null;
}

export async function findFirstSelectWithMinOptions(page: Page, minOptions = 3) {
  const selects = page.locator("select");
  const count = await selects.count();
  for (let i = 0; i < count; i += 1) {
    const sel = selects.nth(i);
    const optCount = await sel.locator("option").count();
    if (optCount >= minOptions) return sel;
  }
  return null;
}

export async function getWishlistSummaryText(page: Page): Promise<string> {
  const body = await page.locator("body").innerText();
  const line = body
    .split("\n")
    .map((v) => v.trim())
    .find((v) => /위시리스트|총\s*\d+개|학점/.test(v));
  return line || "";
}

export function parseCredits(text: string): number | null {
  const m = text.match(/(\d+)\s*학점/);
  if (!m) return null;
  return Number(m[1]);
}

export async function tryAddWishlistOne(page: Page): Promise<"added" | "login_modal" | "none"> {
  const btn = page.locator("button:has-text('담기')").first();
  if ((await btn.count()) === 0) return "none";
  await btn.click({ timeout: 7000 });
  await page.waitForTimeout(700);
  const hasLoginInput = (await page.locator("input[type='password'], input[placeholder*='비밀번호']").count()) > 0;
  if (hasLoginInput) return "login_modal";
  return "added";
}

export async function clearWishlist(page: Page): Promise<void> {
  for (let i = 0; i < 30; i += 1) {
    const removeBtn = page.locator("button:has-text('제거'), button:has-text('삭제'), button:has-text('remove')").first();
    if ((await removeBtn.count()) === 0) break;
    try {
      await removeBtn.click({ timeout: 3000 });
      await page.waitForTimeout(200);
    } catch {
      break;
    }
  }
}

export async function clickNextPaginationIfExists(page: Page): Promise<boolean> {
  const candidates = [
    "button:has-text('다음')",
    "a:has-text('다음')",
    "button[aria-label*='다음']",
    "a[aria-label*='다음']",
    "button:has-text('>')",
    "a:has-text('>')",
  ];
  for (const c of candidates) {
    const loc = page.locator(c).first();
    if ((await loc.count()) > 0) {
      try {
        await loc.click({ timeout: 5000 });
        await page.waitForTimeout(700);
        return true;
      } catch {
        // continue
      }
    }
  }
  return false;
}

export function skipIf(condition: boolean, reason: string): void {
  test.skip(condition, reason);
}
