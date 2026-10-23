from __future__ import annotations

import pytest

from mota.constraints.compiler import (
    ChannelLinkSpec, ChannelNodeSpec, ConstraintRule, HighspeedChannelSpec,
    LayerModelSpec, PackageModelSpec, QuantitySpec, RulePack, RuleSource,
    TuningRegionSpec, TypedSelector, compile_highspeed,
    format_highspeed_report,
)
from mota.domain.models import (
    BoardIR, BoardPad, CircuitIR, Component, Net, Placement, Track,
)
from mota.jobs.contracts import WorkerUnavailable, kernel_binary, run_native_job

MM = 1_000_000


def _circuit_and_board() -> tuple[CircuitIR, BoardIR]:
    components = [
        Component(id="U1", reference="U1", part_id="MCU", symbol_id="sym_u",
                  footprint_id="fp_u", pin_pad_map={"1": ["1"], "2": ["2"]},
                  placement=Placement(x_nm=5 * MM, y_nm=10 * MM)),
        Component(id="U2", reference="U2", part_id="SENSOR", symbol_id="sym_s",
                  footprint_id="fp_s", pin_pad_map={"1": ["1"]},
                  placement=Placement(x_nm=26 * MM, y_nm=14 * MM)),
        Component(id="U3", reference="U3", part_id="SENSOR", symbol_id="sym_s",
                  footprint_id="fp_s", pin_pad_map={"1": ["1"]},
                  placement=Placement(x_nm=26 * MM, y_nm=6 * MM)),
    ]
    nets = [
        Net(id="n1", name="N1", pins=["U1:1", "U2:1", "U3:1"]),
        Net(id="n2", name="N2", pins=["U1:2"]),
        Net(id="p", name="DP_P", pins=["U1:2"]),
        Net(id="n", name="DP_N", pins=["U1:2"]),
    ]
    pads = [
        BoardPad(id="U1-1", component_id="U1", number="1", net_id="n1",
                 x_nm=5 * MM, y_nm=10 * MM, w_nm=400_000, h_nm=400_000,
                 shape="circle"),
        BoardPad(id="U2-1", component_id="U2", number="1", net_id="n1",
                 x_nm=26 * MM, y_nm=14 * MM, w_nm=400_000, h_nm=400_000,
                 shape="circle"),
        BoardPad(id="U3-1", component_id="U3", number="1", net_id="n1",
                 x_nm=26 * MM, y_nm=6 * MM, w_nm=400_000, h_nm=400_000,
                 shape="circle"),
    ]
    tracks = [
        Track(id="T1", net_id="n1", layer="F.Cu", width_nm=200_000,
              points=[[5 * MM, 10 * MM], [26 * MM, 10 * MM]]),
        Track(id="T2", net_id="n1", layer="F.Cu", width_nm=200_000,
              points=[[26 * MM, 10 * MM], [26 * MM, 14 * MM]]),
        Track(id="T3", net_id="n1", layer="F.Cu", width_nm=200_000,
              points=[[26 * MM, 10 * MM], [26 * MM, 6 * MM]]),
    ]
    board = BoardIR(
        outline=[[0, 0], [40 * MM, 0], [40 * MM, 20 * MM], [0, 20 * MM]],
        width_nm=40 * MM, height_nm=20 * MM, layer_count=2,
        pads=pads, tracks=tracks)
    circuit = CircuitIR(components=components, nets=nets)
    return circuit, board


def _pack(rules: list[ConstraintRule], pack_id: str = "hs") -> RulePack:
    return RulePack(
        pack_id=pack_id, version=1, kind="platform", title=pack_id,
        purpose="highspeed unit test",
        sources=[RuleSource(source_id="src", title="test source")],
        rules=rules)


def _rule(rule_id: str, kind: str,
          parameters: dict[str, tuple[float, str]], *,
          selector: TypedSelector | None = None) -> ConstraintRule:
    return ConstraintRule(
        rule_id=rule_id, kind=kind,
        selector=selector or TypedSelector(all=True),
        parameters={name: QuantitySpec(value=value, unit=unit)
                    for name, (value, unit) in parameters.items()},
        sources=["src"])


