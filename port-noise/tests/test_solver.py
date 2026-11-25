"""求解器单元测试：约束图、可行区间、窗口覆盖。"""
from app.solver import (
    build_graph,
    classify_window,
    find_negative_cycle,
    floyd_warshall,
    meter_relative_bounds,
)


def rec(rid, a, b, lo, hi):
    return {"rec_id": rid, "device_a": a, "device_b": b,
            "low_sec": lo, "high_sec": hi}


def test_bounds_direct_record():
    # S->C 约束 0 <= o(C)-o(S) <= 10
    nodes, edges = build_graph([rec(1, "S", "C", 0, 10)])
    bounds = meter_relative_bounds(nodes, edges, ["C"])
    assert bounds["C"] == (0, 10)


def test_bounds_chained_records_tighten():
    # S->R: [0,100], R->C: [-10,10] => o(C) ∈ [-10, 110]
    records = [rec(1, "S", "R", 0, 100), rec(2, "R", "C", -10, 10)]
    nodes, edges = build_graph(records)
    assert find_negative_cycle(edges, nodes) is None
    bounds = meter_relative_bounds(nodes, edges, ["C"])
    assert bounds["C"] == (-10, 110)


def test_conflicting_records_form_negative_cycle():
    # S->C: [0,10]，C->S 另一条又要求 20 <= o(S)-o(C) => 无解负环
    records = [rec(1, "S", "C", 0, 10), rec(2, "C", "S", 20, 30)]
    nodes, edges = build_graph(records)
    cycle = find_negative_cycle(edges, nodes)
    assert set(cycle) == {1, 2}


def test_self_inconsistent_record_is_negative_self_loop():
    # 由入库层拦截 high < low；这里直接构图验证负环识别
    nodes, edges = build_graph([rec(9, "S", "C", 10, 0)])
    cycle = find_negative_cycle(edges, nodes)
    assert cycle == [9]


def test_unreachable_crane():
    records = [rec(1, "S", "C1", 0, 10), rec(2, "X", "C2", 0, 5)]
    nodes, edges = build_graph(records)
    bounds = meter_relative_bounds(nodes, edges, ["C1", "C2"])
    assert bounds["C1"] == (0, 10)
    assert bounds["C2"] is None


def test_floyd_tightest_bounds_via_two_paths():
    # 直连 S->C [0,100]（上界 100），链 S->R->C 给出更紧上界 20
    records = [
        rec(1, "S", "C", 0, 100),
        rec(2, "S", "R", 0, 10),
        rec(3, "R", "C", 0, 10),
    ]
    nodes, edges = build_graph(records)
    bounds = meter_relative_bounds(nodes, edges, ["C"])
    # 最短路径取最紧上界：min(100, 10+10) = 20；下界保持 0
    assert bounds["C"] == (0, 20)


def test_classify_window_half_open_semantics():
    # 含首不含尾：[10,20) 不包含 20
    assert classify_window([(10, 20)], 10, 19) == "always"
    assert classify_window([(10, 20)], 10, 20) == "maybe"
    assert classify_window([(10, 20)], 20, 25) == "never"
    # 相邻区间在整数秒上无缝：[10,20) 与 [20,30) 连续覆盖 10..29
    assert classify_window([(10, 20), (20, 30)], 10, 29) == "always"
    # 有缺口
    assert classify_window([(10, 20), (22, 30)], 10, 29) == "maybe"
    # 多区间合并覆盖
    assert classify_window([(10, 15), (14, 25)], 10, 24) == "always"
    assert classify_window([], 0, 10) == "never"
