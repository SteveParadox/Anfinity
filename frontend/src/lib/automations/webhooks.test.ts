import { afterEach, describe, expect, it, vi } from "vitest";

import { buildWebhookDedupKey, dedupWebhookDelivery, hmacSha256Hex, verifyWebhookSignature } from "./webhooks";

describe("automation webhook security", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("verifies HMAC signatures over timestamp and raw body", async () => {
    const rawBody = JSON.stringify({ hello: "world" });
    const timestamp = "1776902400";
    const secret = "test-secret";
    const signature = await hmacSha256Hex(secret, `${timestamp}.${rawBody}`);

    await expect(
      verifyWebhookSignature({
        rawBody,
        secret,
        now: new Date(Number(timestamp) * 1000),
        headers: {
          timestamp,
          signature: `sha256=${signature}`,
        },
      })
    ).resolves.toBe(true);
  });

  it("rejects stale signatures", async () => {
    await expect(
      verifyWebhookSignature({
        rawBody: "{}",
        secret: "test-secret",
        now: new Date("2026-04-23T00:10:00Z"),
        headers: {
          timestamp: String(Date.parse("2026-04-23T00:00:00Z") / 1000),
          signature: "sha256=bad",
        },
      })
    ).resolves.toBe(false);
  });

  it("uses Redis SET NX EX for 60-second deduplication", async () => {
    vi.stubEnv("UPSTASH_REDIS_REST_URL", "https://redis.example.com");
    vi.stubEnv("UPSTASH_REDIS_REST_TOKEN", "redis-token");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ result: "OK" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await dedupWebhookDelivery({
      workspaceId: "workspace-1",
      rawBody: "{}",
      signature: "sig",
    });

    expect(result.accepted).toBe(true);
    expect(fetchMock).toHaveBeenCalledOnce();
    const firstCall = (fetchMock.mock.calls as unknown as Array<[string, RequestInit]>)[0];
    expect(firstCall).toBeDefined();
    const body = JSON.parse(String(firstCall![1].body));
    expect(body.slice(-3)).toEqual(["NX", "EX", "60"]);
  });

  it("builds stable dedup keys from workspace, signature, and raw body", async () => {
    const first = await buildWebhookDedupKey({ workspaceId: "workspace-1", rawBody: "{}", signature: "sig" });
    const second = await buildWebhookDedupKey({ workspaceId: "workspace-1", rawBody: "{}", signature: "sig" });
    const differentBody = await buildWebhookDedupKey({ workspaceId: "workspace-1", rawBody: "{\"x\":1}", signature: "sig" });

    expect(first).toBe(second);
    expect(first).toMatch(/^automation:webhook:dedup:[a-f0-9]{64}$/);
    expect(first).not.toBe(differentBody);
  });

  it("marks duplicate deliveries as not accepted", async () => {
    vi.stubEnv("UPSTASH_REDIS_REST_URL", "https://redis.example.com");
    vi.stubEnv("UPSTASH_REDIS_REST_TOKEN", "redis-token");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ result: null }), { status: 200 })));

    await expect(
      dedupWebhookDelivery({
        workspaceId: "workspace-1",
        rawBody: "{}",
        signature: "sig",
      })
    ).resolves.toMatchObject({ accepted: false });
  });

  it("fails closed when Redis deduplication is not configured", async () => {
    vi.stubEnv("UPSTASH_REDIS_REST_URL", "");
    vi.stubEnv("UPSTASH_REDIS_REST_TOKEN", "");

    await expect(
      dedupWebhookDelivery({
        workspaceId: "workspace-1",
        rawBody: "{}",
        signature: "sig",
      })
    ).rejects.toThrow("required for webhook deduplication");
  });
});
