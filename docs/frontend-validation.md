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

## LM Studio / Ollama controls and ComfyUI · 2026-11-27

- PostgreSQL migration 0004 adds persistent per-host default runtime; verified switching to Ollama, reloading and switching back to LM Studio.
- 39 PostgreSQL/runtime tests passed. Covers target-host isolation, duplicate control requests, shared host slot, lifecycle cancellation boundaries, persistent Ollama residency policy, native LM Studio instance IDs, stream completion, unavailable services, and read-only ComfyUI payload filtering.
- LM Studio GUI load/unload succeeded for all-minilm-l6-v2 embedding and translategemma-4b-it. Translation inference a92a375f returned 「你好，世界。」 with 6 tokens; the test model remained resident until explicit UI unload.
- Ollama live API load → inference → unload succeeded; inference result reported model_released=false while manually pinned. MCP real inference/cancel smoke passed.
- Live ComfyUI 0.45.0 reports queue counts, 29 model folders and device VRAM. Expanded diffusion_models and verified real filenames. No workflow mutations were sent.
- Sampled responsive widths 320/375/414/768 have no root horizontal scroll. New controls inherit tokens, 44px buttons, focus styles, loading/disabled feedback and reduced motion. Hardware overview uses a compact model picker; full installed inventory is in the models view.
- Screenshots under ignored test-results/: lmstudio-inference.jpg, comfyui-models.jpg.

Remaining service limits: LM Studio native API does not report per-model VRAM; displayed as unavailable. ComfyUI available files are not claimed to be resident models. External direct requests to any runtime are outside central locking. Native APIs and host configuration references: https://lmstudio.ai/docs/developer/rest/load, https://lmstudio.ai/docs/developer/rest/unload, https://lmstudio.ai/docs/developer/rest/list, https://docs.comfy.org/development/comfyui-server/comms_routes.
