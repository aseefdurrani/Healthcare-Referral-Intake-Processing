import type { Metadata } from "next";

import { InboxScreen } from "@/components/referrals/inbox-screen";

export const metadata: Metadata = { title: "Referrals · Referral Intake" };

export default function ReferralsPage() {
  return <InboxScreen />;
}
