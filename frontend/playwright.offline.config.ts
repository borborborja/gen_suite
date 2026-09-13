import { defineConfig } from "@playwright/test";
import ingestion from "./playwright.ingestion.config";

// Reuses the isolated Vite harness, not an installed application.
export default defineConfig(ingestion, {
  testMatch: ["ingestion-unit.spec.ts", "ingestion-ui.spec.ts", "ingest-cleanup-ui.spec.ts", "ai-budget-ui.spec.ts", "research-runs-ui.spec.ts", "indexes-ui.spec.ts", "authorizations-ui.spec.ts", "storage-inventory-ui.spec.ts", "storage-quota-ui.spec.ts", "exports-ui.spec.ts", "gedcom-ui.spec.ts"],
});
