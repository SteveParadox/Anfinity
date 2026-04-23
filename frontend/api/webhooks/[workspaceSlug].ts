import { inngest } from "../../inngest/client";
import { eventNameForTrigger } from "../../src/lib/automations/registries";
import { resolveWorkspaceBySlug } from "../../src/lib/automations/backend";
import {
  dedupWebhookDelivery,
  readWebhookSignatureHeaders,
  verifyWebhookSignature,
} from "../../src/lib/automations/webhooks";

export const config = {
  runtime: "edge",
};

export default async function handler(request: Request): Promise<Response> {
  if (request.method !== "POST") {
    return jsonResponse({ error: "Method not allowed" }, 405);
  }

  const workspaceSlug = extractWorkspaceSlug(request.url);
  if (!workspaceSlug) {
    return jsonResponse({ error: "Missing workspace slug" }, 400);
  }

  const rawBody = await request.text();
  const signatureHeaders = readWebhookSignatureHeaders(request.headers);
  const signatureValid = await verifyWebhookSignature({
    rawBody,
    headers: signatureHeaders,
  });

  if (!signatureValid) {
    return jsonResponse({ error: "Invalid webhook signature" }, 401);
  }

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(rawBody) as Record<string, unknown>;
  } catch {
    return jsonResponse({ error: "Webhook body must be valid JSON" }, 400);
  }

  let workspace;
  try {
    workspace = await resolveWorkspaceBySlug(workspaceSlug);
  } catch (error) {
    return jsonResponse(
      {
        error: "Workspace not found",
        detail: error instanceof Error ? error.message : String(error),
      },
      404
    );
  }

  try {
    const dedupResult = await dedupWebhookDelivery({
      workspaceId: workspace.id,
      rawBody,
      signature: signatureHeaders.signature || "",
    });

    if (!dedupResult.accepted) {
      return jsonResponse({ accepted: false, duplicate: true }, 202);
    }
  } catch (error) {
    return jsonResponse(
      {
        error: "Webhook deduplication unavailable",
        detail: error instanceof Error ? error.message : String(error),
      },
      503
    );
  }

  await inngest.send({
    name: eventNameForTrigger("webhook.received"),
    data: {
      source: "external_webhook",
      workspace: {
        id: workspace.id,
        slug: workspace.slug,
        name: workspace.name,
      },
      payload,
    },
  });

  return jsonResponse({ accepted: true, duplicate: false }, 202);
}

function extractWorkspaceSlug(url: string): string | null {
  const pathname = new URL(url).pathname;
  const segments = pathname.split("/").filter(Boolean);
  return segments.at(-1) ?? null;
}

function jsonResponse(payload: unknown, status: number): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}
