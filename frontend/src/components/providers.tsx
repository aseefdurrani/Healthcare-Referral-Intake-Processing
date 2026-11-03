"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiProblem, classifyProblem } from "@/lib/api/problems";
import { shouldRetryQuery } from "@/lib/api/client";
import { signInPath } from "@/lib/auth/paths";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetryQuery,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  });
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(makeQueryClient);
  client.getQueryCache().config.onError = (error) => {
    if (!(error instanceof ApiProblem)) return;
    const kind = classifyProblem(error);
    if (kind === "session") {
      window.location.assign(signInPath(window.location.pathname).replace("?", "?reason=session_expired&"));
    }
    if (kind === "forbidden") {
      window.location.assign("/sign-in?reason=not_authorized");
    }
  };
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={300}>
        {children}
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export function clearSessionCache(client: QueryClient) {
  client.clear();
}
