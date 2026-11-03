import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Providers } from "@/components/providers";
import { environmentLabel } from "@/lib/auth/mode";
import { safeNextPath, signInPath } from "@/lib/auth/paths";
import { readViewer } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function StaffLayout({ children }: { children: React.ReactNode }) {
  const viewer = await readViewer();
  if (!viewer) {
    const headerStore = await headers();
    const path = headerStore.get("x-pathname") ?? "/referrals";
    redirect(signInPath(safeNextPath(path)));
  }
  return (
    <Providers>
      <AppShell email={viewer.email} environment={environmentLabel()}>
        {children}
      </AppShell>
    </Providers>
  );
}
