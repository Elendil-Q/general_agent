export interface ChainNode {
  name: string;
  subagent: string;
  depends_on: string[];
  prompt?: string | null;
}

export interface Chain {
  name: string;
  description: string;
  category: string;
  nodes: ChainNode[];
}

export interface ChainProgressSummary {
  chain_name: string;
  run_id: string;
  status: string;
  completed_count: number;
  total_count: number;
  input: string;
  updated_at: string;
  resumable: boolean;
}
