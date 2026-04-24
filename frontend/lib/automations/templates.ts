import { fieldPathSchema, resolvePath } from "./conditions";

const TEMPLATE_PATTERN = /\{\{\s*([^{}]+?)\s*\}\}/g;

export interface TemplateInterpolationOptions {
  unknownValue?: string;
}

export function interpolateTemplate(
  template: string,
  context: unknown,
  options: TemplateInterpolationOptions = {}
): string {
  const unknownValue = options.unknownValue ?? "";

  return template.replace(TEMPLATE_PATTERN, (_match, rawPath: string) => {
    const path = rawPath.trim();
    if (!fieldPathSchema.safeParse(path).success) {
      return unknownValue;
    }

    const resolution = resolvePath(context, path);
    if (!resolution.found || resolution.value === null || resolution.value === undefined) {
      return unknownValue;
    }

    return stringifyTemplateValue(resolution.value);
  });
}

export function interpolateConfig<T>(value: T, context: unknown): T {
  if (typeof value === "string") {
    return interpolateTemplate(value, context) as T;
  }

  if (Array.isArray(value)) {
    return value.map((item) => interpolateConfig(item, context)) as T;
  }

  if (typeof value === "object" && value !== null) {
    const result: Record<string, unknown> = {};
    for (const [key, child] of Object.entries(value)) {
      result[key] = interpolateConfig(child, context);
    }
    return result as T;
  }

  return value;
}

function stringifyTemplateValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }

  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }

  if (value instanceof Date) {
    return value.toISOString();
  }

  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}
