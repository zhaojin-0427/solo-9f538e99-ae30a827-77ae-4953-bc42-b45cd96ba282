"""性能约束测试：
1. 查单峰/验单的作业表读取走索引，不随全季作业区间数线性增长；
2. 校时不逐秒试偏移——即使偏移窗口极宽，判定耗时也与窗口宽度无关。
"""
import sqlite3
import time

from tests.conftest import standard_clocks, upload


def _bulk_dataset(tmp_path):
    peaks = [("P1", 1_000_000)]
    # 每台吊机 2 万条远离峰时刻的噪声区间，共 6 万条
    work = []
    for crane in ("C1", "C2", "C3"):
        for i in range(20_000):
            start = 10_000_000 + i * 37 + hash(crane) % 37
            work.append((crane, start, start + 30))
    # C1 一条真正覆盖峰窗口的区间
    work.append(("C1", 1_000_000, 1_000_011))
    clocks = standard_clocks()
    return peaks, work, clocks


def test_peak_query_uses_index_not_full_scan(app, client, tmp_path):
    peaks, work, clocks = _bulk_dataset(tmp_path)
    resp = upload(client, peaks, work, clocks)
    assert resp.status_code == 200, resp.data.decode()

    # 直接对作业表的核定 SQL 做 EXPLAIN QUERY PLAN
    with sqlite3.connect(app.config["DATABASE"]) as raw:
        plan = raw.execute(
            "EXPLAIN QUERY PLAN SELECT start_sec, end_sec FROM work_intervals "
            "WHERE crane_id = ? AND start_sec <= ? AND end_sec > ? ORDER BY start_sec",
            ("C1", 1_000_010, 1_000_000),
        ).fetchall()
    plan_text = " ".join(row[3] for row in plan)
    assert "SCAN work_intervals" not in plan_text, plan_text
    assert "SEARCH" in plan_text and "idx_work_crane_time" in plan_text, plan_text

    # 端到端：6 万区间下查询仍快速且结论正确
    t0 = time.perf_counter()
    body = client.get("/api/peaks/P1").get_json()
    elapsed = time.perf_counter() - t0
    assert body["determination"]["result"] == "issue"
    assert body["determination"]["crane"] == "C1"
    assert elapsed < 1.0


def test_wide_offset_window_does_not_iterate_seconds(app, client, tmp_path):
    # r1 改成极宽区间 [-5_000_000, 5_000_000]：禁止逐秒试探的算法必须瞬时完成
    peaks, work, clocks = _bulk_dataset(tmp_path)
    resp = upload(client, peaks, work, clocks)
    assert resp.status_code == 200
    resp = client.post("/api/clock-records/1/correct",
                       json={"low_sec": -5_000_000, "high_sec": 5_000_000})
    assert resp.status_code == 200

    t0 = time.perf_counter()
    body = client.get("/api/peaks/P1").get_json()
    elapsed = time.perf_counter() - t0
    # 窗口宽一千万秒，逐秒算法不可能在该时限内跑完
    assert elapsed < 1.0
    # C1 窗口几乎覆盖全季，命中大量噪声区间；C2/C3 同样如此 => 多机待核
    assert body["determination"]["result"] == "pending"


def test_form_status_lookup_is_point_query(app, loaded_client):
    client = loaded_client
    form_no = client.post("/api/forms",
                          json={"peak_id": "P1"}).get_json()["form_no"]
    with sqlite3.connect(app.config["DATABASE"]) as raw:
        plan = raw.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM forms WHERE form_no = ?",
            (form_no,),
        ).fetchall()
    plan_text = " ".join(row[3] for row in plan)
    assert "SCAN forms" not in plan_text
    assert "SEARCH" in plan_text
