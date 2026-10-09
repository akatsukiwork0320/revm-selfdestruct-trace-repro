"""Fail-fast verifier for the revm#3833 downstream probe outputs.

Usage:
    python3 check.py <results_dir>             # exit 0 only if every assertion holds
    python3 check.py --selftest <results_dir>  # tamper with copies; every mutation must be rejected

Every expectation is recomputed from the raw outputs or fixed in this file; the pass/fail flags
written by the probe are only compared against the recomputation, never trusted.

Per output file (base42, pr42, base43, pr43):
  * structure: exact top-level and per-case keys, the nine cases in order, spec per case,
    and a variant label naming the pinned commits;
  * execution success per case, and the probe's own geth-run/parity-run state cross-check is true;
  * post-state facts per case (balances, whether the account was selfdestructed);
  * every `suicide` record (position, address, refundAddress, balance, exact keys) and every geth
    SELFDESTRUCT frame (from/to/value, exact keys) equals the expected payload: correct values
    with the PR and for the controls; zero address / zero balance (geth: from 0x0, no to/value)
    in T1, T2, C5 and C6 without the PR;
  * every trace_filter result is recomputed from the parity traces with Alloy's
    TraceFilterMatcher rules (alloy-rpc-types-trace 2.5.0, intersection mode) and must match.
Per pair (base42 -> pr42, base43 -> pr43):
  * T1's nine checks go from [pass, fail x8] to all pass;
  * execution and post-state identical in all nine cases; the five controls identical in full;
  * in T1, T2, C5 and C6 the only differences are suicide action address/refundAddress/balance,
    SELFDESTRUCT frame from/to/value, and the trace_filter matches that follow from them.
Standard library only.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

T1, T2 = "T1_fixture_selfdestruct_self", "T2_selfdestruct_self_with_tx_value"
C1, C2 = "C1_prague_other_beneficiary", "C2_shanghai_selfdestruct_self"
C3, C4 = "C3_prague_create_and_destroy_same_tx", "C4_prague_staticcall_into_selfdestruct"
C5, C6 = "C5_prague_outer_revert", "C6_prague_delegatecall_selfdestruct_self"
C7 = "C7_prague_selfdestruct_out_of_gas"
CASES = [T1, T2, C1, C2, C3, C4, C5, C6, C7]
CONTROLS = {C1, C2, C3, C4, C7}
BUGGY = {T1, T2, C5, C6}  # without the PR, these emit an empty suicide payload
PAIRS = [("base42", "pr42"), ("base43", "pr43")]


def addr(suffix):
    return "0x" + suffix.rjust(40, "0")


ZERO, DE57, BEEF, A1 = addr(""), addr("de57"), addr("beef"), addr("a1")
FAC0, IMPL = addr("fac0"), addr("1111")
CREATED = "created"  # resolved from the create trace at [0] in C3

SPEC = {c: "PRAGUE" for c in CASES} | {C2: "SHANGHAI"}
SUCCESS = {T1: True, T2: True, C1: True, C2: True, C3: True, C4: True, C5: False, C6: True, C7: False}
WATCHED = {T1: [DE57], T2: [DE57], C1: [DE57], C2: [DE57], C3: [FAC0], C4: [A1, DE57],
           C5: [A1, DE57], C6: [A1, IMPL], C7: [DE57]}
# (traceAddress, address, refundAddress, balance) with the PR, and for the controls in both builds
SUICIDES = {
    T1: [([0], DE57, DE57, "0x3e8")],
    T2: [([0], DE57, DE57, "0x5b3")],      # 1000 wei + 459 wei sent by the transaction
    C1: [([0], DE57, BEEF, "0x3e8")],
    C2: [([0], DE57, DE57, "0x3e8")],
    C3: [([0, 0], CREATED, CREATED, "0x100")],
    C4: [],
    C5: [([0, 0], DE57, DE57, "0x3e8")],
    C6: [([0, 0], A1, A1, "0x1f4")],
    C7: [],
}
# (address, balance, selfdestructed) facts about the returned post-state
POST_STATE = {
    T1: [(DE57, "0x3e8", False)],           # trace-interop H26: the contract survives
    T2: [(DE57, "0x5b3", False)],
    C1: [(DE57, "0x0", False), (BEEF, "0x3e8", False)],
    C2: [(DE57, "0x0", True)],              # pre-Cancun: destroyed
    C3: [(CREATED, "0x0", True)],
    C4: [(DE57, "0x3e8", False)],
    C5: [(DE57, "0x3e8", False)],
    C6: [(A1, "0x1f4", False)],
    C7: [(DE57, "0x3e8", False)],
}
PR_HEAD = "86afe4d3cacc95e8ed30e2ec09a0bf5f41623296"
LABELS = {
    "base42": ["640eafa91beae73bafb7776845d53133f603048f", "ef11a664c4de71a51b254ce3ccf6b8353e1cafe0"],
    "pr42": [PR_HEAD, "ef11a664c4de71a51b254ce3ccf6b8353e1cafe0"],
    "base43": ["7e43a49fd9fe57a0f6b8bf9779d62664e92e2418", "36ae391df0eb59263631763e09c5b629c87a20c7"],
    "pr43": ["7e43a49fd9fe57a0f6b8bf9779d62664e92e2418", "cherry-pick " + PR_HEAD,
             "36ae391df0eb59263631763e09c5b629c87a20c7"],
}
DOC_KEYS = {"variant", "t1_fixture_checks", "cases"}
CASE_KEYS = {"id", "note", "spec", "execution", "post_state", "geth_run_state_matches_parity_run",
             "parity_traces", "trace_filter", "geth_call_frame"}
TRACE_KEYS = {"action", "result", "subtraces", "traceAddress", "type"}
SD_FRAME_KEYS = {"from", "gas", "gasUsed", "input", "to", "type", "value"}
SD_FRAME_KEYS_EMPTY = {"from", "gas", "gasUsed", "input", "type"}
MISSING = object()


class Fail(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise Fail(msg)


def low(v):
    return v.lower() if isinstance(v, str) else v


def load(d, label):
    p = pathlib.Path(d) / f"out_{label}.json"
    need(p.is_file(), f"missing {p.name}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Fail(f"{p.name}: invalid JSON ({e})")


def leaf_diffs(x, y, path=()):
    """Yield (path, x, y) for every differing leaf; added or removed keys are reported per key."""
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(x.keys() | y.keys()):
            yield from leaf_diffs(x.get(k, MISSING), y.get(k, MISSING), path + (k,))
    elif isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
        for i, (a, b) in enumerate(zip(x, y)):
            yield from leaf_diffs(a, b, path + (i,))
    elif type(x) is not type(y) or x != y:
        yield path, x, y


def geth_frames(frame, path=()):
    yield path, frame
    for i, child in enumerate(frame.get("calls") or []):
        yield from geth_frames(child, path + ("calls", i))


def created_address(case):
    creates = [t for t in case["parity_traces"] if t.get("type") == "create" and t.get("traceAddress") == [0]]
    need(len(creates) == 1, f"{case['id']}: expected one create trace at [0]")
    need(low(creates[0]["action"].get("value")) == "0x100", f"{case['id']}: create value is not 0x100")
    result = creates[0].get("result") or {}
    need(isinstance(result.get("address"), str), f"{case['id']}: create trace has no result address")
    return low(result["address"])


def expected_suicides(cid, case, fixed):
    out = []
    for ta, a, r, bal in SUICIDES[cid]:
        if cid in BUGGY and not fixed:
            out.append((ta, ZERO, ZERO, "0x0"))
        else:
            a = created_address(case) if a == CREATED else a
            r = created_address(case) if r == CREATED else r
            out.append((ta, a, r, bal))
    return out


def check_suicide_payloads(label, cid, case, fixed):
    exp = expected_suicides(cid, case, fixed)
    got = [t for t in case["parity_traces"] if t.get("type") == "suicide"]
    need(len(got) == len(exp), f"{label} {cid}: {len(got)} suicide records, expected {len(exp)}")
    for t, (ta, a, r, bal) in zip(got, exp):
        act = t.get("action", {})
        need(set(t) == TRACE_KEYS and set(act) == {"address", "refundAddress", "balance"},
             f"{label} {cid}: unexpected keys in suicide record {sorted(t)} / {sorted(act)}")
        need(t["traceAddress"] == ta and t["result"] is None and t["subtraces"] == 0,
             f"{label} {cid}: suicide record shape/position {t['traceAddress']} != {ta}")
        need((low(act["address"]), low(act["refundAddress"]), low(act["balance"])) == (a, r, bal),
             f"{label} {cid}: suicide payload {act} != {(a, r, bal)}")
    frames = [f for _, f in geth_frames(case["geth_call_frame"]) if f.get("type") == "SELFDESTRUCT"]
    need(len(frames) == len(exp), f"{label} {cid}: {len(frames)} SELFDESTRUCT frames, expected {len(exp)}")
    for f, (_, a, r, bal) in zip(frames, exp):
        if cid in BUGGY and not fixed:
            need(set(f) == SD_FRAME_KEYS_EMPTY and low(f["from"]) == ZERO,
                 f"{label} {cid}: SELFDESTRUCT frame {f} is not the expected empty payload")
        else:
            need(set(f) == SD_FRAME_KEYS, f"{label} {cid}: SELFDESTRUCT frame keys {sorted(f)}")
            need((low(f["from"]), low(f["to"]), low(f["value"])) == (a, r, bal),
                 f"{label} {cid}: SELFDESTRUCT frame payload {f} != {(a, r, bal)}")


def filter_sides(t):
    """(from_side, to_side) of a parity trace as Alloy's TraceFilterMatcher reads them."""
    a, kind = t.get("action", {}), t.get("type")
    if kind == "call":
        return low(a.get("from")), low(a.get("to"))
    if kind == "create":
        res = t.get("result")
        return low(a.get("from")), low(res.get("address")) if isinstance(res, dict) and "address" in res else None
    if kind == "suicide":
        return low(a.get("address")), low(a.get("refundAddress"))
    if kind == "reward":
        return None, low(a.get("author"))
    raise Fail(f"unknown trace type {kind}")


