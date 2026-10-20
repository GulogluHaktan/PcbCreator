#!/usr/bin/env bash
# Serve the local model (Ternary Bonsai 27B) via PrismML's llama.cpp fork.
# OpenAI-compatible API on http://127.0.0.1:8080  (pcbc design talks to this).
set -euo pipefail
LLM_HOME="${LLM_HOME:-$HOME/llm}"
BIN="$LLM_HOME/prism-llama/llama-prism-b10787-73bfe6c"
MODEL="${MODEL:-$LLM_HOME/models/Ternary-Bonsai-27B-PQ2_0.gguf}"
NV="$(echo "$LLM_HOME"/cudalibs/lib/python3.*/site-packages/nvidia/*/lib | tr ' ' ':')"
export LD_LIBRARY_PATH="$BIN:$NV:${LD_LIBRARY_PATH:-}"
exec "$BIN/llama-server" -m "$MODEL" --host 127.0.0.1 --port "${PORT:-8080}" \
  ${NGL:+-ngl "$NGL"} -c "${CTX:-16384}" -np 1 --jinja "$@"
