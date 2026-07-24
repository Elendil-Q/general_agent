export type SubagentSource = "builtin" | "global" | "user";

export interface Subagent {
  name: string;
  description: string;
  system_prompt: string | null;
  tools: string[] | null;
  skills: string[] | null;
  skills_on_demand: string[] | null;
  model: string;
  max_turns: number;
  timeout_seconds: number;
  readonly: boolean;
  source: SubagentSource;
  overrides_global?: boolean;
}

export interface CreateSubagentRequest {
  name: string;
  description: string;
  system_prompt?: string | null;
  tools?: string[] | null;
  skills?: string[] | null;
  skills_on_demand?: string[] | null;
  model?: string;
  max_turns?: number;
  timeout_seconds?: number;
}

export interface UpdateSubagentRequest {
  description?: string;
  system_prompt?: string | null;
  tools?: string[] | null;
  skills?: string[] | null;
  skills_on_demand?: string[] | null;
  model?: string;
  max_turns?: number;
  timeout_seconds?: number;
}

export interface SubagentCatalogModel {
  name: string;
  label: string;
}

export interface SubagentCatalogSkill {
  name: string;
  description: string | null;
}

export interface SubagentCatalogs {
  models: SubagentCatalogModel[];
  tools: string[];
  skills: SubagentCatalogSkill[];
}