def check_filters(label, cid, case):
    expected_keys = {f"{side}=[{a}]" for a in WATCHED[cid] for side in ("from", "to")}
    got = {k.lower(): v for k, v in case["trace_filter"].items()}
    need(set(got) == expected_keys, f"{label} {cid}: filter queries {sorted(got)}")
    for q, hits in got.items():
        side, a = q.split("=[")[0], q.split("=[")[1].rstrip("]")
        want = [[t["traceAddress"], t["type"]] for t in case["parity_traces"]
                if filter_sides(t)[0 if side == "from" else 1] == a]
        need(hits == want, f"{label} {cid}: {q} recorded {hits}, recomputed from traces {want}")


def check_post_state(label, cid, case):
    by_addr = {low(e.get("address")): e for e in case["post_state"]}
    for a, bal, destroyed in POST_STATE[cid]:
        a = created_address(case) if a == CREATED else a
        need(a in by_addr, f"{label} {cid}: {a} missing from post-state")
        e = by_addr[a]
        need(low(e.get("balance")) == bal, f"{label} {cid}: {a} balance {e.get('balance')} != {bal}")
        need(("SelfDestructed" in str(e.get("status"))) == destroyed,
             f"{label} {cid}: {a} selfdestructed status {e.get('status')} (expected {destroyed})")


