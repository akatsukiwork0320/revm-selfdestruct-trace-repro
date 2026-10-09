//! Library-level probe for bluealloy/revm#3833 through revm-inspectors.
//!
//! Runs the same transactions twice per case (parity config as Reth's trace_filter uses,
//! and a geth callTracer config), then prints one JSON document with:
//!   - execution summary and post-state digest (must not depend on the revm variant),
//!   - parity flat traces, trace_filter matches (Alloy matcher, as Reth uses),
//!   - the geth callTracer frame.
//! T1 reproduces banteg/trace-interop `mined-probes` block 4 `selfdestruct-self`
//! (0x..de57 with 1000 wei, slot 1 = 42, code ADDRESS SELFDESTRUCT, Prague).
//! Expected values for T1 come from that fixture's independent expectations, not from
//! any client: suicide {address: de57, refundAddress: de57, balance: 0x3e8};
//! trace_filter fromAddress=[de57] -> [([0],suicide)]; toAddress=[de57] -> [([],call),([0],suicide)].

use alloy_primitives::{address, b256, Address, Bytes, B256, U256};
use alloy_rpc_types_eth::TransactionInfo;
use alloy_rpc_types_trace::{
    filter::{TraceFilter, TraceFilterMode},
    geth::CallConfig,
    parity::{Action, LocalizedTransactionTrace},
};
use revm::{
    bytecode::Bytecode,
    context::TxEnv,
    database::{CacheDB, EmptyDB},
    primitives::hardfork::SpecId,
    state::{AccountInfo, EvmState},
    Context, InspectEvm, MainBuilder, MainContext,
};
use revm_inspectors::tracing::{TracingInspector, TracingInspectorConfig};
use serde_json::{json, Value};

const KEY1: Address = address!("7e5f4552091a69125d5dfcb7b8c2659029395bdf");
const DE57: Address = address!("000000000000000000000000000000000000de57");
const OTHER: Address = address!("000000000000000000000000000000000000beef");
const PARENT: Address = address!("00000000000000000000000000000000000000a1");
const FACTORY: Address = address!("000000000000000000000000000000000000fac0");
const IMPL: Address = address!("0000000000000000000000000000000000001111");
const TX_HASH: B256 = b256!("87cfeb711c34353c96b0d55c58aaf1b02f602aff3baa854ee9ba1feb1ca2c3a2");

struct Case {
    id: &'static str,
    note: &'static str,
    spec: SpecId,
    accounts: Vec<(Address, AccountInfo, Vec<(U256, U256)>)>,
    to: Address,
    value: u64,
    gas_limit: u64,
}

fn acct(code: Vec<u8>, balance: u64) -> AccountInfo {
    AccountInfo { balance: U256::from(balance), nonce: 1, ..Default::default() }
        .with_code(Bytecode::new_raw(Bytes::from(code)))
}

fn push20(a: Address) -> Vec<u8> {
    let mut v = vec![0x73];
    v.extend_from_slice(a.as_slice());
    v
}

