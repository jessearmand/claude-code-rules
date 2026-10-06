#!/usr/bin/env bash
# Score the corpus with decision-model GGUFs served one at a time by llama-server (/v1/systemone).
#
#   benchmarks/judge/llamacpp_sweep.sh OUT_DIR NAME=REPO:FILE [NAME=REPO:FILE ...]
#   benchmarks/judge/llamacpp_sweep.sh scratchpad/judge/llamacpp kev-4b-q8=ggml-org/Kev-4B-GGUF:Kev-4B-Q8_0.gguf
#
# Weights come from the Hugging Face cache (`hf download REPO FILE` fetches a missing one). One server runs
# at a time so the judges do not share the GPU. LLAMA_PORT (default 8090) and LLAMA_SERVER_ARGS are honoured.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out_dir="${1:?usage: llamacpp_sweep.sh OUT_DIR NAME=REPO:FILE ...}"
shift
port="${LLAMA_PORT:-8090}"
url="http://127.0.0.1:${port}"
mkdir -p "$out_dir"

server_pid=""
stop_server() {
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
        kill "$server_pid"
        wait "$server_pid" 2>/dev/null || true
    fi
    server_pid=""
}
trap stop_server EXIT

wait_ready() {
    for _ in $(seq 1 120); do
        curl -sf "${url}/health" >/dev/null 2>&1 && return 0
        kill -0 "$server_pid" 2>/dev/null || return 1
        sleep 1
    done
    return 1
}

llama-server --version >"${out_dir}/llama-server.version" 2>&1 || true

for spec in "$@"; do
    name="${spec%%=*}"
    repo_file="${spec#*=}"
    repo="${repo_file%%:*}"
    file="${repo_file#*:}"
    model="$(hf download "$repo" "$file" --quiet)"

    echo "== ${name}: ${repo} ${file}" >&2
    # shellcheck disable=SC2086
    llama-server -m "$model" --host 127.0.0.1 --port "$port" ${LLAMA_SERVER_ARGS:-} >"${out_dir}/${name}.server.log" 2>&1 &
    server_pid=$!
    if ! wait_ready; then
        echo "${name}: server did not become ready, see ${out_dir}/${name}.server.log" >&2
        stop_server
        continue
    fi
    python3 "${here}/run.py" systemone --url "$url" --name "$name" --out "${out_dir}/${name}.jsonl" || true
    stop_server
done
