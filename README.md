# Downstream probe for bluealloy/revm#3833

Does [revm#3833](https://github.com/bluealloy/revm/pull/3833) change what revm-inspectors emits for a post-Cancun `SELFDESTRUCT` to self? This probe answers that for the transaction in trace-interop's mined-probes block 4 (`selfdestruct-self`, [fixture doc](https://github.com/banteg/trace-interop/blob/8ce137ed8de8030df622b9710e8a95a4d06b6840/docs/mined-probes.md)).

## What runs

**The transaction.** It is re-created in memory, not replayed from the chain, and uses a default block environment rather than the mined block header:

- Prague.
- `0x…de57` holds 1000 wei and slot 1 = 42, and runs `ADDRESS SELFDESTRUCT`.
- 200,000 gas, gas price 0.

**The tracing path.** Each build traces it with `TracingInspectorConfig::default_parity()`. It builds parity traces with `into_localized_transaction_traces` and filters them with Alloy's `TraceFilterMatcher`, the same library path Reth's `trace_filter` uses ([reth trace.rs L452–460](https://github.com/paradigmxyz/reth/blob/58a51b3ee3f6714ded9207b244a273c8afb592fd/crates/rpc/rpc/src/trace.rs#L452-L460)). It also builds a callTracer frame.

**Other cases.** Eight more cases cover payload variants and controls; see `probe/src/main.rs`.

| label | revm | revm-inspectors |
|---|---|---|
| base42 | `640eafa9` (PR parent, 42.0.1) | `ef11a664` (v0.42.2) |
| pr42 | `86afe4d3` (PR head as submitted) | `ef11a664` (v0.42.2) |
| base43 | `7e43a49f` (main, 43.0.3) | `36ae391d` (main after v0.44.1) |
| pr43 | `7e43a49f` + PR diff | `36ae391d` |

## Result (reference outputs in `results/`)

| | without #3833 | with #3833 |
|---|---|---|
| suicide `address` / `refundAddress` / `balance` | `0x0` / `0x0` / `0x0` | `…de57` / `…de57` / `0x3e8` |
| `trace_filter` fromAddress=[…de57] | `[]` | `[[0] suicide]` |
| `trace_filter` toAddress=[…de57] | `[[] call]` | `[[] call], [[0] suicide]` |
| callTracer SELFDESTRUCT from / to / value | `0x0` / – / – | `…de57` / `…de57` / `0x3e8` |

- **Rows 1–3** match trace-interop on both sides:
  - The left column matches what trace-interop recorded for Reth 2.7.0 on this transaction.
  - The right column matches the fixture's expected values.
- **Row 4** is an extra check, because mined-probes has no callTracer request.
- **Both builds agree.** The result is the same for the PR as submitted (42.0.1) and applied to main (43.0.3).
- **Five controls are identical before and after:**
  - beneficiary is another address (Prague)
  - beneficiary is self on Shanghai
  - create-and-destroy in one transaction
  - SELFDESTRUCT fails inside STATICCALL; the outer transaction succeeds
  - out of gas at SELFDESTRUCT
- **The recorded fields of the returned state are identical in all nine cases.** This covers balance, nonce, code hash, present storage values and account status; it is not a comparison of committed database state roots.
- **Three more cases change only in the suicide payload** and in the `trace_filter` matches it produces:
  - tx value 459 wei (balance becomes `0x5b3`)
  - DELEGATECALL
  - outer REVERT. Here the newly matchable suicide record is a traced action, not a committed transfer.

## Not covered

- A Reth binary run, chain replay, stateDiff, vmTrace and performance. The PR adds one opcode comparison per step in the inspector loop.
- Whether the hook should fire when nothing is destroyed. That is a design question for the maintainers; this probe only shows the downstream effect.

## Reproduce

```bash
bash run.sh                           # git, Rust (tested 1.97.0), python3; downloads and builds dependencies
python3 check.py results              # verify the reference outputs
python3 check.py --selftest results   # the checker must reject 16 kinds of tampering (Python 3.9+)
```

`run.sh` does the following:

1. It pins five commits and works in a fresh temporary directory, which takes several GB.
2. It checks each worktree HEAD and tracked-file changes, and compares the main+PR patch-id with the PR. Untracked files are excluded; patch-id ignores whitespace differences. The default fresh workspace avoids reusing local worktrees.
3. It builds with the committed `Cargo.lock` files (`--locked`).
4. It writes to a new `runs/<UTC time>/`, records versions and hashes in `PROVENANCE.txt`, and exits with `check.py`'s status.

`check.py` recomputes the T1 pass/fail flags from the raw trace data. It also requires the probe's geth/parity state-match flag to be true; the two states are not stored separately for independent recomputation. For every build and all nine cases it checks:

- the suicide records and SELFDESTRUCT frames: position, exact keys, and expected address / beneficiary / balance;
- the execution flag and key post-state facts;
- every `trace_filter` result, recomputed from the parity traces with Alloy's `TraceFilterMatcher` rules.

Across each before/after pair it requires:

- identical state;
- fully identical controls;
- no difference outside the suicide payload and the filter matches it produces.

The C3 created address is taken from its create record rather than computed from scratch.

## Files

| path | content |
|---|---|
| `probe/src/main.rs`, `probe/Cargo.toml.in` | the nine cases and the probe crate template |
| `run.sh`, `check.py` | reproduction and fail-fast verification |
| `results/out_{base42,pr42,base43,pr43}.json` | reference outputs |
| `lock/Cargo.lock.{42,43}` | dependency versions used |
| `MANIFEST.json` | SHA-256 of the other distribution files; excludes itself and generated runs |
| `LICENSE`, `THIRD_PARTY.md` | package license and source attribution |

Written and run with AI assistance. A second run in a separate environment reproduced the four outputs byte for byte.

## License and attribution

The original harness, checker, documentation and generated reference results are provided under the [MIT License](LICENSE), to the extent rights are held by the contributor. Third-party materials retain their original licenses; see [THIRD_PARTY.md](THIRD_PARTY.md). Dependencies and the upstream PR are fetched from their own repositories and are not relicensed by this package.
