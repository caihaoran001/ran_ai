import { expect, test } from "@playwright/test";

/**
 * FAQ 确认按钮 HTTP 契约测试（全程模拟接口，不连后端、不碰数据库）。
 *
 * 背景：确认接口要求必填请求体 { expected_version }，前端曾漏传导致所有
 * 确认操作 422。本用例在浏览器层拦截真实点击发出的请求，锁定契约：
 *   1. 点击「确认当前版本」必须提交且仅提交 { expected_version: <所显版本> }
 *   2. 成功响应后界面翻转为「已确认」
 */
const FAQ_ID = 9001;
const QUESTION = "E2E契约测试：午B建议最晚几点前喝完？";
const LIST_URL = /\/api\/backend\/knowledge-faqs\?/;
const CONFIRM_URL = new RegExp(`/api/backend/knowledge-faqs/${FAQ_ID}/confirm$`);

function faqPayload(state: "pending" | "confirmed") {
  return {
    id: FAQ_ID,
    title: QUESTION,
    question: QUESTION,
    answer: "午B尽量在下午2点前喝完。",
    category: "产品使用",
    tags: ["午B", "服用时间"],
    status: "active",
    version: 3,
    confirmation_state: state,
    last_confirmed_version: state === "confirmed" ? 3 : null,
    last_confirmed_at: state === "confirmed" ? "2026-08-24T16:41:00Z" : null,
    disabled_at: null,
    archived_at: null,
    created_at: "2026-08-24T15:00:00Z",
    updated_at: "2026-08-24T16:41:00Z",
  };
}

test.describe("FAQ 确认 HTTP 契约", () => {
  test("确认当前版本必须提交 {expected_version} 并翻转为已确认", async ({ page }) => {
    // dev 模式首次编译较慢，放宽单测超时。
    test.setTimeout(180_000);

    let faqListCalls = 0;
    await page.route(LIST_URL, async (route) => {
      faqListCalls += 1;
      await route.fulfill({
        json: faqListCalls === 1 ? [faqPayload("pending")] : [faqPayload("confirmed")],
      });
    });

    let confirmCalls = 0;
    await page.route(CONFIRM_URL, async (route) => {
      confirmCalls += 1;
      const body = route.request().postDataJSON();
      expect(body).toEqual({ expected_version: 3 });
      await route.fulfill({ json: faqPayload("confirmed") });
    });

    // 登录完全由 Next 本地校验（环境变量），不依赖任何后端。
    await page.goto("/login");
    await page.fill("#username", process.env.ADMIN_USERNAME || "admin");
    await page.fill("#password", process.env.ADMIN_PASSWORD || "change-me");
    await page.click('button[type="submit"]');
    await page.waitForURL((url) => !url.pathname.endsWith("/login"));

    await page.goto("/documents");
    await page.getByRole("tab", { name: /FAQ 库/ }).click();
    // 问题文本在卡片中出现两次（标题+常见问法），用按钮做唯一锚点。
    const confirmButton = page.getByRole("button", { name: "确认当前版本" });
    await expect(confirmButton).toBeVisible();
    await expect(page.getByText("待确认", { exact: true })).toBeVisible();

    await confirmButton.click();

    // 成功回调触发列表重取（第二次 GET 返回 confirmed 态），徽章与按钮同步翻转。
    await expect(page.getByText("已确认", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "取消确认" })).toBeVisible();
    expect(confirmCalls).toBe(1);
    expect(faqListCalls).toBeGreaterThanOrEqual(2);
  });
});
