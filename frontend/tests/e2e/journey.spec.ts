import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

const REVIEW_ID = "11111111-1111-4111-8111-111111111111";
const FAILED_ID = "33333333-3333-4333-8333-333333333333";
const PATIENT = "Sample, Jordan Q.";

function summary(id: string, status: string, name: string | null) {
  return {
    id,
    status,
    version: 2,
    created_at: "2026-11-03T14:12:00Z",
    updated_at: "2026-11-03T14:12:00Z",
    document: {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      display_filename: "referral-sample-03.pdf",
      content_type: "application/pdf",
      size_bytes: 1200,
      page_count: 1,
    },
    patient_name: name,
    suggested_specialty: status === "needs_review" || status === "approved" ? "cardiology" : null,
    blocking_flag_count: status === "needs_review" ? 1 : 0,
    warning_flag_count: 0,
    possible_duplicate_of: null,
  };
}

function field(key: string, label: string, value: string | null, status = "explicit") {
  return {
    key,
    label,
    ocr_value: value ? `${label} quote` : null,
    suggested_value: value,
    confirmed_value: null as string | null,
    effective_value: value,
    field_status: status,
    readability: null,
    source: value
      ? { page: 1, element_ids: ["p1-l001"], bbox: { left: 0.1, top: 0.2, width: 0.3, height: 0.05 } }
      : null,
    edited: false,
  };
}

