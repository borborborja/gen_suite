import { api } from "./client";
import type { BillingAuthorization, BillingHistory, BillingPreview, BillingProposal, BillingScope, BillingTask } from "./types";

export const billingHistory = (scope: BillingScope, target_id: string, task_type: BillingTask, offset = 0) =>
  api<BillingHistory>(`/providers/authorizations?${new URLSearchParams({ scope, target_id, task_type, offset: String(offset), limit: "20" })}`);
export const previewBilling = (body: BillingProposal) =>
  api<BillingPreview>("/providers/authorizations/preview", { method: "POST", body: JSON.stringify(body) });
export const approveBilling = (body: BillingProposal & { client_id: string; preview_hash: string; authorize: true; reason: string }) =>
  api<BillingAuthorization>("/providers/authorizations", { method: "POST", body: JSON.stringify(body) });
