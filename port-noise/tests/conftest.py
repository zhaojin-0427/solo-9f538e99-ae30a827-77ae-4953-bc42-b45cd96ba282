import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    application = create_app(database=str(tmp_path / "test.sqlite3"))
    application.config.update(TESTING=True)
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


def csv_file(rows, header):
    buf = io.StringIO()
    buf.write(",".join(header) + "\n")
    for row in rows:
        buf.write(",".join(str(x) for x in row) + "\n")
    data = buf.getvalue().encode("utf-8")
    return io.BytesIO(data)


def _named(blob, name):
    blob.name = name
    return blob


def upload(client, peaks, work, clocks):
    return client.post(
        "/upload",
        data={
            "peaks": _named(csv_file(peaks, ["peak_id", "peak_sec"]), "peaks.csv"),
            "work": _named(csv_file(work, ["crane_id", "start_sec", "end_sec"]), "work.csv"),
            "clocks": _named(csv_file(
                clocks,
                ["rec_id", "device_a", "device_b", "low_sec", "high_sec"]),
                "clocks.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


# 标准测试数据集：
# 偏移区间  C1: [0,10]   C2: [-5,5]（另有 S-R-C2 链）   C3: [100,200]
# r4: S->R [0,100], r5: R->C2 [-10,10]
def standard_clocks():
    return [
        (1, "S", "C1", 0, 10),
        (2, "S", "C2", -5, 5),
        (3, "S", "C3", 100, 200),
        (4, "S", "R", 0, 100),
        (5, "R", "C2", -10, 10),
    ]


def standard_peaks():
    # P1 可签 C1；P2 待核；P4 双机始终作业待核；P5 半开区间边界待核；P7 可签 C1
    return [
        ("P1", 1000),
        ("P2", 2000),
        ("P4", 4000),
        ("P5", 5000),
        ("P7", 7000),
    ]


def standard_work():
    return [
        # P1：C1 窗口 [1000,1010] 全覆盖；C2 窗口 [995,1005] 不相交 => 可签 C1
        ("C1", 1000, 1011),
        ("C2", 1500, 1600),
        # P2：C1 窗口 [2000,2010] 仅沾边 => 待核
        ("C1", 2005, 2006),
        # P4：C1 [4000,4010] 全覆盖；C2 [3995,4005] 也全覆盖 => 双机
        ("C1", 4000, 4011),
        ("C2", 3995, 4006),
        # P5：C1 窗口 [5000,5010]，区间 [5010,5020) 不含 5010 => 待核
        ("C1", 5010, 5020),
        # P7：C1 窗口 [7000,7010] 全覆盖；C2 窗口 [6995,7005] 不相交
        ("C1", 7000, 7011),
        ("C2", 7010, 7020),
    ]


@pytest.fixture()
def loaded_client(client):
    resp = upload(client, standard_peaks(), standard_work(), standard_clocks())
    assert resp.status_code == 200, resp.data.decode()
    return client
