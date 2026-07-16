#!/bin/zsh
# Cleanly stop the scale-up run so it can be resumed later with scripts/resume_run.sh.
# Safe: run.py flushes each completed cell to disk, so only in-flight cells are lost,
# and --resume re-runs exactly those. No merge happens on a stop (merge is rc=0 only).
cd "$(dirname "$0")/.." || exit 1

pkill -f 'run.py.*benchmark_40x6_rest' 2>/dev/null   # the actual matrix run
pkill -f 'orchestrate_final.sh'        2>/dev/null   # current wrapper, if running
pkill -f 'resume_run.sh'               2>/dev/null   # resume wrapper, if running
pkill -x caffeinate                    2>/dev/null   # let the machine sleep again
sleep 2
docker ps -q | xargs -r docker rm -f >/dev/null 2>&1  # remove leftover containers

rows=$(wc -l < results/benchmark_40x6_rest.jsonl 2>/dev/null | tr -d ' ')
# quick backup in case of a truncated final line (resume tolerates it anyway)
cp results/benchmark_40x6_rest.jsonl "results/benchmark_40x6_rest.jsonl.stopbak" 2>/dev/null
echo "stopped cleanly. rest rows preserved: ${rows}/1800 (backup: benchmark_40x6_rest.jsonl.stopbak)"
echo "resume later (back on wifi) with:  ./scripts/resume_run.sh"