fn cases() -> Vec<Case> {
    let self_destruct = vec![0x30, 0xff]; // ADDRESS SELFDESTRUCT
    let mut to_other = push20(OTHER); // PUSH20 OTHER SELFDESTRUCT
    to_other.push(0xff);
    // PUSH2 0x30ff PUSH0 MSTORE PUSH1 2 PUSH1 30 PUSH2 256 CREATE STOP
    let factory = vec![0x61, 0x30, 0xff, 0x5f, 0x52, 0x60, 0x02, 0x60, 0x1e, 0x61, 0x01, 0x00, 0xf0, 0x00];
    // 4x PUSH0, PUSH20 DE57, PUSH2 0xffff, STATICCALL, POP, STOP
    let mut staticcall = vec![0x5f, 0x5f, 0x5f, 0x5f];
    staticcall.extend(push20(DE57));
    staticcall.extend([0x61, 0xff, 0xff, 0xfa, 0x50, 0x00]);
    // 5x PUSH0, PUSH20 DE57, PUSH2 0xffff, CALL, POP, PUSH0, PUSH0, REVERT
    let mut call_then_revert = vec![0x5f, 0x5f, 0x5f, 0x5f, 0x5f];
    call_then_revert.extend(push20(DE57));
    call_then_revert.extend([0x61, 0xff, 0xff, 0xf1, 0x50, 0x5f, 0x5f, 0xfd]);
    // 4x PUSH0, PUSH20 IMPL, PUSH2 0xffff, DELEGATECALL, STOP
    let mut delegatecall = vec![0x5f, 0x5f, 0x5f, 0x5f];
    delegatecall.extend(push20(IMPL));
    delegatecall.extend([0x61, 0xff, 0xff, 0xf4, 0x00]);

    let de57_fixture =
        || (DE57, acct(self_destruct.clone(), 1000), vec![(U256::from(1), U256::from(42))]);
    vec![
        Case {
            id: "T1_fixture_selfdestruct_self",
            note: "mined-probes block 4 selfdestruct-self: Prague, pre-existing contract, beneficiary = self",
            spec: SpecId::PRAGUE,
            accounts: vec![de57_fixture()],
            to: DE57,
            value: 0,
            gas_limit: 200_000,
        },
        Case {
            id: "T2_selfdestruct_self_with_tx_value",
            note: "same as T1 with 459 wei sent by the tx (pre-opcode balance 1459)",
            spec: SpecId::PRAGUE,
            accounts: vec![de57_fixture()],
            to: DE57,
            value: 459,
            gas_limit: 200_000,
        },
        Case {
            id: "C1_prague_other_beneficiary",
            note: "control: Prague, pre-existing contract, beneficiary != self (BalanceTransfer journal entry)",
            spec: SpecId::PRAGUE,
            accounts: vec![(DE57, acct(to_other, 1000), vec![])],
            to: DE57,
            value: 0,
            gas_limit: 200_000,
        },
        Case {
            id: "C2_shanghai_selfdestruct_self",
            note: "control: pre-Cancun, beneficiary = self (AccountDestroyed journal entry)",
            spec: SpecId::SHANGHAI,
            accounts: vec![de57_fixture()],
            to: DE57,
            value: 0,
            gas_limit: 200_000,
        },
        Case {
            id: "C3_prague_create_and_destroy_same_tx",
            note: "control: factory CREATEs initcode 30ff with 256 wei (created in same tx -> AccountDestroyed)",
            spec: SpecId::PRAGUE,
            accounts: vec![(FACTORY, acct(factory, 1000), vec![])],
            to: FACTORY,
            value: 0,
            gas_limit: 200_000,
        },
        Case {
            id: "C4_prague_staticcall_into_selfdestruct",
            note: "control: SELFDESTRUCT inside STATICCALL must fail and emit no suicide record",
            spec: SpecId::PRAGUE,
            accounts: vec![(PARENT, acct(staticcall, 0), vec![]), de57_fixture()],
            to: PARENT,
            value: 0,
            gas_limit: 200_000,
        },
        Case {
            id: "C5_prague_outer_revert",
            note: "child selfdestructs to self successfully, then the parent REVERTs",
            spec: SpecId::PRAGUE,
            accounts: vec![(PARENT, acct(call_then_revert, 0), vec![]), de57_fixture()],
            to: PARENT,
            value: 0,
            gas_limit: 200_000,
        },
        Case {
            id: "C6_prague_delegatecall_selfdestruct_self",
            note: "delegator (500 wei) DELEGATECALLs code 30ff: executing account and beneficiary are the delegator",
            spec: SpecId::PRAGUE,
            accounts: vec![
                (PARENT, acct(delegatecall, 500), vec![]),
                (IMPL, acct(self_destruct.clone(), 0), vec![]),
            ],
            to: PARENT,
            value: 0,
            gas_limit: 200_000,
        },
        Case {
            id: "C7_prague_selfdestruct_out_of_gas",
            note: "control: tx gas 26001 leaves 4999 gas at SELFDESTRUCT (needs 5000): no suicide record",
            spec: SpecId::PRAGUE,
            accounts: vec![de57_fixture()],
            to: DE57,
            value: 0,
            gas_limit: 26_001,
        },
    ]
}

