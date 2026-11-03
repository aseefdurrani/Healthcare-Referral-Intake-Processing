import type { Metadata } from "next";

import { ReferralWorkspace } from "@/components/referrals/referral-workspace";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ referralId: string }>;
}): Promise<Metadata> {
  const { referralId } = await params;
  return { title: `${referralId} · Referral Intake` };
}

export default async function ReferralPage({ params }: { params: Promise<{ referralId: string }> }) {
  const { referralId } = await params;
  return <ReferralWorkspace referralId={referralId} />;
}