def _channel() -> HighspeedChannelSpec:
    return HighspeedChannelSpec(
        id="CH_N1", net="N1", driver="D1", receivers=["R1", "R2"],
        nodes=[
            ChannelNodeSpec(id="D1", kind="driver", component_id="U1",
                            pin="1", net="N1"),
            ChannelNodeSpec(id="R1", kind="receiver", component_id="U2",
                            pin="1", net="N1"),
            ChannelNodeSpec(id="R2", kind="receiver", component_id="U3",
                            pin="1", net="N1"),
        ],
        links=[ChannelLinkSpec(id="L1", from_node="D1", to_node="R1"),
               ChannelLinkSpec(id="L2", from_node="D1", to_node="R2")])


def _coverage(result, rule_key: str):
    matches = [item for item in result.coverage if item.rule_key == rule_key]
    assert len(matches) == 1, (rule_key, [i.rule_key for i in result.coverage])
    return matches[0]


def test_length_match_compiles_explicit_channel():
    circuit, board = _circuit_and_board()
    rule = _rule("uart_length", "length_match", {"max": (0.5, "mm")},
                 selector=TypedSelector(nets=["N1"]))
    result = compile_highspeed(circuit, board, [_pack([rule])],
                               channels=[_channel()])

    assert result.status == "SAT"
    assert result.review_ready is False
    assert result.si_status == "NOT_CHECKED"
    assert result.pi_status == "NOT_CHECKED"
    channel = result.request["channels"][0]
    assert channel["id"] == "CH_N1"
    assert channel["length_tolerance_nm"] == 500_000
    assert channel["driver"] == "D1"
    assert channel["receivers"] == ["R1", "R2"]
    assert channel["nodes"][0]["x_nm"] == 5 * MM
    assert len(result.request["tracks"]) == 3
    applied = _coverage(result, "hs:uart_length")
    assert applied.status == "APPLIED"
    assert applied.object_refs == ["net:N1"]


def test_no_pack_is_invalid():
    circuit, board = _circuit_and_board()
    result = compile_highspeed(circuit, board, [], channels=[_channel()])
    assert result.status == "INVALID"
    assert result.request == {}
    assert any(item.code == "NO_RULES" for item in result.diagnostics)
    assert result.content_hash


def test_empty_pack_is_invalid():
    circuit, board = _circuit_and_board()
    result = compile_highspeed(circuit, board, [_pack([])],
                               channels=[_channel()])
    assert result.status == "INVALID"
    assert any(item.code == "PACK_NO_RULES" for item in result.diagnostics)


def test_rule_without_channel_stays_not_checked():
    circuit, board = _circuit_and_board()
    rule = _rule("uart_length", "length_match", {"max": (0.5, "mm")},
                 selector=TypedSelector(nets=["N1"]))
    result = compile_highspeed(circuit, board, [_pack([rule])])
    assert result.status == "SAT"
    assert result.request["channels"] == []
    entry = _coverage(result, "hs:uart_length")
    assert entry.status == "NOT_CHECKED"
    assert entry.reason == "NO_CHANNEL"


def test_pair_rule_builds_two_net_pair():
    circuit, board = _circuit_and_board()
    rule = _rule("usb_pair", "differential_pair",
                 {"gap": (0.2, "mm"), "gap_tolerance": (0.05, "mm"),
                  "skew": (0.1, "mm"), "uncoupled": (1.0, "mm"),
                  "delay_skew": (5.0, "ps")},
                 selector=TypedSelector(nets=["DP_P", "DP_N"]))
    result = compile_highspeed(circuit, board, [_pack([rule])])
    assert result.status == "SAT"
    pair = result.request["pairs"][0]
    assert pair["net_p"] == "DP_P" and pair["net_n"] == "DP_N"
    assert pair["gap_nm"] == 200_000
    assert pair["gap_tolerance_nm"] == 50_000
    assert pair["skew_tolerance_nm"] == 100_000
    assert pair["max_uncoupled_nm"] == 1_000_000
    assert pair["delay_skew_tolerance_ps"] == 5.0
    assert _coverage(result, "hs:usb_pair").status == "APPLIED"


