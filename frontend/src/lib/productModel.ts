export const PRODUCT_NAME = 'Anfinity';

export const PRODUCT_TAGLINE = "Your team's knowledge, searchable by meaning.";

export const PRODUCT_POSITIONING =
  'Anfinity is an AI knowledge operating system for teams that need to capture, organize, search, understand, and approve knowledge across notes, documents, and connected tools.';

export const PRODUCT_ONE_LINER =
  'Ask questions across your team docs, notes, and decisions, then get answers with sources.';

export const PRODUCT_SUBHEADLINE =
  'Upload notes and documents, connect your tools, ask questions, and get grounded answers with sources, highlights, and workspace-level permissions.';

export const SIGNUP_VALUE_PROPS = [
  'Semantic search across notes and documents',
  'Grounded answers with citations and source cards',
  'Workspace permissions for team knowledge',
  'Feedback loops for retrieval quality',
] as const;

export const PLAN_LABELS: Record<string, string> = {
  free: 'Free',
  pro: 'Pro',
  team: 'Team',
  enterprise: 'Enterprise',
};

export const BILLING_ADD_ONS = [
  { name: 'Extra AI answer credits', price: '$10 / 1,000 credits' },
  { name: 'Extra document processing', price: '$15 / 1,000 pages' },
  { name: 'Extra storage', price: '$10 / 100GB' },
  { name: 'Premium integrations pack', price: '$10 / user / month' },
  { name: 'White-label workspace', price: '$99 / month' },
] as const;

export function formatPlanLabel(plan?: string | null): string {
  if (!plan) return PLAN_LABELS.free;
  return PLAN_LABELS[plan] ?? plan.replace(/(^|_)([a-z])/g, (_match, prefix: string, letter: string) => (
    `${prefix ? ' ' : ''}${letter.toUpperCase()}`
  ));
}