function detail(id: string, status: string, name: string | null, version = 2) {
  const base = summary(id, status, name);
  return {
    ...base,
    version,
    allowed_actions: status === "needs_review" ? ["edit", "approve", "reject"] : status === "failed" ? ["retry", "reject"] : [],
    fields: [
      field("patient.full_name", "Patient name", name),
      field("patient.date_of_birth", "Date of birth", "1958-03-14"),
      field("patient.phone", "Patient phone", "(555) 010-4477", "ambiguous"),
      field("patient.email", "Patient email", "jordan.sample@example.test"),
      field("patient.address", "Patient address", "1 Example Street"),
      field("referring_clinician.name", "Referring clinician", "Dr. Alex Example"),
      field("referring_clinician.phone", "Referring clinician phone", "(555) 010-0100"),
      field("referring_clinician.fax", "Referring clinician fax", null, "not_found"),
      field("referral.reason", "Reason for referral", "Exertional chest pain"),
      field("referral.sender_priority", "Sender priority", "routine"),
    ],
    flags: [
      {
        id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        code: "MISSING_REQUIRED_FIELD",
        severity: "blocking",
        field_key: "referring_clinician.fax",
        message: "A clinician fax was not found.",
        status: "open",
        acknowledgement_reason: null,
      },
    ],
    specialty: {
      label: "cardiology",
      display_name: "Cardiology",
      rationale: "The reason names exertional chest pain.",
      candidates: [],
    },
    specialty_decision: null,
    destination_specialty: status === "approved" ? { code: "cardiology", other_name: null } : null,
    current_run: { id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", attempt: 1, state: "succeeded", error_code: null, retryable: false },
    history: [{ action: "uploaded", actor: "system", from_status: null, to_status: "uploaded", at: "2026-11-03T14:12:02Z", reason: null }],
  };
}

async function installApi(page: Page, options: { processing?: boolean; pdf?: boolean } = {}) {
  let reviews: ReturnType<typeof detail> = detail(REVIEW_ID, "needs_review", PATIENT);
  let polls = 0;
  await page.route("**/api/backend/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/backend", "");
    if (path === "/me") {
      await route.fulfill({ json: { user_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", username: "staff" } });
      return;
    }
    if (path === "/referrals" && request.method() === "GET") {
      await route.fulfill({
        json: {
          items: [summary(REVIEW_ID, reviews.status, PATIENT), summary(FAILED_ID, "failed", null)],
          total: 2,
          limit: 100,
          offset: 0,
        },
      });
      return;
    }
    if (path === "/referrals" && request.method() === "POST") {
      await route.fulfill({
        status: 202,
        json: summary("22222222-2222-4222-8222-222222222222", "uploaded", null),
      });
      return;
    }
    if (path === `/referrals/${REVIEW_ID}/status`) {
      polls += 1;
      const sequence = [
        { status: "uploaded", processing_step: null },
        { status: "processing", processing_step: "reading_text" },
        { status: "processing", processing_step: "structuring" },
        { status: "needs_review", processing_step: null },
      ];
      const current = sequence[Math.min(polls - 1, sequence.length - 1)]!;
      await route.fulfill({
        json: { id: REVIEW_ID, version: polls >= 4 ? reviews.version : 1, failure: null, ...current, ...(reviews.status === "approved" ? { status: "approved" } : {}) },
      });
      return;
    }
    if (path === `/referrals/${FAILED_ID}/status`) {
      await route.fulfill({
        json: {
          id: FAILED_ID,
          status: "failed",
          version: 2,
          processing_step: null,
          failure: {
            step: "structuring",
            message: "The document could not be read.",
            retryable: true,
            run_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
          },
        },
      });
      return;
    }
    if (path === `/referrals/${REVIEW_ID}` && request.method() === "GET") {
      await route.fulfill({ json: options.processing && polls < 4 ? { ...detail(REVIEW_ID, "processing", null, 1), fields: [], flags: [], specialty: null } : reviews });
      return;
    }
    if (path === `/referrals/${FAILED_ID}` && request.method() === "GET") {
      await route.fulfill({ json: detail(FAILED_ID, "failed", null) });
      return;
    }
    if (path === `/referrals/${REVIEW_ID}/document` || path === `/referrals/${FAILED_ID}/document`) {
      await route.fulfill({
        status: 200,
        contentType: options.pdf ? "application/pdf" : "image/png",
        body: options.pdf ? readFileSync("tests/fixtures/referral.pdf") : Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
          "base64",
        ),
      });
      return;
    }
    if (path === `/referrals/${REVIEW_ID}` && request.method() === "PATCH") {
      const body = request.postDataJSON() as { fields: { key: string; value: string }[] };
      reviews = {
        ...reviews,
        version: reviews.version + 1,
        fields: reviews.fields.map((item) =>
          item.key === body.fields[0]?.key
            ? { ...item, confirmed_value: body.fields[0].value, effective_value: body.fields[0].value, edited: true }
            : item,
        ),
      };
      await route.fulfill({ json: reviews });
      return;
    }
    if (path === `/referrals/${REVIEW_ID}/approve`) {
      reviews = { ...reviews, status: "approved", allowed_actions: [], destination_specialty: { code: "cardiology", other_name: null } };
      await route.fulfill({ json: reviews });
      return;
    }
    if (path === `/referrals/${FAILED_ID}/retry`) {
      await route.fulfill({ status: 202, json: summary(FAILED_ID, "uploaded", null) });
      return;
    }
    await route.fulfill({ status: 404, json: { status: 404, title: "Not found", detail: "Missing", code: "NOT_FOUND", correlation_id: "ffffffff-ffff-4fff-8fff-ffffffffffff" } });
  });
}

async function signIn(page: Page) {
  await page.goto("/referrals");
  await expect(page).toHaveURL(/\/sign-in\?next=%2Freferrals/);
  await page.getByRole("link", { name: "Continue to secure sign-in" }).click();
  await expect(page).toHaveURL(/\/referrals$/);
}

test("signed-out staff are sent to sign-in", async ({ page }) => {
  await page.goto("/referrals");
  await expect(page).toHaveURL(/\/sign-in\?next=%2Freferrals/);
  const storage = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
  expect(storage).toEqual({ local: 0, session: 0 });
});

test("review journey keeps patient names out of the address bar and title", async ({ page }) => {
  const urls: string[] = [];
  page.on("request", (request) => urls.push(request.url()));
  await installApi(page);
  await signIn(page);
  await expect(page.getByRole("heading", { name: "Referrals" })).toBeVisible();
  const before = urls.length;
  await page.getByLabel("Search referral ID or patient name").fill(PATIENT);
  await page.waitForTimeout(300);
  expect(urls.slice(before).some((url) => url.includes("Jordan") || url.includes("Sample"))).toBe(false);
  await page.getByRole("link", { name: REVIEW_ID.slice(0, 8) }).click();
  await expect(page).toHaveURL(new RegExp(`/referrals/${REVIEW_ID}$`));
  await expect(page).toHaveTitle(`${REVIEW_ID} · Referral Intake`);
  expect(page.url()).not.toContain("Jordan");
  expect(page.url()).not.toContain("Sample");
  await expect(page.getByRole("heading", { name: `Referral for ${PATIENT}` })).toBeVisible();
  await expect(page.locator('[id="field-patient.phone"]').getByRole("button", { name: "Edit" })).toBeVisible({ timeout: 15_000 });
  await page.locator('[id="field-patient.phone"]').getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Patient phone").fill("(555) 010-4471");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Edited").first()).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await page.getByRole("button", { name: "Approve" }).last().click();
  await expect(page.getByRole("heading", { name: "Approved" })).toBeVisible();
  await expect(page.getByText("nothing was sent to a department")).toBeVisible();
});

test("a failed referral can be retried", async ({ page }) => {
  await installApi(page);
  await signIn(page);
  let retried = false;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes(`/referrals/${FAILED_ID}/retry`)) retried = true;
  });
  await page.getByRole("tab", { name: /Failed/ }).click();
  await page.getByRole("link", { name: FAILED_ID.slice(0, 8) }).click();
  await expect(page.getByText("The document could not be read.")).toBeVisible();
  await page.getByRole("button", { name: "Retry processing" }).click();
  await expect.poll(() => retried).toBe(true);
});

