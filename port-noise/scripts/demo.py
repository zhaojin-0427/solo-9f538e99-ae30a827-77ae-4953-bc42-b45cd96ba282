#!/usr/bin/env python3
"""端到端演示脚本（不依赖 pytest）：
导入三份 CSV -> 查峰/签发 -> 更正撤单 -> 冲突更正被拒 -> 改回不恢复 -> 重新签发 -> 单号查状态。

用法：python scripts/demo.py
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402


PEAKS = """peak_id,peak_sec
P1,1000
P2,2000
P4,4000
P5,5000
P7,7000
"""

WORK = """crane_id,start_sec,end_sec
C1,1000,1011
C2,1500,1600
C3,9000,9100
C1,2005,2006
C1,4000,4011
C2,3995,4006
C1,5010,5020
C1,7000,7011
C2,7010,7020
"""

CLOCKS = """rec_id,device_a,device_b,low_sec,high_sec
1,S,C1,0,10
2,S,C2,-5,5
3,S,C3,100,200
4,S,R,0,100
5,R,C2,-10,10
"""


def post_file(client, url, files):
    data = {k: (io.BytesIO(v.encode("utf-8")), f"{k}.csv")
            for k, v in files.items()}
    return client.post(url, data=data, content_type="multipart/form-data",
                       follow_redirects=True)


def show(title, resp):
    print(f"\n===== {title} -> HTTP {resp.status_code} =====")
    ctype = resp.headers.get("Content-Type", "")
    if "application/json" in ctype:
        import json
        print(json.dumps(resp.get_json(), ensure_ascii=False, indent=2))
    else:
        print(resp.data.decode("utf-8")[:400])


def main():
    tmp = tempfile.mkdtemp(prefix="port-noise-demo-")
    app = create_app(database=os.path.join(tmp, "demo.sqlite3"))
    client = app.test_client()

    show("健康检查", client.get("/api/health"))
    show("首次三份 CSV 导入", post_file(client, "/upload", {
        "peaks": PEAKS, "work": WORK, "clocks": CLOCKS}))
    show("重复导入（应拒绝）", post_file(client, "/upload", {
        "peaks": PEAKS, "work": WORK, "clocks": CLOCKS}))

    show("查峰 P1（应可签 C1）", client.get("/api/peaks/P1"))
    show("查峰 P2（应待核）", client.get("/api/peaks/P2"))
    show("待核峰 P2 签发（应 409）", client.post("/api/forms", json={"peak_id": "P2"}))

    issued = client.post("/api/forms", json={"peak_id": "P1"})
    show("P1 签发复核单", issued)
    form_no = issued.get_json()["form_no"]
    show(f"按单号 {form_no} 查状态", client.get(f"/api/forms/{form_no}"))

    show("更正记录 1 为 [100,200]（应撤销 P1 的单）",
         client.post("/api/clock-records/1/correct",
                     json={"low_sec": 100, "high_sec": 200}))
    show("撤单后再查单号", client.get(f"/api/forms/{form_no}"))

    show("冲突更正记录 4 为 [-100,-50]（应 409 且给出冲突记录，数据不动）",
         client.post("/api/clock-records/4/correct",
                     json={"low_sec": -100, "high_sec": -50}))

    show("记录 1 改回 [0,10]（旧单不恢复）",
         client.post("/api/clock-records/1/correct",
                     json={"low_sec": 0, "high_sec": 10}))
    show("旧单仍为 revoked", client.get(f"/api/forms/{form_no}"))
    reissued = client.post("/api/forms", json={"peak_id": "P1"})
    show("重新签发（新单号）", reissued)
    show("钟差记录历史（含失效行）", client.get("/api/clock-records"))
    print("\n演示完成。数据库文件：", app.config["DATABASE"])


if __name__ == "__main__":
    main()
