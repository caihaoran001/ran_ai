import { expect, test } from "@playwright/test";

const adminPages = [
  { path: "/dashboard", heading: "数据看板" },
  { path: "/users", heading: "用户管理" },
  { path: "/documents", heading: "审核控制台" },
  { path: "/questionnaire-config", heading: "运营规则配置" },
  { path: "/agent-operations", heading: "AI 助教运行情况" },
  { path: "/rag-evaluations", heading: "AI 回复质量" },
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

  test("AI reply quality exposes the fixed corpus and per-case evidence", async ({ page }) => {
    await page.goto("/rag-evaluations");

    const aiManagement = page.getByRole("navigation", { name: "AI 管理栏目" });
    await expect(aiManagement.getByRole("link", { name: "运行监控" })).toBeVisible();
    await expect(aiManagement.getByRole("link", { name: "回复质量" })).toHaveAttribute("aria-current", "page");
    const sidebar = page.locator("aside");
    await expect(sidebar.getByText("系统", { exact: true })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "AI 管理" })).toHaveAttribute("aria-current", "page");
    await expect(sidebar.getByRole("link", { name: "RAG 评测" })).toHaveCount(0);
    await expect(sidebar.getByRole("link", { name: "系统诊断" })).toHaveCount(0);

    const corpus = page.getByRole("region", { name: "固定题库构成" });
    await expect(corpus).toContainText("FAQ 改写");
    await expect(corpus).toContainText("真实聊天");
    await expect(corpus).toContainText("应该检索");
    await expect(corpus).toContainText("应该跳过");
    await expect(page.getByText("本批次共 50 条", { exact: false })).toBeVisible();

    await page.getByRole("button", { name: "未通过 1" }).click();
    const failedCase = page.getByRole("button", { name: /瓶盖一盖就可以了吗/ });
    if ((await failedCase.getAttribute("aria-expanded")) !== "true") {
      await failedCase.click();
    }
    await expect(failedCase).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("heading", { name: "预期结果" })).toBeVisible();
    await expect(page.getByText("回答依据的权威事实")).toBeVisible();
    await expect(page.getByText("模拟回复", { exact: true })).toBeVisible();
    await expect(page.getByText("最终回复反转了瓶盖与大刻度的比较方向")).toBeVisible();
  });

  test("AI management stays visible in the mobile navigation", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/rag-evaluations");

    const activeItem = page.locator('header nav a[aria-current="page"]');
    await expect(activeItem).toHaveText("AI 管理");
    await expect(activeItem).toBeInViewport();
  });
});
