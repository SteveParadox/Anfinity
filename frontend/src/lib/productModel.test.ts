import { describe, expect, it } from 'vitest';

import {
  BILLING_ADD_ONS,
  PLAN_LABELS,
  PRODUCT_NAME,
  PRODUCT_ONE_LINER,
  formatPlanLabel,
} from './productModel';

describe('product model constants', () => {
  it('uses the Anfinity brand and cited-answer positioning', () => {
    expect(PRODUCT_NAME).toBe('Anfinity');
    expect(PRODUCT_ONE_LINER).toContain('sources');
  });

  it('keeps frontend plan labels aligned to canonical billing ids', () => {
    expect(Object.keys(PLAN_LABELS)).toEqual(['free', 'pro', 'team', 'enterprise']);
    expect(formatPlanLabel('enterprise')).toBe('Enterprise');
    expect(formatPlanLabel('past_due')).toBe('Past Due');
  });

  it('captures usage add-ons without implying self-serve checkout exists', () => {
    expect(BILLING_ADD_ONS.map((addOn) => addOn.name)).toContain('Extra AI answer credits');
    expect(BILLING_ADD_ONS).toHaveLength(5);
  });
});
