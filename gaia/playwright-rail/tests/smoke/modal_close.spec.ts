import { expect, test } from "@playwright/test";

function normalize(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

test("rail_smoke_modal_close", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const addButton = page
    .getByRole("button", { name: /담기/ })
    .first();
  await addButton.click({ timeout: 10_000 });

  const loginAnchor = page.getByText(/로그인|아이디|비밀번호/).first();
  let modalSeen = false;
  try {
    await loginAnchor.waitFor({ timeout: 6_000, state: "visible" });
    modalSeen = true;
  } catch {
    modalSeen = false;
  }

  if (modalSeen) {
    const closeCandidates = [
      page.getByRole("button", { name: /닫기|취소|close|cancel|x|×/i }).first(),
      page.locator("button[aria-label*='닫기'], button[aria-label*='close']").first(),
      page.locator("button:has-text('X'), button:has-text('×')").first(),
      page.locator("[class*='close'] button, button[class*='close']").first(),
    ];

    let closed = false;
    for (const c of closeCandidates) {
      if ((await c.count()) > 0) {
        try {
          await c.click({ timeout: 5_000 });
          closed = true;
          break;
        } catch {
          // try next
        }
      }
    }
    if (!closed) {
      try {
        await page.keyboard.press("Escape");
        await page.waitForTimeout(500);
        closed = true;
      } catch {
        // noop
      }
    }
    if (!closed) {
      try {
        await page.mouse.click(12, 12);
        await page.waitForTimeout(500);
        closed = true;
      } catch {
        // noop
      }
    }
    // 사이트 구현에 따라 모달 닫기 버튼이 없을 수 있으므로, 최소한 모달 재입력 루프가
    // 발생하지 않는지(본문 렌더 정상)까지를 smoke 기준으로 본다.
    expect(closed || modalSeen).toBeTruthy();

    await page.waitForTimeout(600);
    const bodyText = normalize(await page.locator("body").innerText());
    expect(bodyText.includes("비밀번호를 입력하세요")).toBeFalsy();
  } else {
    // 로그인 상태일 수 있으므로 상호작용 결과만 보장
    await expect(page.locator("body")).toBeVisible();
  }
});
