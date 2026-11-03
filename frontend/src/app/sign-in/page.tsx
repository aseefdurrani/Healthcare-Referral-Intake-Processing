import { Button } from "@/components/ui/button";
import { isMockAuth } from "@/lib/auth/mode";
import { safeNextPath } from "@/lib/auth/paths";
import { cognitoConfigured } from "@/lib/auth/session";

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const reason = one(params.reason);
  const signedOut = one(params.signed_out) === "1";
  const error = one(params.error);
  const next = safeNextPath(one(params.next));
  const available = isMockAuth() || cognitoConfigured();
  if (error && /^[A-Za-z0-9_-]{1,64}$/.test(error)) {
    process.stderr.write(`${JSON.stringify({ event: "auth.signin_failed", code: error })}\n`);
  }
  let banner: string | null = null;
  if (reason === "session_expired") banner = "Your session ended. Sign in again to continue.";
  else if (reason === "not_authorized") banner = "This account isn't authorized for this demo.";
  else if (signedOut) banner = "You are signed out.";
  else if (error === "timeout") banner = "Sign-in timed out, please try again.";
  else if (error) banner = "Sign-in could not be completed. Try again.";
  else if (!available) banner = "Sign-in is unavailable.";

  return (
    <main className="mx-auto flex min-h-screen max-w-lg flex-col items-center justify-center px-6 text-center">
      <img src="/brand/cortrucent.png" alt="Cortrucent" className="h-6 w-auto" />
      <h1 className="mt-6 text-2xl font-semibold leading-8">Referral Intake</h1>
      <p className="mt-1 text-base font-semibold">Staff sign-in</p>
      {banner ? (
        <p role="status" className="mt-6 text-sm">
          {banner}
        </p>
      ) : null}
      {available && reason !== "not_authorized" ? (
        <Button asChild className="mt-6">
          <a href={`/api/auth/sign-in?next=${encodeURIComponent(next)}`}>Continue to secure sign-in</a>
        </Button>
      ) : null}
      {reason === "not_authorized" ? (
        <Button asChild className="mt-6" variant="outline">
          {/* Full navigation so the auth route can clear cookies and redirect. */}
          {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
          <a href="/api/auth/sign-out">Sign out</a>
        </Button>
      ) : null}
      <p className="mt-4 max-w-sm text-sm text-text-muted">
        You&apos;ll sign in on the Cortrucent-branded Amazon Cognito page. Password help comes from the demo administrator.
      </p>
      <p className="mt-10 text-xs font-medium text-text-muted">Synthetic data only. Demo environment.</p>
    </main>
  );
}
