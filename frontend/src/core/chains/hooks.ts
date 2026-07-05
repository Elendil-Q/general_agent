import { useQuery } from "@tanstack/react-query";

import { loadChains } from ".";

export function useChains() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["chains"],
    queryFn: () => loadChains(),
  });
  return { chains: data ?? [], isLoading, error };
}
