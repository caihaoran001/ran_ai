import { expect, test } from "@playwright/test";

const adminPages = [
  { path: "/dashboard", heading: "数据看板" },
  { path: "/users", heading: "用户管理" },
  { path: "/documents", heading: "审核控制台" },
  { path: "/questionnaire-config", heading: "运营规则配置" },
  { path: "/system-diagnostics", heading: "系统诊断控制台" },
];

test.describe("admin console smoke", () => {
  for (const pageInfo of adminPages) {
    test(`${pageInfo.path} loads`, async ({ page }) => {
      await page.goto(pageInfo.path);
      await expect(page.getByRole("heading", { name: pageInfo.heading })).toBeVisible();
    });
  }

  test("system diagnostics separates health suites and real E2E", async ({ page }) => {
    await page.goto("/system-diagnostics");

    await expect(page.getByText("安全健康检查")).toBeVisible();
    await expect(page.getByText("真实模型 E2E")).toBeVisible();
    await expect(page.getByText("test_01_intake")).toBeVisible();
    await expect(page.getByText("test_08_outcome_engine")).toBeVisible();
    await expect(page.getByText("test_e2e_01_real_llm")).toBeVisible();
  });
});