fn db_for(case: &Case) -> CacheDB<EmptyDB> {
    let mut db = CacheDB::<EmptyDB>::default();
    db.insert_account_info(
        KEY1,
        AccountInfo { balance: U256::from(10u128.pow(18)), ..Default::default() },
    );
    for (a, info, storage) in &case.accounts {
        db.insert_account_info(*a, info.clone());
        for (k, v) in storage {
            db.insert_account_storage(*a, *k, *v).unwrap();
        }
    }
    db
}

fn tx_for(case: &Case) -> TxEnv {
    TxEnv::builder()
        .caller(KEY1)
        .to(case.to)
        .value(U256::from(case.value))
        .gas_limit(case.gas_limit)
        .build_fill()
}

fn state_digest(state: &EvmState) -> Value {
    let mut accts: Vec<_> = state.iter().collect();
    accts.sort_by_key(|(a, _)| **a);
    Value::Array(
        accts
            .into_iter()
            .map(|(a, acc)| {
                let mut slots: Vec<_> =
                    acc.storage.iter().map(|(k, s)| (*k, s.present_value)).collect();
                slots.sort();
                json!({
                    "address": a,
                    "balance": acc.info.balance,
                    "nonce": acc.info.nonce,
                    "code_hash": acc.info.code_hash,
                    "storage": slots.iter().map(|(k, v)| json!([k, v])).collect::<Vec<_>>(),
                    "status": format!("{:?}", acc.status),
                })
            })
            .collect(),
    )
}

fn action_type(a: &Action) -> &'static str {
    match a {
        Action::Call(_) => "call",
        Action::Create(_) => "create",
        Action::Selfdestruct(_) => "suicide",
        Action::Reward(_) => "reward",
    }
}

fn filter_hits(traces: &[LocalizedTransactionTrace], from: Vec<Address>, to: Vec<Address>) -> Value {
    let m = TraceFilter {
        from_address: from,
        to_address: to,
        mode: TraceFilterMode::Intersection,
        ..Default::default()
    }
    .matcher();
    Value::Array(
        traces
            .iter()
            .filter(|t| m.matches(&t.trace))
            .map(|t| json!([t.trace.trace_address, action_type(&t.trace.action)]))
            .collect(),
    )
}

