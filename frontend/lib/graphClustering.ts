import type { GraphClusterInputNode } from '@/types';

export interface ClusteredGraphMember {
  node_id: string;
  score: number;
  rank: number;
}

export interface ClusteredGraphGroup {
  key: string;
  members: ClusteredGraphMember[];
  centroid: number[];
  metadata: {
    nodeCount: number;
    representativeLabels: string[];
    nodeTypes: Record<string, number>;
  };
}

export interface GraphClusteringResult {
  k: number;
  clusters: ClusteredGraphGroup[];
}

interface WorkingCluster {
  centroid: number[];
  members: GraphClusterInputNode[];
}

function createSeededRandom(seed: string): () => number {
  let hash = 2166136261;
  for (let index = 0; index < seed.length; index += 1) {
    hash ^= seed.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }

  return () => {
    hash += 0x6d2b79f5;
    let value = hash;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

export function cosineSimilarity(left: number[], right: number[]): number {
  if (!left.length || left.length !== right.length) {
    return 0;
  }

  let dot = 0;
  let leftMagnitude = 0;
  let rightMagnitude = 0;
  for (let index = 0; index < left.length; index += 1) {
    const leftValue = left[index] ?? 0;
    const rightValue = right[index] ?? 0;
    dot += leftValue * rightValue;
    leftMagnitude += leftValue * leftValue;
    rightMagnitude += rightValue * rightValue;
  }

  if (!leftMagnitude || !rightMagnitude) {
    return 0;
  }

  return Math.max(-1, Math.min(1, dot / (Math.sqrt(leftMagnitude) * Math.sqrt(rightMagnitude))));
}

function normalizeVector(vector: number[]): number[] {
  const magnitude = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0));
  if (!magnitude) {
    return vector.map(() => 0);
  }
  return vector.map((value) => value / magnitude);
}

function weightedCentroid(nodes: GraphClusterInputNode[]): number[] {
  const dimension = nodes[0]?.embedding.length ?? 0;
  if (!dimension) {
    return [];
  }

  const totals = new Array<number>(dimension).fill(0);
  let totalWeight = 0;

  for (const node of nodes) {
    const weight = Math.max(0.5, node.value || 1);
    totalWeight += weight;
    for (let index = 0; index < dimension; index += 1) {
      totals[index] += (node.embedding[index] ?? 0) * weight;
    }
  }

  if (!totalWeight) {
    return new Array<number>(dimension).fill(0);
  }

  return normalizeVector(totals.map((value) => value / totalWeight));
}

function pickInitialCentroids(
  nodes: GraphClusterInputNode[],
  k: number,
  random: () => number
): number[][] {
  if (!nodes.length || k <= 0) {
    return [];
  }

  const centroids: number[][] = [];
  const firstIndex = Math.floor(random() * nodes.length);
  centroids.push([...nodes[firstIndex].embedding]);

  while (centroids.length < Math.min(k, nodes.length)) {
    const distances = nodes.map((node) => {
      const nearest = centroids.reduce((best, centroid) => {
        const distance = 1 - cosineSimilarity(node.embedding, centroid);
        return Math.min(best, Math.max(0, distance));
      }, Number.POSITIVE_INFINITY);
      return nearest ** 2;
    });

    const totalDistance = distances.reduce((sum, value) => sum + value, 0);
    if (!totalDistance) {
      const fallbackNode = nodes[centroids.length % nodes.length];
      centroids.push([...fallbackNode.embedding]);
      continue;
    }

    let threshold = random() * totalDistance;
    let selectedIndex = distances.length - 1;
    for (let index = 0; index < distances.length; index += 1) {
      threshold -= distances[index] ?? 0;
      if (threshold <= 0) {
        selectedIndex = index;
        break;
      }
    }
    centroids.push([...nodes[selectedIndex].embedding]);
  }

  return centroids.map((centroid) => normalizeVector(centroid));
}

