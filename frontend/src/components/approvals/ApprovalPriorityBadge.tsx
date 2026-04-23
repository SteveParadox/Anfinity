import type { ComponentType } from 'react';
import { AlertTriangle, ArrowDown, ArrowUp, ChevronsUp } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import type { ApprovalWorkflowPriority } from '@/types';


const PRIORITY_LABELS: Record<ApprovalWorkflowPriority, string> = {
  low: 'Low',
  normal: 'Normal',
  high: 'High',
  critical: 'Critical',
};

const PRIORITY_CLASSES: Record<ApprovalWorkflowPriority, string> = {
  low: 'border-zinc-700 bg-zinc-900 text-zinc-300',
  normal: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  high: 'border-orange-500/40 bg-orange-500/10 text-orange-300',
  critical: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
};

const PRIORITY_ICONS = {
  low: ArrowDown,
  normal: ArrowUp,
  high: ChevronsUp,
  critical: AlertTriangle,
} satisfies Record<ApprovalWorkflowPriority, ComponentType<{ className?: string }>>;


export function ApprovalPriorityBadge({ priority }: { priority: ApprovalWorkflowPriority }) {
  const Icon = PRIORITY_ICONS[priority];
  return (
    <Badge variant="outline" className={PRIORITY_CLASSES[priority]}>
      <Icon className="size-3.5" />
      {PRIORITY_LABELS[priority]}
    </Badge>
  );
}