def test_pair_rule_with_three_nets_is_not_checked():
    circuit, board = _circuit_and_board()
    rule = _rule("bad_pair", "differential_pair", {"gap": (0.2, "mm")})
    result = compile_highspeed(circuit, board, [_pack([rule])])
    assert result.request["pairs"] == []
    entry = _coverage(result, "hs:bad_pair")
    assert entry.status == "NOT_CHECKED"
    assert entry.reason == "PAIR_NET_COUNT"


def test_channel_with_missing_pad_is_dropped():
    circuit, board = _circuit_and_board()
    spec = _channel()
    spec.nodes[1] = ChannelNodeSpec(id="R1", kind="receiver",
                                    component_id="U2", pin="9", net="N1")
    rule = _rule("uart_length", "length_match", {"max": (0.5, "mm")},
                 selector=TypedSelector(nets=["N1"]))
    result = compile_highspeed(circuit, board, [_pack([rule])],
                               channels=[spec])
    assert result.request["channels"] == []
    dropped = _coverage(result, "channel:CH_N1")
    assert dropped.status == "NOT_CHECKED"
    assert dropped.reason == "MISSING_PAD"
    assert "U2:9" in dropped.message
    assert result.status == "UNKNOWN"


def test_delay_rule_needs_layer_models():
    circuit, board = _circuit_and_board()
    rule = _rule("uart_delay", "propagation_delay",
                 {"target": (200.0, "ps")},
                 selector=TypedSelector(nets=["N1"]))

    without = compile_highspeed(circuit, board, [_pack([rule])],
                                channels=[_channel()])
    entry = _coverage(without, "hs:uart_delay")
    assert entry.status == "NOT_CHECKED"
    assert entry.reason == "MISSING_STACKUP_MODEL"
    assert without.request["layer_models"] == []

    with_models = compile_highspeed(
        circuit, board, [_pack([rule])], channels=[_channel()],
        layer_models=[LayerModelSpec(layer="F.Cu",
                                     propagation_ps_per_mm=6.0,
                                     reference_layer="B.Cu",
                                     source="test")])
    assert _coverage(with_models, "hs:uart_delay").status == "APPLIED"
    channel = with_models.request["channels"][0]
    assert channel["target_delay_ps"] == 200.0
    assert with_models.request["layer_models"][0]["layer"] == "F.Cu"


def test_hash_is_stable_and_sensitive():
    circuit, board = _circuit_and_board()
    rule = _rule("uart_length", "length_match", {"max": (0.5, "mm")},
                 selector=TypedSelector(nets=["N1"]))
    first = compile_highspeed(circuit, board, [_pack([rule])],
                              channels=[_channel()])
    again = compile_highspeed(circuit, board, [_pack([rule])],
                              channels=[_channel()])
    assert first.content_hash == again.content_hash

    changed = _channel()
    changed.length_tolerance_nm = 1
    different = compile_highspeed(circuit, board, [_pack([rule])],
                                  channels=[changed])
    assert different.content_hash != first.content_hash


def test_channel_without_nodes_is_not_checked():
    circuit, board = _circuit_and_board()
    spec = HighspeedChannelSpec(id="CH_EMPTY", net="N1", driver="D1",
                                receivers=["R1"])
    rule = _rule("uart_length", "length_match", {"max": (0.5, "mm")},
                 selector=TypedSelector(nets=["N1"]))
    result = compile_highspeed(circuit, board, [_pack([rule])],
                               channels=[spec])
    assert result.request["channels"] == []
    entry = _coverage(result, "channel:CH_EMPTY")
    assert entry.status == "NOT_CHECKED"
    assert entry.reason == "MISSING_NODE"
    assert result.status == "UNKNOWN"


