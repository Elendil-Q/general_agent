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