function assignNodes(nodes: GraphClusterInputNode[], centroids: number[][]): WorkingCluster[] {
  const clusters = centroids.map((centroid) => ({ centroid, members: [] as GraphClusterInputNode[] }));
  if (!clusters.length) {
    return [];
  }

  for (const node of nodes) {
    let bestIndex = 0;
    let bestSimilarity = -Infinity;
    for (let index = 0; index < centroids.length; index += 1) {
      const similarity = cosineSimilarity(node.embedding, centroids[index] ?? []);
      if (similarity > bestSimilarity) {
        bestSimilarity = similarity;
        bestIndex = index;
      }
    }
    clusters[bestIndex]?.members.push(node);
  }

  return clusters;
}

function buildClusterKey(index: number, labels: string[]): string {
  const labelSeed = labels
    .flatMap((label) => label.toLowerCase().split(/[^a-z0-9]+/g))
    .filter((token) => token.length >= 3)
    .slice(0, 3)
    .join('-');
  return labelSeed ? `cluster-${index + 1}-${labelSeed}` : `cluster-${index + 1}`;
}

export function estimateOptimalClusterCount(nodeCount: number): number {
  if (nodeCount < 4) {
    return Math.max(1, nodeCount);
  }

  const estimated = Math.round(Math.sqrt(nodeCount / 2));
  const upperBound = Math.min(10, Math.max(2, Math.floor(nodeCount / 2)));
  return Math.max(2, Math.min(upperBound, estimated));
}

export function clusterGraphNodes(
  nodes: GraphClusterInputNode[],
  options?: {
    seed?: string;
    k?: number;
    maxIterations?: number;
    minClusterSize?: number;
  }
): GraphClusteringResult {
  const normalizedNodes = nodes
    .filter((node) => Array.isArray(node.embedding) && node.embedding.length > 0)
    .map((node) => ({
      ...node,
      embedding: normalizeVector(node.embedding),
    }));

  if (!normalizedNodes.length) {
    return { k: 0, clusters: [] };
  }

  const k = Math.max(
    1,
    Math.min(options?.k ?? estimateOptimalClusterCount(normalizedNodes.length), normalizedNodes.length)
  );
  const maxIterations = Math.max(5, options?.maxIterations ?? 24);
  const random = createSeededRandom(options?.seed ?? 'knowledge-graph');
  let centroids = pickInitialCentroids(normalizedNodes, k, random);

  for (let iteration = 0; iteration < maxIterations; iteration += 1) {
    const assigned = assignNodes(normalizedNodes, centroids);
    const nextCentroids = assigned.map((cluster, index) => {
      if (!cluster.members.length) {
        return centroids[index] ?? [];
      }
      return weightedCentroid(cluster.members);
    });

    const centroidShift = nextCentroids.reduce((sum, centroid, index) => {
      const previous = centroids[index] ?? [];
      return sum + (1 - cosineSimilarity(previous, centroid));
    }, 0);

    centroids = nextCentroids;
    if (centroidShift < 0.0005) {
      break;
    }
  }

  const minClusterSize = Math.max(1, options?.minClusterSize ?? 2);
  const finalClusters = assignNodes(normalizedNodes, centroids)
    .filter((cluster) => cluster.members.length >= minClusterSize)
    .map((cluster, index) => {
      const sortedMembers = [...cluster.members]
        .map((member) => ({
          node: member,
          score: Math.max(0, Math.min(1, (cosineSimilarity(member.embedding, cluster.centroid) + 1) / 2)),
        }))
        .sort((left, right) => right.score - left.score || right.node.value - left.node.value);

      const representativeLabels = sortedMembers.slice(0, 4).map((item) => item.node.label);
      const nodeTypes = sortedMembers.reduce<Record<string, number>>((accumulator, item) => {
        accumulator[item.node.type] = (accumulator[item.node.type] ?? 0) + 1;
        return accumulator;
      }, {});

      return {
        key: buildClusterKey(index, representativeLabels),
        centroid: cluster.centroid,
        members: sortedMembers.map((item, memberIndex) => ({
          node_id: item.node.id,
          score: Number(item.score.toFixed(4)),
          rank: memberIndex,
        })),
        metadata: {
          nodeCount: sortedMembers.length,
          representativeLabels,
          nodeTypes,
        },
      };
    });

  return {
    k: finalClusters.length,
    clusters: finalClusters,
  };
}