def test_delay_rule_without_parameters_is_rejected():
    circuit, board = _circuit_and_board()
    rule = _rule("bare_delay", "propagation_delay", {})
    result = compile_highspeed(circuit, board, [_pack([rule])],
                               channels=[_channel()])
    assert result.status == "INVALID"
    entry = _coverage(result, "hs:bare_delay")
    assert entry.status == "REJECTED"
    assert entry.reason == "MISSING_PARAMETER"


def test_rule_without_unit_is_rejected():
    circuit, board = _circuit_and_board()
    rule = ConstraintRule(
        rule_id="bare", kind="length_match",
        selector=TypedSelector(nets=["N1"]),
        parameters={"max": QuantitySpec(value=0.5, unit="")},
        sources=["src"])
    result = compile_highspeed(circuit, board, [_pack([rule])],
                               channels=[_channel()])
    assert result.status == "INVALID"
    assert any(item.code == "MISSING_UNIT" for item in result.diagnostics)
    entry = _coverage(result, "hs:bare")
    assert entry.status == "REJECTED"


def test_unused_specs_are_carried_without_claims():
    circuit, board = _circuit_and_board()
    rule = _rule("usb_pair", "differential_pair",
                 {"gap": (0.2, "mm"), "skew": (0.1, "mm")},
                 selector=TypedSelector(nets=["DP_P", "DP_N"]))
    result = compile_highspeed(
        circuit, board, [_pack([rule])],
        package_models=[PackageModelSpec(component_id="U1", pin="1",
                                         delay_ps=20.0, source="test")],
        tuning_regions=[TuningRegionSpec(id="TR", net="DP_N",
                                         start=(20 * MM, 5 * MM),
                                         end=(24 * MM, 5 * MM),
                                         layer="F.Cu", width_nm=2 * MM)])
    assert result.request["package_models"][0]["component_id"] == "U1"
    assert result.request["tuning_regions"][0]["net"] == "DP_N"
    assert result.request["reference_planes"] == []
    report = format_highspeed_report(result)
    assert "SI: NOT_CHECKED" in report
    assert "hs:usb_pair" in report


def test_compiled_request_runs_in_the_kernel():
    try:
        binary = kernel_binary()
    except WorkerUnavailable as error:
        pytest.skip(f"native kernel not built: {error}")

    circuit, board = _circuit_and_board()
    rule = _rule("uart_length", "length_match", {"max": (0.5, "mm")},
                 selector=TypedSelector(nets=["N1"]))
    compiled = compile_highspeed(
        circuit, board, [_pack([rule])], channels=[_channel()],
        layer_models=[LayerModelSpec(layer="F.Cu",
                                     propagation_ps_per_mm=6.0,
                                     reference_layer="B.Cu",
                                     source="test")])
    document = {
        "schema_version": 2,
        "input_id": "c11-highspeed",
        "board": {
            "width_nm": 40 * MM,
            "height_nm": 20 * MM,
            "thickness_nm": 1_600_000,
            "stackup": {"layers": [
                {"id": "F.Cu", "order": 1, "kind": "copper",
                 "thickness_nm": 35_000, "material_ref": "copper"},
                {"id": "B.Cu", "order": 2, "kind": "copper",
                 "thickness_nm": 35_000, "material_ref": "copper"},
            ]},
        },
        "highspeed": compiled.request,
        "queries": [{"id": "hs1", "kind": "highspeed"}],
    }
    result = run_native_job(document, binary=binary)
    assert result.status == "SUCCEEDED", result.diagnostics
    payload = result.results[0]["highspeed"]
    assert payload["status"] in {"SUCCEEDED", "PARTIAL", "FAILED", "UNKNOWN"}
    assert payload["si_status"] == "NOT_CHECKED"
    assert payload["pi_status"] == "NOT_CHECKED"
    kinds = {item["kind"] for item in payload["checks"]}
    assert "length_match" in kinds
    assert "channel_topology" in kinds
    assert payload["channels"] == 1
