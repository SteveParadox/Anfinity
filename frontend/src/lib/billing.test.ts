import { describe, expect, it } from 'vitest';

import {
  getUsagePercentage,
  getUsageProgressState,
  isPaidSubscription,
  parseEntitlementMetadata,
} from './billing';
import type { BillingSubscription } from './api';

describe('billing frontend helpers', () => {
  it('renders usage percentages safely for zero, null, missing, and over-limit values', () => {
    expect(getUsagePercentage(null)).toBeNull();
    expect(getUsagePercentage({ current_usage: 0, limit: null })).toBeNull();
    expect(getUsagePercentage({ current_usage: 0, limit: 0 })).toBeNull();
    expect(getUsagePercentage({ current_usage: 25, limit: 100 })).toBe(25);
    expect(getUsagePercentage({ current_usage: 120, limit: 100 })).toBe(120);
  });

  it('classifies usage bar states for upgrade prompts', () => {
    expect(getUsageProgressState(null)).toBe('normal');
    expect(getUsageProgressState(79.9)).toBe('normal');
    expect(getUsageProgressState(80)).toBe('amber');
    expect(getUsageProgressState(100)).toBe('red');
  });

  it('parses entitlement metadata from 402 errors', () => {
    const error = {
      message: 'Upgrade required',
      details: {
      code: 'ENTITLEMENT_LIMIT_REACHED',
      message: 'Limit reached',
      feature_key: 'notes_created_monthly',
      required_plan: 'pro',
      current_plan: 'free',
      limit: 100,
      usage: 100,
      },
    };

    expect(parseEntitlementMetadata(error)).toEqual({
      code: 'ENTITLEMENT_LIMIT_REACHED',
      message: 'Limit reached',
      feature_key: 'notes_created_monthly',
      required_plan: 'pro',
      current_plan: 'free',
      limit: 100,
      usage: 100,
      upgrade_url: null,
    });
    expect(parseEntitlementMetadata({ status: 403, code: 'FORBIDDEN', message: 'Nope' })).toBeNull();
  });

  it('treats missing subscriptions as unpaid/free-safe', () => {
    const proSubscription: BillingSubscription = {
      plan_key: 'pro',
      billing_interval: 'monthly',
      status: 'active',
    };

    expect(isPaidSubscription(null)).toBe(false);
    expect(isPaidSubscription({ ...proSubscription, plan_key: 'free' })).toBe(false);
    expect(isPaidSubscription(proSubscription)).toBe(true);
  });
});
