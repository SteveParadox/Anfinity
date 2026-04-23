import { Badge } from '@/components/ui/badge';
import type { ApprovalWorkflowStatus } from '@/types';


const STATUS_LABELS: Record<ApprovalWorkflowStatus, string> = {
  draft: 'Draft',
  submitted: 'Submitted',
  needs_changes: 'Needs Changes',
  approved: 'Approved',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
};

const STATUS_CLASSES: Record<ApprovalWorkflowStatus, string> = {
  draft: 'border-zinc-700 bg-zinc-900 text-zinc-200',
  submitted: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  needs_changes: 'border-orange-500/40 bg-orange-500/10 text-orange-300',
  approved: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  rejected: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
  cancelled: 'border-slate-600 bg-slate-900 text-slate-300',
};

export function ApprovalStatusBadge({ status }: { status: ApprovalWorkflowStatus }) {
  return (
    <Badge variant="outline" className={STATUS_CLASSES[status]}>
      {STATUS_LABELS[status]}
    </Badge>
  );
}
