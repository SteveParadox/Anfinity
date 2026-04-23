import { z } from "zod";

export const FIELD_PATH_PATTERN = /^[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$][A-Za-z0-9_$]*)*$/;
const BLOCKED_FIELD_PATH_SEGMENTS = new Set(["__proto__", "prototype", "constructor"]);
const MAX_REGEX_PATTERN_LENGTH = 512;
const MAX_REGEX_INPUT_LENGTH = 20_000;

export const conditionOperatorSchema = z.enum([
  "equals",
  "not_equals",
  "contains",
  "not_contains",
  "matches_regex",
  "greater_than",
  "less_than",
  "exists",
]);

export type ConditionOperator = z.infer<typeof conditionOperatorSchema>;

export const fieldPathSchema = z
  .string()
  .min(1)
  .max(200)
  .regex(FIELD_PATH_PATTERN, "Field paths must use dot notation, for example note.title")
  .refine((path) => parseFieldPath(path) !== null, "Field paths cannot reference unsafe object prototype segments");

export interface FieldCondition {
  path: string;
  operator: ConditionOperator;
  value?: unknown;
}

export interface ConditionGroup {
  all?: Condition[];
  any?: Condition[];
  not?: Condition;
}

export type Condition = FieldCondition | ConditionGroup;

export const conditionSchema: z.ZodType<Condition> = z.lazy(() =>
  z.union([
    z.object({
      path: fieldPathSchema,
      operator: conditionOperatorSchema,
      value: z.unknown().optional(),
    }),
    z
      .object({
        all: z.array(conditionSchema).min(1).optional(),
        any: z.array(conditionSchema).min(1).optional(),
        not: conditionSchema.optional(),
      })
      .refine((value) => Boolean(value.all || value.any || value.not), {
        message: "Condition groups must include all, any, or not",
      }),
  ])
);

export interface PathResolution {
  found: boolean;
  value: unknown;
}

export function isValidFieldPath(path: string): boolean {
  return parseFieldPath(path) !== null;
}

export function parseFieldPath(path: string): string[] | null {
  if (typeof path !== "string" || path.length === 0 || path.length > 200 || !FIELD_PATH_PATTERN.test(path)) {
    return null;
  }

  const segments = path.split(".");
  if (segments.some((segment) => BLOCKED_FIELD_PATH_SEGMENTS.has(segment))) {
    return null;
  }

  return segments;
}

export function resolvePath(source: unknown, path: string): PathResolution {
  const segments = parseFieldPath(path);
  if (!segments) {
    return { found: false, value: undefined };
  }

  let current: unknown = source;

  for (const segment of segments) {
    if (current === null || current === undefined) {
      return { found: false, value: undefined };
    }

    if (typeof current !== "object") {
      return { found: false, value: undefined };
    }

    if (!Object.prototype.hasOwnProperty.call(current, segment)) {
      return { found: false, value: undefined };
    }

    current = (current as Record<string, unknown>)[segment];
  }

  return { found: true, value: current };
}

export function evaluateConditions(conditions: Condition[] | undefined, context: unknown): boolean {
  if (!conditions || conditions.length === 0) {
    return true;
  }

  return conditions.every((condition) => evaluateCondition(condition, context));
}

export function evaluateCondition(condition: Condition, context: unknown): boolean {
  if (isFieldCondition(condition)) {
    return evaluateFieldCondition(condition, context);
  }

  if (condition.all) {
    return condition.all.every((child) => evaluateCondition(child, context));
  }

  if (condition.any) {
    return condition.any.some((child) => evaluateCondition(child, context));
  }

  if (condition.not) {
    return !evaluateCondition(condition.not, context);
  }

  return false;
}

function isFieldCondition(condition: Condition): condition is FieldCondition {
  return "path" in condition && "operator" in condition;
}

function evaluateFieldCondition(condition: FieldCondition, context: unknown): boolean {
  const resolution = resolvePath(context, condition.path);
  const actual = resolution.value;
  const expected = condition.value;

  switch (condition.operator) {
    case "exists":
      return resolution.found && actual !== null && actual !== undefined;
    case "equals":
      return resolution.found && jsonEquals(actual, expected);
    case "not_equals":
      return !resolution.found || !jsonEquals(actual, expected);
    case "contains":
      return resolution.found && containsValue(actual, expected);
    case "not_contains":
      return !resolution.found || !containsValue(actual, expected);
    case "matches_regex":
      return resolution.found && matchesRegex(actual, expected);
    case "greater_than":
      return resolution.found && compareNumbers(actual, expected, (left, right) => left > right);
    case "less_than":
      return resolution.found && compareNumbers(actual, expected, (left, right) => left < right);
    default:
      return false;
  }
}

function jsonEquals(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) {
    return true;
  }

  if (Array.isArray(left) || Array.isArray(right) || isPlainObject(left) || isPlainObject(right)) {
    return stableStringify(left) === stableStringify(right);
  }

  return false;
}

function containsValue(actual: unknown, expected: unknown): boolean {
  if (typeof actual === "string") {
    return typeof expected === "string" && actual.includes(expected);
  }

  if (Array.isArray(actual)) {
    if (Array.isArray(expected)) {
      return expected.every((item) => actual.some((actualItem) => jsonEquals(actualItem, item)));
    }
    return actual.some((item) => jsonEquals(item, expected));
  }

  if (isPlainObject(actual) && typeof expected === "string") {
    return Object.prototype.hasOwnProperty.call(actual, expected);
  }

  return false;
}

function matchesRegex(actual: unknown, expected: unknown): boolean {
  if (typeof actual !== "string" || typeof expected !== "string") {
    return false;
  }

  if (expected.length === 0 || expected.length > MAX_REGEX_PATTERN_LENGTH || hasUnsafeRegexShape(expected)) {
    return false;
  }

  try {
    const regex = new RegExp(expected);
    return regex.test(actual.slice(0, MAX_REGEX_INPUT_LENGTH));
  } catch {
    return false;
  }
}

function compareNumbers(
  actual: unknown,
  expected: unknown,
  comparator: (left: number, right: number) => boolean
): boolean {
  const left = toFiniteNumber(actual);
  const right = toFiniteNumber(expected);
  if (left === null || right === null) {
    return false;
  }
  return comparator(left, right);
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === "string" && value.trim() !== "") {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  return null;
}

function stableStringify(value: unknown): string {
  return JSON.stringify(stableNormalize(value));
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stableNormalize(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stableNormalize);
  }

  if (!isPlainObject(value)) {
    return value;
  }

  const sorted: Record<string, unknown> = {};
  for (const key of Object.keys(value).sort()) {
    sorted[key] = stableNormalize(value[key]);
  }
  return sorted;
}

function hasUnsafeRegexShape(pattern: string): boolean {
  // Guard the common catastrophic backtracking form: a quantified group that
  // already contains a quantified token, for example ^(a+)+$.
  return /\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*?]|\{\d+(?:,\d*)?\})/.test(pattern);
}
