import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  CreateSubagentRequest,
  Subagent,
  SubagentCatalogs,
  UpdateSubagentRequest,
} from "./types";

const BACKEND_UNAVAILABLE_STATUSES = new Set([502, 503, 504]);

export class SubagentNameCheckError extends Error {
  constructor(
    message: string,
    public readonly reason: "backend_unreachable" | "request_failed",
    /**
     * Raw backend `detail` string when the failure came from a backend
     * response carrying one. `null` when no detail was provided (e.g.
     * network-layer failure, empty response body, unparseable body) - in
     * which case `message` is a generated fallback like "Failed to check
     * subagent name: Bad Gateway" and the UI should prefer its own localized
     * fallback instead of surfacing the generated string.
     */
    public readonly detail: string | null = null,
  ) {
    super(message);
    this.name = "SubagentNameCheckError";
  }
}

export async function listSubagents(): Promise<Subagent[]> {
  const res = await fetch(`${getBackendBaseURL()}/api/subagents`);
  if (!res.ok) throw new Error(`Failed to load subagents: ${res.statusText}`);
  const data = (await res.json()) as { subagents: Subagent[] };
  return data.subagents;
}

export async function getSubagent(name: string): Promise<Subagent> {
  const res = await fetch(`${getBackendBaseURL()}/api/subagents/${name}`);
  if (!res.ok) throw new Error(`Subagent '${name}' not found`);
  return res.json() as Promise<Subagent>;
}

export async function createSubagent(
  request: CreateSubagentRequest,
): Promise<Subagent> {
  const res = await fetch(`${getBackendBaseURL()}/api/subagents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to create subagent: ${res.statusText}`,
    );
  }
  return res.json() as Promise<Subagent>;
}

export async function updateSubagent(
  name: string,
  request: UpdateSubagentRequest,
): Promise<Subagent> {
  const res = await fetch(`${getBackendBaseURL()}/api/subagents/${name}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(
      err.detail ?? `Failed to update subagent: ${res.statusText}`,
    );
  }
  return res.json() as Promise<Subagent>;
}

export async function deleteSubagent(name: string): Promise<void> {
  const res = await fetch(`${getBackendBaseURL()}/api/subagents/${name}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete subagent: ${res.statusText}`);
}

export async function checkSubagentName(
  name: string,
): Promise<{ available: boolean; name: string }> {
  let res: Response;
  try {
    res = await fetch(
      `${getBackendBaseURL()}/api/subagents/check?name=${encodeURIComponent(name)}`,
    );
  } catch {
    throw new SubagentNameCheckError(
      "Could not reach the DeerFlow backend.",
      "backend_unreachable",
    );
  }

  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    if (BACKEND_UNAVAILABLE_STATUSES.has(res.status)) {
      throw new SubagentNameCheckError(
        "Could not reach the DeerFlow backend.",
        "backend_unreachable",
      );
    }
    const backendDetail = typeof err.detail === "string" ? err.detail : null;
    throw new SubagentNameCheckError(
      backendDetail ?? `Failed to check subagent name: ${res.statusText}`,
      "request_failed",
      backendDetail,
    );
  }
  return res.json() as Promise<{ available: boolean; name: string }>;
}

export async function getSubagentCatalogs(): Promise<SubagentCatalogs> {
  const res = await fetch(`${getBackendBaseURL()}/api/subagents/catalogs`);
  if (!res.ok)
    throw new Error(`Failed to load subagent catalogs: ${res.statusText}`);
  return res.json() as Promise<SubagentCatalogs>;
}
