import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { resolve, join } from "node:path";

test("upload → Core generation → history → actual audio playback; mobile layout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "让阅读，有回声。" }),
  ).toBeVisible();
  await page
    .getByLabel("上传电子书")
    .setInputFiles({
      name: "浏览器演示.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(
        "Chapter 1\nDivision of labour improves skill through repeated practice.\nChapter 2\nExchange depends on trust and cooperation.",
      ),
    });
  await page.getByRole("radio", { name: /双人播客/ }).click();
  await page.getByLabel("目标时长").fill("40");
  const submitted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/jobs") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成播客" }).click();
  const job = await (await submitted).json();
  expect(job.mode).toBe("two_host");
  expect(job.minutes).toBe(40);
  const audio = page.getByLabel("播客音频");
  await expect(audio).toBeVisible({ timeout: 30_000 });
  await expect
    .poll(() =>
      audio.evaluate((element: HTMLAudioElement) => element.readyState),
    )
    .toBeGreaterThanOrEqual(1);
  await audio.evaluate((element: HTMLAudioElement) => element.play());
  await expect
    .poll(() =>
      audio.evaluate((element: HTMLAudioElement) => element.currentTime),
    )
    .toBeGreaterThan(0);
  await audio.evaluate((element: HTMLAudioElement) => element.pause());
  await page.getByText("产物与任务信息").click();
  await expect(page.locator("details code")).toContainText("output");
  await expect(
    page.getByText("当前内置 TTS 生成测试音调", { exact: false }),
  ).toBeVisible();
  await page.screenshot({ path: "test-results/desktop.png", fullPage: true });
  await page.reload();
  await expect(page.getByLabel("播客音频")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "生成播客" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "test-results/mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("real quota failure → UI resume → no repeated completed chapter", async ({
  page,
  request,
}) => {
  const upload = await request.post("/api/uploads?filename=recovery.txt", {
    headers: { "Content-Type": "text/plain" },
    data: "Chapter 1\nFirst point.\nChapter 2\nSecond point.",
  });
  const { upload_id } = await upload.json();
  const job = JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      ["e2e/seed_failure.py", process.env.BOOKCAST_E2E_DATA!, upload_id],
      { encoding: "utf8" },
    ),
  );
  const chapter = join(job.directory, "analysis/chunks/0001-0001.json");
  const before = readFileSync(chapter);
  const modified = statSync(chapter).mtimeMs;
  await page.goto("/");
  await expect(page.getByRole("button", { name: "从断点恢复" })).toBeVisible();
  await expect(page.getByText(/quota_exhausted/).first()).toBeVisible();
  await page.getByRole("button", { name: "从断点恢复" }).click();
  await expect(page.getByLabel("播客音频")).toBeVisible({ timeout: 30_000 });
  expect(readFileSync(chapter).equals(before)).toBe(true);
  expect(statSync(chapter).mtimeMs).toBe(modified);
  const manifest = JSON.parse(
    readFileSync(join(job.directory, "manifest.json"), "utf8"),
  );
  expect(
    manifest.ai_calls.filter(
      (call: { task: string }) => call.task === "analysis:0001:0001",
    ),
  ).toHaveLength(1);
});

test("book candidates require explicit choice; server error remains actionable", async ({
  page,
}) => {
  // UI contract fixture only; real source eligibility + acquisition tested through Core in pytest.
  await page.route("**/api/books/search?*", (route) =>
    route.fulfill({
      json: {
        search_id: "a".repeat(32),
        complete: true,
        warnings: [],
        candidates: [1, 2].map((n) => ({
          id: `gutenberg:${n}`,
          identity: {
            title: `The Wealth of Nations ${n}`,
            authors: ["Adam Smith"],
            language: "en",
            edition: null,
            publication_year: null,
          },
        })),
      },
    }),
  );
  await page.route("**/api/jobs", async (route) => {
    if (route.request().method() === "POST")
      await route.fulfill({
        status: 409,
        json: { detail: "所选版本暂无合法可用来源，请上传本地文件。" },
      });
    else await route.continue();
  });
  await page.goto("/");
  await page.getByRole("tab", { name: "搜索书名" }).click();
  await page.getByLabel("书名", { exact: true }).fill("The Wealth of Nations");
  await page.getByRole("button", { name: "查找版本" }).click();
  await expect(
    page.getByRole("radiogroup", { name: "选择书籍版本" }).getByRole("radio"),
  ).toHaveCount(2);
  await expect(page.getByRole("button", { name: "生成播客" })).toBeDisabled();
  await page
    .getByRole("radiogroup", { name: "选择书籍版本" })
    .getByRole("radio")
    .nth(1)
    .check();
  await page.getByRole("button", { name: "生成播客" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "所选版本暂无合法可用来源" }),
  ).toBeVisible();
});
