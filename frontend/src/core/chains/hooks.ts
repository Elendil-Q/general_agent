import { useQuery } from "@tanstack/react-query";

import { loadChains, loadChainProgress } from ".";

export function useChains() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["chains"],
    queryFn: () => loadChains(),
  });
  return { chains: data ?? [], isLoading, error };
}

export function useChainProgress(threadId: string | null | undefined) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["chain-progress", threadId],
    queryFn: () => loadChainProgress(threadId!),
    enabled: !!threadId,
  });
  return { chainProgress: data ?? [], isLoading, error };
}
