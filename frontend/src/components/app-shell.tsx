"use client";

import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function AppShell({
  email,
  environment,
  children,
}: {
  email: string;
  environment: string;
  children: React.ReactNode;
}) {
  const queryClient = useQueryClient();
  return (
    <div className="min-h-screen bg-canvas text-text">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="flex h-16 items-center gap-6 border-b border-border bg-surface px-6">
        <img src="/brand/cortrucent.png" alt="Cortrucent" className="h-6 w-auto" />
        <div className="flex items-baseline gap-4">
          <p className="text-base font-semibold leading-6">Referral Intake</p>
          <Link href="/referrals" className="text-sm text-primary">
            Referrals
          </Link>
        </div>
        <div className="ml-auto flex items-center gap-4">
          <p className="text-xs font-medium leading-4 text-text-muted">Synthetic data only</p>
          <p className="text-xs font-medium leading-4 text-text-muted">{environment}</p>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" type="button">
                {email}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
                <a
                  href="/api/auth/sign-out"
                  onClick={() => {
                    queryClient.clear();
                  }}
                >
                  Sign out
                </a>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>
      <main id="content" className="px-6 py-6">
        {children}
      </main>
    </div>
  );
}
