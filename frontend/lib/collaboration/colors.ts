const COLLABORATOR_COLORS = [
  "#F97316",
  "#EAB308",
  "#22C55E",
  "#14B8A6",
  "#3B82F6",
  "#8B5CF6",
  "#EC4899",
  "#EF4444",
] as const;

function hashString(value: string): number {
  let hash = 0;

  for (let index = 0; index < value.length; index += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(index);
    hash |= 0;
  }

  return Math.abs(hash);
}

export function getCollaboratorColor(userId: string): string {
  if (!userId) {
    return COLLABORATOR_COLORS[0];
  }

  return COLLABORATOR_COLORS[hashString(userId) % COLLABORATOR_COLORS.length];
}