fn run_case(case: &Case) -> Value {
    // Parity view: same config and builder path as Reth's trace_transaction / trace_filter.
    let mut evm = Context::mainnet()
        .modify_cfg_chained(|cfg| cfg.set_spec_and_mainnet_gas_params(case.spec))
        .with_db(db_for(case))
        .build_mainnet_with_inspector(TracingInspector::new(
            TracingInspectorConfig::default_parity(),
        ));
    let res = evm.inspect_tx(tx_for(case)).expect("tx must execute");
    let info = TransactionInfo {
        hash: Some(TX_HASH),
        index: Some(0),
        block_number: Some(4),
        ..Default::default()
    };
    let traces = evm.inspector.clone().into_parity_builder().into_localized_transaction_traces(info);
    let parity: Vec<Value> = traces.iter().map(|t| serde_json::to_value(&t.trace).unwrap()).collect();
    let watched: Vec<Address> = case.accounts.iter().map(|(a, _, _)| *a).collect();
    let mut filters = serde_json::Map::new();
    for a in &watched {
        filters.insert(format!("from=[{a}]"), filter_hits(&traces, vec![*a], vec![]));
        filters.insert(format!("to=[{a}]"), filter_hits(&traces, vec![], vec![*a]));
    }

    // Geth callTracer view.
    let call_cfg = CallConfig::default();
    let mut gevm = Context::mainnet()
        .modify_cfg_chained(|cfg| cfg.set_spec_and_mainnet_gas_params(case.spec))
        .with_db(db_for(case))
        .build_mainnet_with_inspector(TracingInspector::new(
            TracingInspectorConfig::from_geth_call_config(&call_cfg),
        ));
    let gres = gevm.inspect_tx(tx_for(case)).expect("tx must execute");
    let frame = gevm
        .inspector
        .clone()
        .into_geth_builder()
        .geth_call_traces(call_cfg, gres.result.tx_gas_used());

    json!({
        "id": case.id,
        "note": case.note,
        "spec": format!("{:?}", case.spec),
        "execution": {
            "success": res.result.is_success(),
            "gas_used": res.result.tx_gas_used(),
            "result": format!("{:?}", res.result),
        },
        "post_state": state_digest(&res.state),
        "geth_run_state_matches_parity_run": state_digest(&gres.state) == state_digest(&res.state),
        "parity_traces": parity,
        "trace_filter": Value::Object(filters),
        "geth_call_frame": serde_json::to_value(&frame).unwrap(),
    })
}

fn fixture_checks(t1: &Value) -> Value {
    // Independent expectations from trace-interop mined-probes (docs/mined-probes.md, H23/H26).
    let de57 = format!("{DE57:#x}");
    let suicide = t1["parity_traces"]
        .as_array()
        .unwrap()
        .iter()
        .find(|t| t["type"] == "suicide")
        .cloned()
        .unwrap_or(Value::Null);
    let a = &suicide["action"];
    let eq = |v: &Value, s: &str| v.as_str().map(|x| x.eq_ignore_ascii_case(s)).unwrap_or(false);
    let filter_from = &t1["trace_filter"][format!("from=[{DE57}]")];
    let filter_to = &t1["trace_filter"][format!("to=[{DE57}]")];
    let geth_sd = t1["geth_call_frame"]["calls"]
        .as_array()
        .and_then(|c| c.iter().find(|f| f["type"] == "SELFDESTRUCT").cloned())
        .unwrap_or(Value::Null);
    let checks = vec![
        ("suicide record exists at traceAddress [0]", suicide["traceAddress"] == json!([0])),
        ("suicide action.address == 0x..de57", eq(&a["address"], &de57)),
        ("suicide action.refundAddress == 0x..de57", eq(&a["refundAddress"], &de57)),
        ("suicide action.balance == 0x3e8", eq(&a["balance"], "0x3e8")),
        ("trace_filter fromAddress=[de57] -> [([0],suicide)]", *filter_from == json!([[[0], "suicide"]])),
        (
            "trace_filter toAddress=[de57] -> [([],call),([0],suicide)]",
            *filter_to == json!([[[], "call"], [[0], "suicide"]]),
        ),
        ("geth SELFDESTRUCT frame from == de57", eq(&geth_sd["from"], &de57)),
        ("geth SELFDESTRUCT frame to == de57", eq(&geth_sd["to"], &de57)),
        ("geth SELFDESTRUCT frame value == 0x3e8", eq(&geth_sd["value"], "0x3e8")),
    ];
    Value::Array(checks.into_iter().map(|(name, ok)| json!({"check": name, "pass": ok})).collect())
}

fn main() {
    let variant = std::env::args().nth(1).unwrap_or_else(|| "unknown".to_string());
    let results: Vec<Value> = cases().iter().map(run_case).collect();
    let checks = fixture_checks(&results[0]);
    let out = json!({
        "variant": variant,
        "t1_fixture_checks": checks,
        "cases": results,
    });
    println!("{}", serde_json::to_string_pretty(&out).unwrap());
}
