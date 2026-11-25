"""端到端 API 行为：核定、签发、更正撤单、冲突拒绝、事件顺序。"""
from tests.conftest import standard_clocks, standard_peaks, standard_work, upload


def test_health_before_load(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["loaded"] is False


def test_upload_and_determinations(loaded_client):
    client = loaded_client
    p1 = client.get("/api/peaks/P1").get_json()
    assert p1["determination"]["result"] == "issue"
    assert p1["determination"]["crane"] == "C1"
    assert p1["determination"]["windows"]["C1"]["classification"] == "always"

    p2 = client.get("/api/peaks/P2").get_json()
    assert p2["determination"]["result"] == "pending"

    p4 = client.get("/api/peaks/P4").get_json()
    assert p4["determination"]["result"] == "pending"
    assert "多台" in p4["determination"]["reason"]

    # 半开区间：[5010,5020) 不含 5010，窗口 [5000,5010] 未被覆盖
    p5 = client.get("/api/peaks/P5").get_json()
    assert p5["determination"]["result"] == "pending"

    p7 = client.get("/api/peaks/P7").get_json()
    assert p7["determination"]["crane"] == "C1"


def test_issue_form_and_lookup(loaded_client):
    client = loaded_client
    # 待核峰拒签
    resp = client.post("/api/forms", json={"peak_id": "P2"})
    assert resp.status_code == 409

    resp = client.post("/api/forms", json={"peak_id": "P1"})
    assert resp.status_code == 201
    form_no = resp.get_json()["form_no"]

    again = client.post("/api/forms", json={"peak_id": "P1"})
    assert again.status_code == 409  # 同峰已有有效单

    status = client.get(f"/api/forms/{form_no}").get_json()
    assert status["status"] == "active"
    assert status["crane_id"] == "C1"
    assert client.get("/api/forms/RC-999999").status_code == 404

    peak = client.get("/api/peaks/P1").get_json()
    assert peak["active_form"] == form_no


def test_correction_revokes_form_and_never_restores(loaded_client):
    client = loaded_client
    form_no = client.post("/api/forms",
                          json={"peak_id": "P1"}).get_json()["form_no"]

    # 把 C1 偏移区间改成 [100, 200]：P1 在 C1 钟面窗口 [1100,1200]，无人作业
    resp = client.post("/api/clock-records/1/correct",
                       json={"low_sec": 100, "high_sec": 200})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["revoked_count"] == 1
    assert body["revoked"][0]["form_no"] == form_no

    status = client.get(f"/api/forms/{form_no}").get_json()
    assert status["status"] == "revoked"
    assert "钟差更正" in status["revoke_reason"]

    # 改回原归属：旧单不恢复
    resp2 = client.post("/api/clock-records/1/correct",
                        json={"low_sec": 0, "high_sec": 10})
    assert resp2.status_code == 200
    assert client.get(f"/api/forms/{form_no}").get_json()["status"] == "revoked"

    # 重新签发得到新单号
    new_form = client.post("/api/forms", json={"peak_id": "P1"}).get_json()
    assert new_form["form_no"] != form_no
    assert client.get(f"/api/forms/{new_form['form_no']}").get_json()["status"] == "active"


def test_infeasible_correction_is_rejected_and_everything_untouched(loaded_client):
    client = loaded_client
    form_no = client.post("/api/forms",
                          json={"peak_id": "P1"}).get_json()["form_no"]

    # r1 改 S->C1 [0,10] 为 [50,60]；r4/r5 链经 R 给 C1? 不涉及 C1。
    # 用 r2(S-C2[-5,5]) 与 r4(S-R[0,100])、r5(R-C2[-10,10]) 制造负环：
    # 若 r4 改为 S->R [-100,-50]，则经链 o(C2) <= -50+10 = -40，
    # 而 r2 要求 o(C2) >= -5 且 <= 5，产生矛盾负环。
    resp = client.post("/api/clock-records/4/correct",
                       json={"low_sec": -100, "high_sec": -50})
    assert resp.status_code == 409
    body = resp.get_json()
    assert "conflict_records" in body
    assert set(body["conflict_records"]) <= {2, 4, 5}

    # 数据原样：r4 仍是 [0,100]
    records = {r["rec_id"]: r for r in
               client.get("/api/clock-records").get_json() if r["active"] == 1}
    assert records[4]["low_sec"] == 0 and records[4]["high_sec"] == 100
    # 复核单原样有效
    assert client.get(f"/api/forms/{form_no}").get_json()["status"] == "active"
    # 峰查询仍正常
    assert client.get("/api/peaks/P1").get_json()["determination"]["result"] == "issue"


def test_correction_keeps_form_when_attribution_unchanged(loaded_client):
    client = loaded_client
    form_no = client.post("/api/forms",
                          json={"peak_id": "P1"}).get_json()["form_no"]
    # 把 C1 区间从 [0,10] 收窄到 [2,8]，P1 窗口 [1002,1008] 仍被 [1000,1011) 全覆盖
    resp = client.post("/api/clock-records/1/correct",
                       json={"low_sec": 2, "high_sec": 8})
    assert resp.status_code == 200
    assert resp.get_json()["revoked_count"] == 0
    assert client.get(f"/api/forms/{form_no}").get_json()["status"] == "active"


def test_event_ordering_interleaved(loaded_client):
    client = loaded_client
    # 更正前先签两张单（事件流：issue f1, issue f2, correction, revoke x2）
    f1 = client.post("/api/forms", json={"peak_id": "P1"}).get_json()["form_no"]
    f2 = client.post("/api/forms", json={"peak_id": "P7"}).get_json()["form_no"]
    assert int(f2.split("-")[1]) > int(f1.split("-")[1])

    resp = client.post("/api/clock-records/1/correct",
                       json={"low_sec": 100, "high_sec": 200})
    assert resp.status_code == 200
    revoked = [r["form_no"] for r in resp.get_json()["revoked"]]
    # 两张单都失去原归属；撤销按 form 的入库先后（created_event）核定
    assert revoked == [f1, f2]
    for fno in (f1, f2):
        assert client.get(f"/api/forms/{fno}").get_json()["status"] == "revoked"

    # 事件流水页按事件序号呈现全部交错动作
    page = client.get("/events").data.decode()
    assert "issue" in page and "correction" in page and "revoke" in page


def test_duplicate_upload_rejected(loaded_client):
    client = loaded_client
    resp = upload(client, standard_peaks(), standard_work(), standard_clocks())
    assert resp.status_code == 400
    assert "已导入" in resp.data.decode()


def test_upload_rolls_back_on_bad_csv(client):
    bad_clocks = standard_clocks() + [(6, "S", "C9", 5, 1)]  # high < low 被解析层拦
    resp = upload(client, standard_peaks(), standard_work(), bad_clocks)
    assert resp.status_code == 400
    assert client.get("/api/health").get_json()["loaded"] is False


def test_upload_rolls_back_on_negative_cycle(client):
    # 解析层全部合法，但约束图负环：S->C1 [0,10] 与 C1->S [20,30] 矛盾
    clocks = standard_clocks() + [(6, "C1", "S", 20, 30)]
    resp = upload(client, standard_peaks(), standard_work(), clocks)
    assert resp.status_code == 400
    assert "冲突" in resp.data.decode()
    assert client.get("/api/health").get_json()["loaded"] is False


def test_upload_rejects_crane_using_meter_id(client):
    work = standard_work() + [("S", 1, 2)]
    resp = upload(client, standard_peaks(), work, standard_clocks())
    assert resp.status_code == 400
    assert "保留编号" in resp.data.decode()


def test_upload_rejects_non_integer_rec_id(client):
    clocks = [("R1", "S", "C1", 0, 10)] + standard_clocks()
    resp = upload(client, standard_peaks(), standard_work(), clocks)
    assert resp.status_code == 400
    assert "记录编号" in resp.data.decode()


def test_correction_input_validation(loaded_client):
    client = loaded_client
    bad1 = client.post("/api/clock-records/1/correct", json={"low_sec": 9, "high_sec": 1})
    assert bad1.status_code == 400
    bad2 = client.post("/api/clock-records/1/correct", json={"low_sec": "x", "high_sec": 1})
    assert bad2.status_code == 400
    bad3 = client.post("/api/clock-records/999/correct", json={"low_sec": 0, "high_sec": 1})
    assert bad3.status_code == 404
    # 被拒后原数据不动
    records = {r["rec_id"]: r for r in
               client.get("/api/clock-records").get_json() if r["active"] == 1}
    assert records[1]["low_sec"] == 0 and records[1]["high_sec"] == 10


def test_upload_rejects_unreachable_crane(client):
    work = standard_work() + [("C8", 1, 2)]  # C8 不在任何钟差记录里
    resp = upload(client, standard_peaks(), work, standard_clocks())
    assert resp.status_code == 400
    assert "C8" in resp.data.decode()
    assert client.get("/api/health").get_json()["loaded"] is False


def test_unknown_peak_and_form_404(loaded_client):
    client = loaded_client
    assert client.get("/api/peaks/NOPE").status_code == 404
    assert client.post("/api/forms", json={"peak_id": "NOPE"}).status_code == 404


def test_clock_history_kept(loaded_client):
    client = loaded_client
    client.post("/api/clock-records/1/correct",
                json={"low_sec": 5, "high_sec": 8})
    rows = client.get("/api/clock-records").get_json()
    r1_rows = [r for r in rows if r["rec_id"] == 1]
    assert len(r1_rows) == 2
    assert {r["active"] for r in r1_rows} == {0, 1}
    active = [r for r in r1_rows if r["active"] == 1][0]
    resp = client.post(f"/api/clock-records/{active['rec_id']}/correct",
                       json={"low_sec": 1, "high_sec": 2})
    assert resp.status_code == 200