def t1_recomputed(doc):
    """The probe's nine T1 checks, recomputed from the raw trace data."""
    c = doc["cases"][0]
    sd = [t for t in c["parity_traces"] if t.get("type") == "suicide"]
    a = sd[0]["action"] if len(sd) == 1 else {}
    filters = {k.lower(): v for k, v in c["trace_filter"].items()}
    geth = [f for _, f in geth_frames(c["geth_call_frame"]) if f.get("type") == "SELFDESTRUCT"]
    g = geth[0] if len(geth) == 1 else {}
    return [
        len(sd) == 1 and sd[0].get("traceAddress") == [0],
        low(a.get("address")) == DE57,
        low(a.get("refundAddress")) == DE57,
        low(a.get("balance")) == "0x3e8",
        filters.get(f"from=[{DE57}]") == [[[0], "suicide"]],
        filters.get(f"to=[{DE57}]") == [[[], "call"], [[0], "suicide"]],
        low(g.get("from")) == DE57,
        low(g.get("to")) == DE57,
        low(g.get("value")) == "0x3e8",
    ]


def check_doc(label, doc):
    fixed = label.startswith("pr")
    need(set(doc) == DOC_KEYS, f"{label}: top-level keys {sorted(doc)}")
    v = doc["variant"]
    need(isinstance(v, str) and v.startswith(f"{label}: ") and all(s in v for s in LABELS[label])
         and (fixed or PR_HEAD not in v), f"{label}: variant label does not name the pinned commits: {v!r}")
    ids = [c.get("id") for c in doc["cases"]]
    need(ids == CASES, f"{label}: case list {ids} != expected nine cases")
    recorded = [c.get("pass") for c in doc["t1_fixture_checks"]]
    need(recorded == t1_recomputed(doc), f"{label}: recorded T1 flags {recorded} disagree with the trace data")
    for case in doc["cases"]:
        cid = case["id"]
        need(set(case) == CASE_KEYS, f"{label} {cid}: case keys {sorted(case)}")
        need(case["spec"] == SPEC[cid], f"{label} {cid}: spec {case['spec']}")
        need(case["execution"].get("success") is SUCCESS[cid], f"{label} {cid}: execution success flag")
        need(case["geth_run_state_matches_parity_run"] is True,
             f"{label} {cid}: geth-config run and parity-config run disagree on state")
        check_post_state(label, cid, case)
        check_suicide_payloads(label, cid, case, fixed)
        check_filters(label, cid, case)


