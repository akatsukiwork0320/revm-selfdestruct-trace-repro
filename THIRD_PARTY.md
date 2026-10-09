# Third party sources

The original probe reconstructs the fixture in memory and calls upstream libraries. This distribution does not vendor their repositories or binaries.

## trace interop

The T1 input and expected values follow banteg's [mined-probes fixture](https://github.com/banteg/trace-interop/blob/8ce137ed8de8030df622b9710e8a95a4d06b6840/docs/mined-probes.md). The three reported observations correspond to the [transaction payload](https://github.com/banteg/trace-interop/blob/8ce137ed8de8030df622b9710e8a95a4d06b6840/reports/cases/mined-probes/transaction-selfdestruct-self.md), [from-address filter](https://github.com/banteg/trace-interop/blob/8ce137ed8de8030df622b9710e8a95a4d06b6840/reports/cases/mined-probes/filter-self-destruct-from.md), and [to-address filter](https://github.com/banteg/trace-interop/blob/8ce137ed8de8030df622b9710e8a95a4d06b6840/reports/cases/mined-probes/filter-self-destruct-to.md). Its MIT notice is retained below.

```text
MIT License

Copyright (c) 2026 banteg

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## revm

The reproduction script fetches [revm PR 3833](https://github.com/bluealloy/revm/pull/3833), authored by mo4islona, and its pinned comparison commits. This package provides additional validation, not authorship of that upstream fix. revm is MIT-licensed; the following notice is from [the pinned PR head](https://github.com/bluealloy/revm/blob/86afe4d3cacc95e8ed30e2ec09a0bf5f41623296/LICENSE).

```text
MIT License

Copyright (c) 2021-2026 draganrakita

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Libraries and matching rules

- revm-inspectors is used through its public API under its own MIT OR Apache-2.0 terms. The pins are in MANIFEST.json and run.sh.
- The Python checker implements the relevant address-matching rules from [Alloy's TraceFilterMatcher](https://github.com/alloy-rs/alloy/blob/v2.5.0/crates/rpc-types-trace/src/filter.rs) for the queries in this fixture. Alloy is MIT OR Apache-2.0.
- Cargo.lock records other dependencies and versions; their source is downloaded during the build and retains its original license.

This package's MIT license does not replace any dependency or fixture-source license.

