import assert from "node:assert/strict";
import { test } from "node:test";
import { validateAuthForm } from "../src/utils/authValidation.ts";

const validSignup = {
  name: "A Shopper",
  email: "shopper@example.com",
  password: "a-long-demo-password",
  passwordConfirmation: "a-long-demo-password",
};

test("signup validates name, email, password length, and confirmation", () => {
  assert.equal(validateAuthForm("signup", validSignup), null);
  assert.match(validateAuthForm("signup", { ...validSignup, email: "not-an-email" }), /valid email/i);
  assert.match(validateAuthForm("signup", { ...validSignup, password: "short" }), /12 and 128/i);
  assert.match(validateAuthForm("signup", { ...validSignup, passwordConfirmation: "different-password" }), /do not match/i);
  assert.match(validateAuthForm("signup", { ...validSignup, name: " " }), /name/i);
});

test("login accepts a valid email and leaves credential validation to the backend", () => {
  assert.equal(validateAuthForm("login", { ...validSignup, password: "old-password", passwordConfirmation: "" }), null);
  assert.match(validateAuthForm("login", { ...validSignup, password: "" }), /password/i);
});
