"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveReferral,
  getDocument,
  getReferral,
  getReferralStatus,
  listReferrals,
  patchReferral,
  rejectReferral,
  retryReferral,
  uploadReferral,
} from "./client";
import type { Priority, ReasonCode, ReferralStatus } from "./types";

export function statusPollInterval(status: ReferralStatus | undefined): number | false {
  return status === "uploaded" || status === "processing" ? 2000 : false;
}

export function inboxPollInterval(statuses: ReferralStatus[] | undefined): number | false {
  if (!statuses?.some((status) => status === "uploaded" || status === "processing")) return false;
  return 10_000;
}

export function useReferralList() {
  return useQuery({
    queryKey: ["referrals"],
    queryFn: listReferrals,
    staleTime: 5_000,
    refetchInterval: (query) => inboxPollInterval(query.state.data?.items.map((item) => item.status)),
    refetchOnWindowFocus: true,
  });
}

export function useReferral(id: string) {
  return useQuery({
    queryKey: ["referral", id],
    queryFn: () => getReferral(id),
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

export function useReferralStatus(id: string) {
  return useQuery({
    queryKey: ["referral", id, "status"],
    queryFn: () => getReferralStatus(id),
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    refetchInterval: (query) => statusPollInterval(query.state.data?.status),
  });
}

export function useReferralDocument(id: string) {
  return useQuery({
    queryKey: ["referral", id, "document"],
    queryFn: () => getDocument(id),
    gcTime: 0,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function useReferralMutations(id: string) {
  const queryClient = useQueryClient();
  const apply = (detail: { id: string }) => {
    queryClient.setQueryData(["referral", detail.id], detail);
    void queryClient.invalidateQueries({ queryKey: ["referrals"] });
    void queryClient.invalidateQueries({ queryKey: ["referral", id, "status"] });
  };
  return {
    edit: useMutation({
      mutationFn: (body: { version: number; fields: { key: string; value: string | null }[] }) =>
        patchReferral(id, body),
      onSuccess: apply,
    }),
    approve: useMutation({
      mutationFn: (body: {
        version: number;
        destination_specialty: { code: string; other_name?: string | null };
        sender_priority: Priority;
        not_on_document: string[];
        acknowledgements: { flag_id: string; reason: string }[];
      }) => approveReferral(id, body),
      onSuccess: apply,
    }),
    reject: useMutation({
      mutationFn: (body: { version: number; reason_code: ReasonCode; note?: string }) =>
        rejectReferral(id, body),
      onSuccess: apply,
    }),
    retry: useMutation({
      mutationFn: (version: number) => retryReferral(id, version),
      onSuccess: () => {
        void queryClient.invalidateQueries({ queryKey: ["referral", id] });
        void queryClient.invalidateQueries({ queryKey: ["referral", id, "status"] });
        void queryClient.invalidateQueries({ queryKey: ["referrals"] });
      },
    }),
    upload: useMutation({
      mutationFn: ({ file, idempotencyKey }: { file: File; idempotencyKey: string }) =>
        uploadReferral(file, idempotencyKey),
    }),
  };
}
