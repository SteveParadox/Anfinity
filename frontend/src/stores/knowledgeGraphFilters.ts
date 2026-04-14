import { create } from 'zustand';

import type { KnowledgeGraphFilters } from '@/types';

export const defaultKnowledgeGraphFilters: KnowledgeGraphFilters = {
  nodeTypes: [],
  edgeTypes: [],
  search: '',
  minWeight: 0,
  includeIsolated: true,
  dateFrom: undefined,
  dateTo: undefined,
  clusterIds: [],
  confidenceThreshold: 0,
};

interface KnowledgeGraphFilterStore {
  filters: KnowledgeGraphFilters;
  setFilters: (
    next: KnowledgeGraphFilters | ((current: KnowledgeGraphFilters) => KnowledgeGraphFilters)
  ) => void;
  resetFilters: () => void;
}

const useKnowledgeGraphFilterStore = create<KnowledgeGraphFilterStore>((set) => ({
  filters: { ...defaultKnowledgeGraphFilters },
  setFilters: (next) =>
    set((state) => ({
      filters: typeof next === 'function' ? next(state.filters) : next,
    })),
  resetFilters: () => set({ filters: { ...defaultKnowledgeGraphFilters } }),
}));

export function getKnowledgeGraphFilterState(): { filters: KnowledgeGraphFilters } {
  return { filters: useKnowledgeGraphFilterStore.getState().filters };
}

export function setKnowledgeGraphFilters(
  next: KnowledgeGraphFilters | ((current: KnowledgeGraphFilters) => KnowledgeGraphFilters)
): void {
  useKnowledgeGraphFilterStore.getState().setFilters(next);
}

export function resetKnowledgeGraphFilters(): void {
  useKnowledgeGraphFilterStore.getState().resetFilters();
}

export function useKnowledgeGraphFilters(): KnowledgeGraphFilters {
  return useKnowledgeGraphFilterStore((state) => state.filters);
}
