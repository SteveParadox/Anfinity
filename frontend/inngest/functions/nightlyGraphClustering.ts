import { inngest } from '../client';
import { clusterGraphNodes } from '@/lib/graphClustering';
import type { GraphClusterInput } from '@/types';

const API_BASE_URL = process.env.API_BASE_URL || process.env.VITE_API_URL || 'http://localhost:8080';
const GRAPH_CLUSTER_SYNC_TOKEN = process.env.GRAPH_CLUSTER_SYNC_TOKEN || '';

async function apiRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'x-graph-sync-token': GRAPH_CLUSTER_SYNC_TOKEN,
      ...(options?.headers || {}),
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Graph clustering API failed (${response.status}): ${body}`);
  }

  return response.json() as Promise<T>;
}

export const nightlyGraphClustering = inngest.createFunction(
  {
    id: 'nightly-graph-clustering',
    name: 'Nightly Graph Clustering',
  },
  {
    cron: '0 2 * * *',
  },
  async ({ step }) => {
    if (!GRAPH_CLUSTER_SYNC_TOKEN) {
      throw new Error('GRAPH_CLUSTER_SYNC_TOKEN is required for nightly graph clustering.');
    }

    const workspacePayload = await step.run('load-workspaces', async () => {
      return apiRequest<{ workspace_ids: string[] }>('/knowledge-graph/internal/workspaces');
    });

    const summaries: Array<{ workspaceId: string; clustersSaved: number; nodesClustered: number }> = [];

    for (const workspaceId of workspacePayload.workspace_ids || []) {
      const input = await step.run(`cluster-input-${workspaceId}`, async () => {
        return apiRequest<GraphClusterInput>(`/knowledge-graph/internal/${workspaceId}/cluster-input`);
      });

      const clustering = clusterGraphNodes(input.nodes || [], {
        seed: workspaceId,
        minClusterSize: 2,
      });

      const syncResponse = await step.run(`persist-clusters-${workspaceId}`, async () => {
        return apiRequest<{ workspace_id: string; clusters_saved: number; nodes_clustered: number }>(
          `/knowledge-graph/internal/${workspaceId}/clusters`,
          {
            method: 'POST',
            body: JSON.stringify({
              k: clustering.k,
              algorithm: 'kmeans++-cosine',
              clusters: clustering.clusters.map((cluster) => ({
                key: cluster.key,
                members: cluster.members,
                metadata: cluster.metadata,
              })),
            }),
          }
        );
      });

      summaries.push({
        workspaceId,
        clustersSaved: syncResponse.clusters_saved,
        nodesClustered: syncResponse.nodes_clustered,
      });
    }

    return {
      workspaceCount: summaries.length,
      summaries,
    };
  }
);
