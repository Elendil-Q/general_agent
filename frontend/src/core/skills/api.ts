import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { Skill } from "./type";

export async function loadSkills() {
  const skills = await fetch(`${getBackendBaseURL()}/api/skills`);
  const json = await skills.json();
  return json.skills as Skill[];
}

export async function enableSkill(skillName: string, enabled: boolean) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/${skillName}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        enabled,
      }),
    },
  );
  return response.json();
}

export interface InstallSkillRequest {
  thread_id: string;
  path: string;
}

export interface InstallSkillResponse {
  success: boolean;
  skill_name: string;
  message: string;
}

export async function installSkill(
  request: InstallSkillRequest,
): Promise<InstallSkillResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/skills/install`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    // Handle HTTP error responses (4xx, 5xx)
    const errorData = await response.json().catch(() => ({}));
    const errorMessage =
      errorData.detail ?? `HTTP ${response.status}: ${response.statusText}`;
    return {
      success: false,
      skill_name: "",
      message: errorMessage,
    };
  }

  return response.json();
}
export async function uploadSkill(file: File): Promise<InstallSkillResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${getBackendBaseURL()}/api/skills/custom`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const errorMessage =
      errorData.detail ?? `HTTP ${response.status}: ${response.statusText}`;
    return {
      success: false,
      skill_name: "",
      message: errorMessage,
    };
  }

  return response.json();
}

// --- Custom skill editing ---

export interface CustomSkillContent {
  name: string;
  description: string;
  license: string | null;
  category: string;
  enabled: boolean;
  content: string;
}

export async function getCustomSkill(
  name: string,
): Promise<CustomSkillContent> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${name}`,
  );
  if (!response.ok) {
    throw new Error(
      (await response.json().catch(() => ({}))).detail ??
        "Failed to load skill content",
    );
  }
  return response.json();
}

export async function updateCustomSkill(
  name: string,
  content: string,
): Promise<CustomSkillContent> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${name}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
  if (!response.ok) {
    throw new Error(
      (await response.json().catch(() => ({}))).detail ??
        "Failed to update skill",
    );
  }
  return response.json();
}

export async function deleteCustomSkill(
  name: string,
): Promise<{ success: boolean }> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${name}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    throw new Error(
      (await response.json().catch(() => ({}))).detail ??
        "Failed to delete skill",
    );
  }
  return response.json();
}

export async function getPublicSkill(
  name: string,
): Promise<{ content: string }> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/public/${encodeURIComponent(name)}`,
  );
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}
