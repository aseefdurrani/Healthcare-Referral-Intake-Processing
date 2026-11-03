export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { assertDemoAuthMode } = await import("./src/lib/auth/mode");
    assertDemoAuthMode();
  }
}
