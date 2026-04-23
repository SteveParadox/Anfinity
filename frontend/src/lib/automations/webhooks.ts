const WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS = 5 * 60;
const WEBHOOK_DEDUP_TTL_SECONDS = 60;

export interface WebhookSignatureHeaders {
  signature: string | null;
  timestamp: string | null;
}

export interface WebhookDedupResult {
  accepted: boolean;
  key: string;
}

export function readWebhookSignatureHeaders(headers: Headers): WebhookSignatureHeaders {
  return {
    signature: headers.get("x-anfinity-signature"),
    timestamp: headers.get("x-anfinity-timestamp"),
  };
}

export async function verifyWebhookSignature(params: {
  rawBody: string;
  headers: WebhookSignatureHeaders;
  secret?: string;
  now?: Date;
}): Promise<boolean> {
  const secret = params.secret ?? process.env.AUTOMATION_WEBHOOK_SECRET ?? "";
  if (!secret || !params.headers.signature || !params.headers.timestamp) {
    return false;
  }

  const timestampSeconds = Number(params.headers.timestamp);
  if (!Number.isFinite(timestampSeconds)) {
    return false;
  }

  const nowSeconds = Math.floor((params.now ?? new Date()).getTime() / 1000);
  if (Math.abs(nowSeconds - timestampSeconds) > WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS) {
    return false;
  }

  const expected = await hmacSha256Hex(secret, `${params.headers.timestamp}.${params.rawBody}`);
  const received = normalizeSignature(params.headers.signature);
  return constantTimeEqual(expected, received);
}

export async function dedupWebhookDelivery(params: {
  workspaceId: string;
  rawBody: string;
  signature: string;
}): Promise<WebhookDedupResult> {
  const redisUrl = process.env.UPSTASH_REDIS_REST_URL;
  const redisToken = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!redisUrl || !redisToken) {
    throw new Error("UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN are required for webhook deduplication");
  }

  const key = await buildWebhookDedupKey(params);

  const response = await fetch(redisUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${redisToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(["SET", key, "1", "NX", "EX", String(WEBHOOK_DEDUP_TTL_SECONDS)]),
  });

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`Redis dedup request failed (${response.status}): ${body}`);
  }

  const payload = (await response.json()) as { result?: string | null };
  return {
    accepted: payload.result === "OK",
    key,
  };
}

export async function buildWebhookDedupKey(params: {
  workspaceId: string;
  rawBody: string;
  signature: string;
}): Promise<string> {
  const digest = await sha256Hex(`${params.workspaceId}:${params.signature}:${params.rawBody}`);
  return `automation:webhook:dedup:${digest}`;
}

export async function hmacSha256Hex(secret: string, payload: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(payload));
  return bufferToHex(signature);
}

export async function sha256Hex(payload: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
  return bufferToHex(digest);
}

function normalizeSignature(signature: string): string {
  return signature.trim().replace(/^sha256=/i, "").toLowerCase();
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) {
    return false;
  }

  let result = 0;
  for (let index = 0; index < left.length; index += 1) {
    result |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return result === 0;
}

function bufferToHex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
