import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { ModesConfigResponse } from "./types";

/**
 * Load mode presets from the backend. Returns `null` when the backend is older
 * and does not serve `GET /api/modes/config` (404), so callers can fall back
 * to the built-in defaults. Any other error is thrown.
 */
export async function loadModesConfig(): Promise<ModesConfigResponse | null> {
  const response = await fetch(`${getBackendBaseURL()}/api/modes/config`);
  if (!response.ok) {
    if (response.status === 404) {
      // Older backend — caller falls back to built-in presets.
      return null;
    }
    throw new Error(`Failed to load modes config: ${response.statusText}`);
  }
  return response.json() as Promise<ModesConfigResponse>;
}
