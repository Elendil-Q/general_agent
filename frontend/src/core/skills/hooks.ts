import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteCustomSkill,
  enableSkill,
  getCustomSkill,
  getPublicSkill,
  updateCustomSkill,
  uploadSkill,
} from "./api";
import type { Skill } from "./type";

import { loadSkills } from ".";

export function useSkills() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["skills"],
    queryFn: () => loadSkills(),
  });
  return { skills: data ?? [], isLoading, error };
}

export function useEnableSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      skillName,
      enabled,
    }: {
      skillName: string;
      enabled: boolean;
    }) => {
      await enableSkill(skillName, enabled);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}
export function useUploadSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      return uploadSkill(file);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}

export function useCustomSkill(name: string) {
  return useQuery({
    queryKey: ["skill", name],
    queryFn: () => getCustomSkill(name),
    enabled: !!name,
  });
}

export function usePublicSkill(name: string) {
  return useQuery({
    queryKey: ["skill", "public", name],
    queryFn: () => getPublicSkill(name),
    enabled: !!name,
  });
}

export function useSkillContent(skill: Skill | null) {
  const customQuery = useCustomSkill(
    skill?.category === "custom" ? skill.name : "",
  );
  const publicQuery = usePublicSkill(
    skill?.category === "public" ? skill.name : "",
  );
  return skill?.category === "custom" ? customQuery : publicQuery;
}

export function useUpdateCustomSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      name,
      content,
    }: {
      name: string;
      content: string;
    }) => {
      return updateCustomSkill(name, content);
    },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      void queryClient.invalidateQueries({
        queryKey: ["skill", variables.name],
      });
    },
  });
}

export function useDeleteCustomSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string) => {
      return deleteCustomSkill(name);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}
