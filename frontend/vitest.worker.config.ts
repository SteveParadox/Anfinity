import { defineConfig } from "vitest/config";
import { cloudflareTest } from "@cloudflare/vitest-plugin";

export default defineConfig({
  plugins: [cloudflareTest({
    wrangler: { configPath: "./wrangler.jsonc" },
    miniflare: { bindings: { REALTIME_SERVICE_SECRET: "runtime-test-secret" } },
  })],
  test: { include: ["worker-tests/**/*.test.ts"], testTimeout: 15000 },
});
