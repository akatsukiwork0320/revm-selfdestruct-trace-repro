#!/usr/bin/env bash
# Reproduce the revm#3833 downstream probe from pinned commits, then verify with check.py.
#
#   base42 = revm 640eafa9 (PR parent, 42.0.1)          + revm-inspectors ef11a664 (v0.42.2)
#   pr42   = revm 86afe4d3 (PR head as submitted)       + revm-inspectors ef11a664 (v0.42.2)
#   base43 = revm 7e43a49f (main, 43.0.3)               + revm-inspectors 36ae391d (main after v0.44.1)
#   pr43   = revm 7e43a49f + PR diff applied (no commit) + revm-inspectors 36ae391d
#
# Needs git, a Rust toolchain (tested: 1.97.0), python3, and network access to github.com and
# crates.io. Clones and builds go to a fresh temporary directory (several GB) unless WORK is set;
# a reused WORK is verified commit by commit. Outputs go to a new runs/<UTC time>/ directory and
# are never overwritten. Nothing is pushed or posted.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REVM_PR_HEAD=86afe4d3cacc95e8ed30e2ec09a0bf5f41623296
REVM_PR_BASE=640eafa91beae73bafb7776845d53133f603048f
REVM_MAIN=7e43a49fd9fe57a0f6b8bf9779d62664e92e2418
INSP_MAIN=36ae391df0eb59263631763e09c5b629c87a20c7
INSP_042=ef11a664c4de71a51b254ce3ccf6b8353e1cafe0

WORK="${WORK:-$(mktemp -d "${TMPDIR:-/tmp}/revm3833_probe.XXXXXX")}"
OUT="$HERE/runs/$(date -u +%Y%m%dT%H%M%SZ)"
[ -e "$OUT" ] && { echo "refusing to overwrite $OUT" >&2; exit 1; }
mkdir -p "$WORK"
cd "$WORK"
echo "work dir: $WORK"
echo "output:   runs/$(basename "$OUT")"

[ -d revm ] || git clone -q --filter=blob:none --no-checkout https://github.com/bluealloy/revm.git revm
git -C revm fetch -q origin main "+refs/pull/3833/head:refs/remotes/origin/pr3833"
[ -d revm-inspectors ] || git clone -q --filter=blob:none --no-checkout https://github.com/paradigmxyz/revm-inspectors.git revm-inspectors
git -C revm-inspectors fetch -q --tags origin main
have() { git -C "$1" cat-file -e "$2^{commit}" 2>/dev/null || git -C "$1" fetch -q origin "$2"; }
have revm "$REVM_PR_HEAD"; have revm "$REVM_MAIN"
have revm-inspectors "$INSP_MAIN"; have revm-inspectors "$INSP_042"
[ "$(git -C revm rev-parse origin/pr3833)" = "$REVM_PR_HEAD" ] || echo "note: PR #3833 head has moved since pinning; the pinned head is used"
[ "$(git -C revm rev-parse "$REVM_PR_HEAD^")" = "$REVM_PR_BASE" ] || { echo "PR parent mismatch" >&2; exit 1; }

worktree() { # repo dir commit
  [ -d "$2" ] || git -C "$1" worktree add -q --detach "../$2" "$3"
  [ "$(git -C "$2" rev-parse HEAD)" = "$3" ] || { echo "$2: HEAD is not $3" >&2; exit 1; }
}
clean() { [ -z "$(git -C "$1" status --porcelain --untracked-files=no)" ] || { echo "$1: unexpected local changes" >&2; exit 1; }; }
pid() { git patch-id --stable | cut -d' ' -f1; }
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

worktree revm revm-base42 "$REVM_PR_BASE"; clean revm-base42
worktree revm revm-pr42 "$REVM_PR_HEAD";   clean revm-pr42
worktree revm revm-base43 "$REVM_MAIN";    clean revm-base43
worktree revm revm-pr43 "$REVM_MAIN"
if [ -z "$(git -C revm-pr43 status --porcelain --untracked-files=no)" ]; then
  git -C revm-pr43 cherry-pick -n "$REVM_PR_HEAD"
fi
PR_PID="$(git -C revm show "$REVM_PR_HEAD" | pid)"
APPLIED_PID="$(git -C revm-pr43 diff HEAD | pid)"
[ "$PR_PID" = "$APPLIED_PID" ] || { echo "revm-pr43: applied diff is not the PR diff" >&2; exit 1; }
worktree revm-inspectors insp-042 "$INSP_042";   clean insp-042
worktree revm-inspectors insp-main "$INSP_MAIN"; clean insp-main

mkprobe() { # dir revm_req inspectors_dir lockfile
  rm -rf "$1"; mkdir -p "$1/src"
  cp "$HERE/probe/src/main.rs" "$1/src/main.rs"
  sed -e "s#@REVM_REQ@#$2#" -e "s#@INSP_PATH@#$WORK/$3#" "$HERE/probe/Cargo.toml.in" > "$1/Cargo.toml"
  cp "$HERE/lock/$4" "$1/Cargo.lock"
}
mkdir -p "$OUT"   # created only after every worktree passed verification
mkprobe probe42 "42.0.1" insp-042 Cargo.lock.42
mkprobe probe43 "43.0.0" insp-main Cargo.lock.43

run() { # label probe_dir revm_worktree description
  echo "== build and run $1"
  (cd "$2" && cargo build --release -q --locked --config "patch.crates-io.revm.path=\"$WORK/$3/crates/revm\"")
  "$2/target/release/sd_probe" "$1: $4" > "$OUT/out_$1.json"
}
run base42 probe42 revm-base42 "revm $REVM_PR_BASE (PR parent) + revm-inspectors $INSP_042 (v0.42.2)"
run pr42   probe42 revm-pr42   "revm $REVM_PR_HEAD (PR head as submitted) + revm-inspectors $INSP_042 (v0.42.2)"
run base43 probe43 revm-base43 "revm $REVM_MAIN (main) + revm-inspectors $INSP_MAIN (main)"
run pr43   probe43 revm-pr43   "revm $REVM_MAIN (main) + cherry-pick $REVM_PR_HEAD + revm-inspectors $INSP_MAIN (main)"

{
  echo "rustc: $(rustc --version)"
  echo "cargo: $(cargo --version)"
  echo "revm PR head $REVM_PR_HEAD, parent $REVM_PR_BASE, main $REVM_MAIN"
  echo "revm-inspectors main $INSP_MAIN, v0.42.2 $INSP_042"
  echo "pr43 applied diff patch-id = PR patch-id: $APPLIED_PID"
  for f in out_base42.json out_pr42.json out_base43.json out_pr43.json; do
    a="$(sha "$OUT/$f")"; r="$(sha "$HERE/results/$f")"
    echo "$f sha256 $a $([ "$a" = "$r" ] && echo '(identical to results/)' || echo '(differs from results/)')"
  done
} > "$OUT/PROVENANCE.txt"
cat "$OUT/PROVENANCE.txt"

python3 "$HERE/check.py" "$OUT" | tee "$OUT/CHECK.txt"
exit "${PIPESTATUS[0]}"
