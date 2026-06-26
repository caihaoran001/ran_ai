import { expect, test } from "@playwright/test";

const adminPages = [
  { path: "/dashboard", heading: "数据看板" },
  { path: "/users", heading: "用户管理" },
  { path: "/documents", heading: "审核控制台" },
  { path: "/questionnaire-config", heading: "运营规则配置" },
  { path: "/system-diagnostics", heading: "系统诊断控制台" },
];

test.describe("admin console smoke", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
    if (await page.locator("#username").isVisible()) {
      await page.fill("#username", process.env.ADMIN_USERNAME || "admin");
      await page.fill("#password", process.env.ADMIN_PASSWORD || "change-me");
      await page.click('button[type="submit"]');
      await page.waitForURL((url) => !url.pathname.endsWith("/login"));
    }
  });

  for (const pageInfo of adminPages) {
    test(`${pageInfo.path} loads`, async ({ page }) => {
      await page.goto(pageInfo.path);
      await expect(page.getByRole("heading", { name: pageInfo.heading })).toBeVisible();
    });
  }

  test("system diagnostics renders health suites", async ({ page }) => {
    await page.goto("/system-diagnostics");

    await expect(page.getByRole("heading", { name: "安全健康检查" })).toBeVisible();
    await expect(page.getByText("intake")).toBeVisible();
    await expect(page.getByText("outcome_engine")).toBeVisible();
  });
});

