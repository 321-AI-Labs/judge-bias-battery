# Reproduction Environment — Judge Serving

This note describes the serving environment used for the P8 judge-reliability
runs in generic terms. Absolute paths, drive letters, and host-specific details
from the lab-internal serving config are intentionally omitted.

## Judges

Three open-weight judges were served locally, each behind an OpenAI-compatible
`/v1/chat/completions` endpoint on `127.0.0.1` (one port per judge):

| Judge | Model | Format / quantization | Serving engine |
|---|---|---|---|
| glm | GLM-4-9B-Chat | GGUF, Q4_K_M | llama.cpp `llama-server`, build b10930 |
| llama | Meta-Llama-3.1-8B-Instruct | GGUF, Q4_K_M | llama.cpp `llama-server`, build b10930 |
| qwen | Qwen3-8B | GGUF, Q4_K_M (Ollama pull, layer blob reused read-only) | llama.cpp `llama-server`, build b10930 |

## Serving parameters

- **CPU-only inference** (`-ngl 0`): GPU reserved for unrelated benchmarking;
  judges never touched it, by design, for bench isolation.
- **Context:** 8192 tokens (`-c 8192`); **threads:** 16 (`-t 16`).
- **Qwen3 reasoning disabled** (`--reasoning off`): a live probe showed thinking
  mode consumed its token budget without emitting a verdict; GLM-4-9B and
  Llama-3.1-8B are non-thinking models, so Qwen3 was run non-thinking for a
  fair same-rubric comparison. The harness parser still tolerates
  `reasoning_content` / `<think>` response shapes as a robustness measure.
- **OS:** Windows 11; engine binary integrity pinned by build tag (b10930).

## Call parameters (recorded per call in the verbatim logs)

- `temperature: 0.0` (deterministic decoding), per-call `seed`, `max_tokens: 256`.
- Health check: `GET /v1/models` returns 200 before runs.

Model artifacts were integrity-checked at fetch time (expected vs. downloaded
byte counts, source repository commit pins). See `examples/endpoints.example.json`
for the sanitized config schema.