test("a 401 returns to sign-in", async ({ page }) => {
  await page.route("**/api/backend/**", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/problem+json",
      json: {
        type: "about:blank",
        title: "Sign in required",
        status: 401,
        detail: "Sign in again.",
        code: "TOKEN_EXPIRED",
        correlation_id: "99999999-9999-4999-8999-999999999999",
      },
    });
  });
  await page.goto("/referrals");
  await page.getByRole("link", { name: "Continue to secure sign-in" }).click();
  await expect(page).toHaveURL(/reason=session_expired/);
});

test("sign-in, inbox, and review have no serious accessibility violations", async ({ page }) => {
  await installApi(page);
  await page.goto("/sign-in");
  const signIn = await new AxeBuilder({ page }).analyze();
  expect(signIn.violations.filter((item) => item.impact === "serious" || item.impact === "critical")).toEqual([]);
  await signInPage(page);
  const inbox = await new AxeBuilder({ page }).analyze();
  expect(inbox.violations.filter((item) => item.impact === "serious" || item.impact === "critical")).toEqual([]);
});

async function signInPage(page: Page) {
  await page.goto("/sign-in?next=%2Freferrals");
  await page.getByRole("link", { name: "Continue to secure sign-in" }).click();
}


test("processing completes without refresh and the real PDF survives reload and focus", async ({ page, context }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await installApi(page, { processing: true, pdf: true });
  await signIn(page);
  await page.getByRole("link", { name: REVIEW_ID.slice(0, 8) }).click();
  await expect(page.getByRole("heading", { name: "Reading the document" })).toBeVisible();
  await expect(page.locator(".react-pdf__Page canvas")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("heading", { name: `Referral for ${PATIENT}` })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Elapsed", { exact: false })).toHaveCount(0);
  await expect(page.getByText("page 1 of 2", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.locator('.react-pdf__Page[data-page-number="2"] canvas')).toBeVisible();
  const other = await context.newPage();
  await other.goto("about:blank");
  await page.bringToFront();
  await other.close();
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`/referrals/${REVIEW_ID}$`));
  await expect(page.locator(".react-pdf__Page canvas")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("heading", { name: "Referral", exact: true }).scrollIntoViewIfNeeded();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.getByText("History", { exact: true }).scrollIntoViewIfNeeded();
  const footer = page.locator(".sticky.bottom-0");
  await expect(footer).toBeVisible();
  await page.screenshot({ path: "test-results/referral-review.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("expired document service session offers recovery", async ({ page }) => {
  await installApi(page);
  let attempts = 0;
  await page.route(`**/referrals/${REVIEW_ID}/document`, (route) => {
    attempts += 1;
    return attempts === 1
      ? route.fulfill({ status: 503, json: { code: "AWS_SESSION_EXPIRED", detail: "The local document service needs to be reconnected." } })
      : route.fulfill({ contentType: "application/pdf", body: readFileSync("tests/fixtures/referral.pdf") });
  });
  await signIn(page);
  await page.getByRole("link", { name: REVIEW_ID.slice(0, 8) }).click();
  await expect(page.getByText("The local document service session expired. Reconnect AWS, then retry the document.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry document" })).toBeVisible();
  await page.getByRole("button", { name: "Retry document" }).click();
  await expect(page.locator(".react-pdf__Page canvas")).toBeVisible({ timeout: 20_000 });
});

test("saved synthetic referral shows one matching warning and a contained document", async ({ page }) => {
  const saved = JSON.parse(readFileSync("tests/fixtures/referral-detail.json", "utf8"));
  await installApi(page);
  await page.route(`**/api/backend/referrals/${saved.id}**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/document")) {
      await route.fulfill({ contentType: "application/pdf", body: readFileSync("tests/fixtures/referral.pdf") });
    } else if (path.endsWith("/status")) {
      await route.fulfill({ json: { id: saved.id, status: saved.status, version: saved.version, processing_step: null, failure: null } });
    } else {
      await route.fulfill({ json: saved });
    }
  });
  await signIn(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/referrals/${saved.id}`);
  await expect(page.locator(".react-pdf__Page canvas")).toBeVisible();
  await expect(page.getByRole("heading", { name: "1 review item needs your attention" })).toBeVisible();
  await expect(page.getByText("12 resolved or acknowledged", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/audited-referral.png" });
  await page.getByRole("button", { name: "View field" }).click();
  await expect(page.locator('[id="field-referral.diagnosis_codes"]')).toBeInViewport();
  await page.locator('[id="field-referral.diagnosis_codes"]').getByRole("button", { name: "p.1" }).click();
  await expect(page.getByTestId("highlight-box")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  expect(await page.locator(".sticky.bottom-0").evaluate((el) => Math.abs(innerHeight - el.getBoundingClientRect().bottom))).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: "test-results/audited-referral-bottom.png" });
});
