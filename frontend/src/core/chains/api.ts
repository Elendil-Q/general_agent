import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { Chain } from "./type";

export async function loadChains() {
  const res = await fetch(`${getBackendBaseURL()}/api/chains`);
  const json = await res.json();
  return json.chains as Chain[];
}
