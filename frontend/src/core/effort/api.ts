import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { EffortsConfigResponse } from "./types";

/**
 * Load effort presets from the backend. Returns `null` when the backend is older
 * and does not serve `GET /api/efforts/config` (404), so callers can fall back
 * to the built-in defaults. Any other error is thrown.
 */
export async function loadEffortsConfig(): Promise<EffortsConfigResponse | null> {
  const response = await fetch(`${getBackendBaseURL()}/api/efforts/config`);
  if (!response.ok) {
    if (response.status === 404) {
      // Older backend — caller falls back to built-in presets.
      return null;
    }
    throw new Error(`Failed to load efforts config: ${response.statusText}`);
  }
  return response.json() as Promise<EffortsConfigResponse>;
}
