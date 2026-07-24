import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createSubagent,
  deleteSubagent,
  getSubagent,
  getSubagentCatalogs,
  listSubagents,
  updateSubagent,
} from "./api";
import type { CreateSubagentRequest, UpdateSubagentRequest } from "./types";

export function useSubagents() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["subagents"],
    queryFn: () => listSubagents(),
  });
  return { subagents: data ?? [], isLoading, error };
}

export function useSubagent(name: string | null | undefined) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["subagents", name],
    queryFn: () => getSubagent(name!),
    enabled: !!name,
  });
  return { subagent: data ?? null, isLoading, error };
}

export function useSubagentCatalogs() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["subagents", "catalogs"],
    queryFn: () => getSubagentCatalogs(),
  });
  return { catalogs: data ?? null, isLoading, error };
}

export function useCreateSubagent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateSubagentRequest) => createSubagent(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["subagents"] });
    },
  });
}

export function useUpdateSubagent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      request,
    }: {
      name: string;
      request: UpdateSubagentRequest;
    }) => updateSubagent(name, request),
    onSuccess: (_data, { name }) => {
      void queryClient.invalidateQueries({ queryKey: ["subagents"] });
      void queryClient.invalidateQueries({ queryKey: ["subagents", name] });
    },
  });
}

export function useDeleteSubagent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteSubagent(name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["subagents"] });
    },
  });
}
