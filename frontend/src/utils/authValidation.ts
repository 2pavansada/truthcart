export type AuthMode = "login" | "signup";

export interface AuthFormValues {
  name: string;
  email: string;
  password: string;
  passwordConfirmation: string;
}

export function validateAuthForm(mode: AuthMode, values: AuthFormValues): string | null {
  const name = values.name.trim();
  const email = values.email.trim();

  if (mode === "signup" && (!name || name.length > 80)) {
    return "Enter your name (up to 80 characters).";
  }
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return "Enter a valid email address.";
  }
  if (mode === "signup" && (values.password.length < 12 || values.password.length > 128)) {
    return "Use a password between 12 and 128 characters.";
  }
  if (mode === "login" && (!values.password || values.password.length > 128)) {
    return "Enter your password.";
  }
  if (mode === "signup" && values.password !== values.passwordConfirmation) {
    return "The passwords do not match.";
  }
  return null;
}
