export type AppEnvironment = "local" | "aws-demo" | "test";

export function authMode(): "mock" | "cognito" {
  return process.env.AUTH_MODE === "mock" ? "mock" : "cognito";
}

export function appEnvironment(): AppEnvironment {
  const value = process.env.NEXT_PUBLIC_APP_ENV;
  if (value === "aws-demo" || value === "test") return value;
  return "local";
}

export function environmentLabel(value: string | undefined = appEnvironment()): string {
  return value === "aws-demo" ? "AWS demo" : "Local demo";
}

/** A production demo build refuses mock auth. Tests set NEXT_PUBLIC_APP_ENV=test. */
export function mockAuthRefused(mode: string | undefined, appEnv: string | undefined, nodeEnv: string | undefined): boolean {
  return nodeEnv === "production" && mode === "mock" && appEnv !== "test";
}

export function assertDemoAuthMode(): void {
  if (mockAuthRefused(process.env.AUTH_MODE, process.env.NEXT_PUBLIC_APP_ENV, process.env.NODE_ENV)) {
    throw new Error("AUTH_MODE=mock is refused for a demo build. Use AUTH_MODE=cognito.");
  }
}

export function isMockAuth(): boolean {
  assertDemoAuthMode();
  return authMode() === "mock";
}