def check_changed_case(cid, b, p):
    for path, x, y in leaf_diffs(b, p):
        sec = path[0] if path else None
        if sec == "parity_traces" and len(path) == 4 and path[2] == "action":
            ok = (b["parity_traces"][path[1]].get("type") == "suicide"
                  and path[3] in {"address", "refundAddress", "balance"})
        elif sec == "geth_call_frame":
            fpath = path[1:-1]
            frame = dict(geth_frames(b["geth_call_frame"])).get(fpath, {})
            ok = frame.get("type") == "SELFDESTRUCT" and path[-1] in {"from", "to", "value"}
        elif sec == "trace_filter" and len(path) == 2:
            ok = all(h in y for h in x) and all(h in x or h[1] == "suicide" for h in y)
        else:
            ok = False
        need(ok, f"{cid}: difference outside the suicide payload at {path}")


def check_pair(d, b_label, p_label):
    b, p = load(d, b_label), load(d, p_label)
    check_doc(b_label, b)
    check_doc(p_label, p)
    bc, pc = t1_recomputed(b), t1_recomputed(p)
    need(bc == [True] + [False] * 8, f"{b_label}: bug not reproduced as expected ({bc})")
    need(all(pc), f"{p_label}: T1 checks failing ({pc})")
    for cb, cp in zip(b["cases"], p["cases"]):
        cid = cb["id"]
        for key in ("execution", "post_state"):
            need(cb[key] == cp[key], f"{cid}: {key} differs between {b_label} and {p_label}")
        if cid in CONTROLS:
            need(cb == cp, f"{cid}: control case output differs")
        else:
            check_changed_case(cid, cb, cp)
    return (f"{b_label} -> {p_label}: T1 {sum(bc)}/9 -> {sum(pc)}/9; payloads, filters and state facts "
            f"match expectations in all nine cases; 5 controls identical; T1/T2/C5/C6 differ only in "
            f"the suicide payload and the filter matches it produces")


def run(d):
    msgs = []
    try:
        for b_label, p_label in PAIRS:
            msgs.append(check_pair(d, b_label, p_label))
    except Fail as e:
        print(f"FAIL: {e}")
        return 1
    for m in msgs:
        print(f"OK   {m}")
    return 0


# ---------------------------------------------------------------- self-test


def _edit(fname, fn):
    def apply(tmp):
        p = tmp / fname
        doc = json.loads(p.read_text(encoding="utf-8"))
        fn(doc)
        p.write_text(json.dumps(doc), encoding="utf-8")
    return apply


def _case(doc, cid):
    return next(c for c in doc["cases"] if c["id"] == cid)


def _suicide(doc, cid):
    return next(t for t in _case(doc, cid)["parity_traces"] if t["type"] == "suicide")


def _sd_frame(doc, cid):
    return next(f for _, f in geth_frames(_case(doc, cid)["geth_call_frame"]) if f.get("type") == "SELFDESTRUCT")


