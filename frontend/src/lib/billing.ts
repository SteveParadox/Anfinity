import type { ApiError, BillingPlanDefinition, BillingSubscription, BillingUsageMetric } from '@/lib/api';

export type BillingInterval = 'monthly' | 'annual';

export interface EntitlementMetadata {
  code: string;
  message: string;
  feature_key: string;
  required_plan: string | null;
  current_plan: string;
  limit: number | null;
  usage: number | null;
  upgrade_url?: string | null;
}

export function formatCurrencyFromCents(cents: number, currency: string = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format((cents || 0) / 100);
}

export function getPlanPriceCents(plan: BillingPlanDefinition, interval: BillingInterval): number | null {
  if (plan.monthly_price_cents === 0 && plan.annual_price_cents === null) {
    return null;
  }
  if (interval === 'annual') {
    return plan.annual_per_month_cents ?? plan.monthly_price_cents;
  }
  return plan.monthly_price_cents;
}

export function getPlanAnnualSavingsLabel(plan: BillingPlanDefinition, currency: string = 'USD'): string | null {
  if (!plan.annual_savings_cents || plan.annual_savings_cents <= 0) {
    return null;
  }
  return `${formatCurrencyFromCents(plan.annual_savings_cents, currency)} saved yearly`;
}

export function getUsagePercentage(metric: Partial<BillingUsageMetric> | null | undefined): number | null {
  const limit = typeof metric?.limit === 'number' ? metric.limit : null;
  if (limit === null || limit <= 0) {
    return null;
  }
  const usage = typeof metric?.current_usage === 'number' ? metric.current_usage : 0;
  const percentage = (usage / limit) * 100;
  return Math.max(0, percentage);
}

export function getUsageProgressState(percentage: number | null): 'normal' | 'amber' | 'red' {
  if (percentage === null) return 'normal';
  if (percentage >= 100) return 'red';
  if (percentage >= 80) return 'amber';
  return 'normal';
}

export function calculateProjectedMonthlyCost(input: {
  usage: number;
  limit: number | null;
  overageRateCents: number | null;
  elapsedDays: number;
  totalDays: number;
}): {
  projectedUsage: number;
  projectedOverageUnits: number;
  projectedOverageCents: number;
} {
  const usage = Math.max(0, input.usage || 0);
  const elapsedDays = Math.max(1, input.elapsedDays || 1);
  const totalDays = Math.max(elapsedDays, input.totalDays || elapsedDays);
  const projectedUsage = Math.round((usage / elapsedDays) * totalDays);
  if (input.limit === null || input.overageRateCents === null) {
    return {
      projectedUsage,
      projectedOverageUnits: 0,
      projectedOverageCents: 0,
    };
  }
  const projectedOverageUnits = Math.max(0, projectedUsage - input.limit);
  return {
    projectedUsage,
    projectedOverageUnits,
    projectedOverageCents: projectedOverageUnits * input.overageRateCents,
  };
}

export function parseEntitlementMetadata(error: unknown): EntitlementMetadata | null {
  const apiError = error as ApiError & { details?: Record<string, any> };
  const metadata = apiError?.details as Partial<EntitlementMetadata> | undefined;
  if (!metadata || typeof metadata !== 'object') {
    return null;
  }
  if (!metadata.feature_key || !metadata.current_plan) {
    return null;
  }
  return {
    code: String(metadata.code || 'ENTITLEMENT_REQUIRED'),
    message: String(metadata.message || apiError?.message || 'Upgrade required'),
    feature_key: String(metadata.feature_key),
    required_plan: metadata.required_plan ? String(metadata.required_plan) : null,
    current_plan: String(metadata.current_plan),
    limit: typeof metadata.limit === 'number' ? metadata.limit : null,
    usage: typeof metadata.usage === 'number' ? metadata.usage : null,
    upgrade_url: metadata.upgrade_url ? String(metadata.upgrade_url) : null,
  };
}

export function isPaidSubscription(subscription: BillingSubscription | null | undefined): boolean {
  return Boolean(subscription && subscription.plan_key !== 'free');
}
