#!/usr/bin/env bash
#
# The whole round-7 measurement campaign, in the order it must run.
#
# WHY ONE SCRIPT. Round 7 is a complete refresh of 54 reports and nine sweeps plus a new
# profile, and the pieces have ordering constraints that are easy to get wrong by hand:
#
#   * Stages 3, 4 and 7 all pin the capture to port 8799 so the containerised gateways can
#     reach it at a fixed address. Two of them at once fight over that socket -- the exact
#     failure `_stop` and `Connection: close` exist to close -- so they are serialised
#     here and are never backgrounded.
#   * Stages 1, 2 and 6 use ephemeral ports and parallelise internally.
#   * Stage 5 costs money. It is in the script because the repository owner authorised it
#     on 2026-09-09; it is last among the refresh stages so that a failure earlier does not
#     spend anything.
#
# NOTHING HERE WRITES TO A PUBLISHED DIRECTORY. Every stage writes to a staging tree.
# Promotion is a separate, deliberate step after `compare` has been read.
#
# Usage:
#   bash benchmarks/round7-campaign.sh              # everything
#   bash benchmarks/round7-campaign.sh 1 2 3        # selected stages
#
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH="$PWD/pii-leak-benchmark${PYTHONPATH:+:$PYTHONPATH}"

STAGES="${*:-1 2 3 4 5 6 7}"
want() { case " $STAGES " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
banner() { printf '\n\n########## STAGE %s: %s\n\n' "$1" "$2"; }

FAILED=""
note_failure() { FAILED="$FAILED $1"; printf '\n!!!!! STAGE %s FAILED (exit %s)\n' "$1" "$2"; }

if want 1; then
  banner 1 "v2 local, exhaustive-2-part, and the two union oracles"
  python benchmarks/refresh_v2_evidence.py local exhaustive presidio worstcase worstcase-presidio
  [ $? -eq 0 ] || note_failure 1 $?
fi

if want 2; then
  banner 2 "seed-sweep.json -- 7 policies x 12 seeds, one process per policy"
  python benchmarks/refresh_v2_evidence.py sweep
  [ $? -eq 0 ] || note_failure 2 $?
fi

if want 3; then
  banner 3 "the eight external gateway single-run rows (capture on 8799)"
  OUT=benchmarks/results/staging-refresh bash benchmarks/rerun-external-gateway-rows.sh
  [ $? -eq 0 ] || note_failure 3 $?
fi

if want 4; then
  banner 4 "the eight per-target seed sweeps (capture on 8799)"
  python benchmarks/refresh_gateway_sweeps.py
  [ $? -eq 0 ] || note_failure 4 $?
fi

if want 5; then
  banner 5 "GOOGLE CLOUD -- BILLED. DLP and Model Armor, midpoint and exhaustive-2-part"
  python benchmarks/refresh_v2_evidence.py gcp
  [ $? -eq 0 ] || note_failure 5 $?
fi

if want 6; then
  banner 6 "the FIDE profile: midpoint sweep, two-part sweep, union, and the Shield row"
  python benchmarks/fide_sweep.py midpoint twopart worstcase shield summarise
  [ $? -eq 0 ] || note_failure 6 $?
fi

if want 7; then
  banner 7 "LLM Guard under the two-part oracle over its own six seeds (capture on 8799)"
  python benchmarks/llm_guard_exhaustive.py twopart
  [ $? -eq 0 ] || note_failure 7 $?
fi

printf '\n\n########## CAMPAIGN DONE\n'
if [ -n "$FAILED" ]; then
  printf 'FAILED STAGES:%s\n' "$FAILED"
  printf 'A failed stage is a gap to document, not a row to leave stale.\n'
  exit 1
fi
printf 'All requested stages completed. Nothing is published yet.\n'
printf 'Next: python benchmarks/refresh_v2_evidence.py compare\n'
