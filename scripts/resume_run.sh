#!/bin/zsh
# Resume the scale-up run: runs only the missing (task,model,trial) cells at
# concurrency 6, keeps the machine awake, then merges pilot + rest into the final
# results file. Launch detached:  nohup ./scripts/resume_run.sh >/dev/null 2>&1 &
cd "$(dirname "$0")/.." || exit 1
set -a; source .env; set +a
MODELS="fw-gpt-oss-120b,fw-kimi-k2p6,fw-deepseek-v4-pro,fw-glm-5p2,oai-gpt-5.5,oai-gpt-5,ant-claude-sonnet-5,goog-gemini-3.5-flash,goog-gemini-3.1-flash-lite"
LOG="results/resume.log"

caffeinate -i -m -s -w $$ &   # keep awake until this script exits

echo "[resume] started $(date)" >> "$LOG"
./.venv/bin/python run.py \
  --tasks tasks/subset40.txt --models "$MODELS" \
  --trials 5 --concurrency 6 --resume \
  --output results/benchmark_40x6_rest.jsonl >> "$LOG" 2>&1
rc=$?
echo "[resume] run exited rc=$rc $(date)" >> "$LOG"

if [ "$rc" -eq 0 ]; then
  cat results/benchmark_40x6_pilot.jsonl results/benchmark_40x6_rest.jsonl > results/benchmark_40x6.jsonl
  echo "[resume] merged -> results/benchmark_40x6.jsonl ($(wc -l < results/benchmark_40x6.jsonl) rows) $(date)" >> "$LOG"
else
  echo "[resume] nonzero rc=$rc; no merge (stop/crash). Re-run this script to continue. $(date)" >> "$LOG"
fi
