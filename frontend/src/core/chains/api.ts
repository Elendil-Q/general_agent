import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { Chain, ChainProgressSummary } from "./type";

export async function loadChains() {
  const res = await fetch(`${getBackendBaseURL()}/api/chains`);
  const json = await res.json();
  return json.chains as Chain[];
}

export async function loadChainProgress(threadId: string) {
  const res = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/chains/progress`,
  );
  const json = await res.json();
  return (json.chains ?? []) as ChainProgressSummary[];
}

export async function discardChainProgress(
  threadId: string,
  chainName: string,
) {
  const res = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/chains/${encodeURIComponent(chainName)}/progress`,
    { method: "DELETE" },
  );
  return res.ok;
}
