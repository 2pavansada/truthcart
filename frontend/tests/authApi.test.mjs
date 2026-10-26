import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiClientError, getAuthMe, loginAccount, signupAccount } from "../src/api/client.ts";

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("signup fetches a CSRF token and sends it with cookie credentials", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    if (String(url) === "/api/auth/csrf") return jsonResponse({ csrf_token: "csrf-demo" });
    return jsonResponse({ user_id: "u-1", name: "A Shopper", email: "shopper@example.com" }, 201);
  };

  const user = await signupAccount({ name: "A Shopper", email: "shopper@example.com", password: "a-long-demo-password" });

  assert.equal(user.user_id, "u-1");
  assert.equal(calls.length, 2);
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(calls[1].url, "/api/auth/signup");
  assert.equal(calls[1].init.credentials, "include");
  assert.equal(calls[1].init.headers["X-CSRF-Token"], "csrf-demo");
});

test("/me treats an unauthenticated session as guest state", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (_url, init = {}) => {
    assert.equal(init.credentials, "include");
    return jsonResponse({ error: { code: "AUTH_REQUIRED", message: "Sign in to access your account." } }, 401);
  };

  assert.equal(await getAuthMe(), null);
});

test("login exposes generic backend credential errors without simulating success", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url) => String(url) === "/api/auth/csrf"
    ? jsonResponse({ csrf_token: "csrf-demo" })
    : jsonResponse({ error: { code: "INVALID_CREDENTIALS", message: "Email or password is incorrect." } }, 401);

  await assert.rejects(
    loginAccount({ email: "shopper@example.com", password: "wrong-password" }),
    (error) => error instanceof ApiClientError && error.code === "INVALID_CREDENTIALS" && /Email or password/.test(error.message),
  );
});
