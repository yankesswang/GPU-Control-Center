# Frontend validation · 2026-11-27

- Production build: TypeScript + Vite passed.
- API: 19 tests passed; existing Starlette deprecation warning.
- MCP: real Ollama submit/explain/complete/cancel and invalid ID rejection passed.
- Browser: 1280 desktop; 320, 375, 414, 768 responsive widths checked. No root horizontal overflow in sampled widths. Full mobile GPU cards remain legible.
- GUI real inference: 212325e3 completed, 8 tokens; generation visible in result dialog.
- Checked search empty state, clear filters, installed model listing, modal close and native focus entry.
- Local screenshot: test-results/hardware-dashboard.jpg (ignored artifact).

Hallmark review: P4 H4 E4 S5 R4 V4. Reviewed gate groups for visual/structure, motion, typography, input states, icon consistency, contrast, honest telemetry and responsive layout. Marketing hero/video/testimonial and studied-DNA gates are not applicable. This is the first Hallmark design in this repository; the existing four functional views are deliberately preserved. Panel containers do not visually enclose another card surface. Device illustration is custom SVG with an accessible description; Phosphor provides the interface icons. No fake historical charts or model progress percentages. Colors/fonts use canonical tokens. Reduced-motion removes spatial animation. Legacy stylesheet is preserved and overridden.

Limits: sampled widths are not an exhaustive viewport sweep; screen-reader and operating-system high-contrast audits were not performed. Hardware silhouette is illustrative, not an exact RTX 4090 product rendering. Existing per-host scheduling behavior is unchanged.

## Loaded model inventory

Hardware views now show actual resident models from the configured host Ollama `/api/ps`, separate from installed `/api/tags`. Native Agent includes name, loaded bytes and GPU bytes in heartbeat metrics. Empty, unavailable, pre-upgrade Agent and offline snapshot states are distinct. This inventory is scoped to the configured Ollama service; other inference servers and per-GPU model placement are not inferred.

Validation: TypeScript/Vite production build passed; 24 API/runtime tests passed, including externally loaded models, CPU-only zero VRAM, empty runtime, invalid inventory and connection errors. Live runtime warmed qwen2.5:0.5b for 60 seconds; GUI displayed the name and 0.43 GiB loaded/GPU memory. Test model was explicitly unloaded and `/api/ps` confirmed empty. No other models were unloaded.
