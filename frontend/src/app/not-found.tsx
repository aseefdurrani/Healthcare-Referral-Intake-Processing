import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-lg px-6 py-16">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="mt-2 text-sm text-text-muted">That page is not part of Referral Intake.</p>
      <Link href="/referrals" className="mt-4 inline-block text-primary">
        Back to referrals
      </Link>
    </main>
  );
}