def _filter(doc, cid, prefix):
    return next(v for k, v in _case(doc, cid)["trace_filter"].items() if k.lower().startswith(prefix))


MUTATIONS = {
    # files and structure
    "missing_output_file": lambda tmp: (tmp / "out_base42.json").unlink(),
    "base_replaced_by_pr": lambda tmp: shutil.copy(tmp / "out_pr42.json", tmp / "out_base42.json"),
    "missing_case": _edit("out_pr43.json", lambda d: d["cases"].pop(4)),
    "variant_label_swapped": _edit("out_pr43.json", lambda d: d.update(variant=d["variant"].replace("pr43", "pr42", 1))),
    # state and execution
    "post_state_changed": _edit("out_pr43.json", lambda d: _case(d, T1)["post_state"][1].update(balance="0x0")),
    "geth_state_check_false_both": lambda tmp: [  # same edit as ChatGPT's false_geth_state_match_on_both
        _edit(f, lambda d: _case(d, C1).update(geth_run_state_matches_parity_run=False))(tmp)
        for f in ("out_base43.json", "out_pr43.json")],
    "execution_flag_flipped_both": lambda tmp: [
        _edit(f, lambda d: _case(d, C5)["execution"].update(success=True))(tmp)
        for f in ("out_base43.json", "out_pr43.json")],
    # payload values
    "wrong_T1_balance_flag_rewritten": _edit("out_pr43.json", lambda d: (
        _suicide(d, T1)["action"].update(balance="0x1"), d["t1_fixture_checks"][3].update({"pass": True}))),
    "wrong_T2_balance": _edit("out_pr43.json",  # same edit as ChatGPT's wrong_T2_balance
                              lambda d: _suicide(d, T2)["action"].update(balance="0xdeadbeef")),
    "wrong_C6_beneficiary": _edit("out_pr42.json", lambda d: _suicide(d, C6)["action"].update(refundAddress=DE57)),
    "extra_geth_nonpayload_key": _edit("out_pr43.json",  # same edit as ChatGPT's extra_geth_nonpayload_key
                                       lambda d: _sd_frame(d, C6).update(audit_unexpected_key=True)),
    "control_case_changed": _edit("out_pr43.json", lambda d: _case(d, C4)["parity_traces"][1].update(error="x")),
    "extra_change_outside_payload": _edit("out_pr43.json", lambda d: _case(d, C5)["parity_traces"][0].update(error="x")),
    # trace_filter results
    "invented_filter_suicide": _edit("out_pr43.json",  # same edit as ChatGPT's invented_filter_suicide
                                     lambda d: _filter(d, C6, "from=").append([[999], "suicide"])),
    "filter_gains_non_suicide": _edit("out_pr43.json", lambda d: _filter(d, C6, "from=").append([[9], "call"])),
    "filter_match_dropped": _edit("out_pr42.json", lambda d: _filter(d, C5, f"from=[{DE57}").clear()),
}


def selftest(d):
    """Copy outputs, apply one mutation each, and require check.py to reject every one."""
    src = pathlib.Path(d)
    failures = []
    with tempfile.TemporaryDirectory() as t:
        tmp0 = pathlib.Path(t) / "unmodified"
        shutil.copytree(src, tmp0)
        rc = subprocess.run([sys.executable, __file__, str(tmp0)], capture_output=True).returncode
        print(f"{'OK  ' if rc == 0 else 'FAIL'} unmodified outputs accepted (exit {rc})")
        if rc != 0:
            failures.append("unmodified")
        for name, apply in MUTATIONS.items():
            tmp = pathlib.Path(t) / name
            shutil.copytree(src, tmp)
            apply(tmp)
            r = subprocess.run([sys.executable, __file__, str(tmp)], capture_output=True, text=True)
            rejected = r.returncode != 0
            last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
            print(f"{'OK  ' if rejected else 'FAIL'} {name}: {'rejected' if rejected else 'ACCEPTED'}  ({last})")
            if not rejected:
                failures.append(name)
    rejected_count = len(MUTATIONS) - len([f for f in failures if f != "unmodified"])
    print(f"{rejected_count}/{len(MUTATIONS)} mutations rejected")
    return 1 if failures else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        sys.exit(selftest(args[1] if len(args) > 1 else "results"))
    sys.exit(run(args[0] if args else "results"))
