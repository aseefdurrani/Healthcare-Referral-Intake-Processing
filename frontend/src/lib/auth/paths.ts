const REFERRAL_PATH = /^\/referrals\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A return path may only be the inbox or one referral id. */
export function safeNextPath(value: string | null | undefined): string {
  if (!value) return "/referrals";
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/referrals";
  if (value.includes("?") || value.includes("#") || value.includes("%")) return "/referrals";
  if (value === "/referrals") return value;
  return REFERRAL_PATH.test(value) ? value : "/referrals";
}

export function signInPath(nextPath: string): string {
  const next = safeNextPath(nextPath);
  return `/sign-in?next=${encodeURIComponent(next)}`;
}
