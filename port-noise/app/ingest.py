"""首次三份 CSV 的解析与入库（整季一次性导入，重复导入拒绝）。"""
import csv
import io

from . import solver
from .db import is_seed_loaded


MAX_CRANES = 12
METER_ID = solver.METER_ID


def _parse_int(value, what, line_no):
    value = value.strip()
    try:
        return int(value)
    except (TypeError, ValueError):
        raise solver.IngestError(f"第 {line_no} 行：{what} 必须是整数秒，实际为 {value!r}")


def _read_csv(blob, expected_header, label):
    if blob is None:
        raise solver.IngestError(f"缺少 {label} CSV 文件")
    if isinstance(blob, bytes):
        text = blob.decode("utf-8-sig")
    else:
        text = blob.read().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise solver.IngestError(f"{label} CSV 为空")
    header = [h.strip() for h in header]
    if header != expected_header:
        raise solver.IngestError(
            f"{label} CSV 表头应为 {expected_header}，实际为 {header}")
    rows = []
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != len(expected_header):
            raise solver.IngestError(
                f"{label} CSV 第 {line_no} 行列数为 {len(row)}，应为 {len(expected_header)}")
        rows.append((line_no, [c.strip() for c in row]))
    if not rows:
        raise solver.IngestError(f"{label} CSV 没有数据行")
    return rows


def parse_peaks(blob):
    rows = _read_csv(blob, ["peak_id", "peak_sec"], "声级仪噪声峰")
    peaks, seen = [], set()
    for line_no, cols in rows:
        peak_id, sec_text = cols
        if not peak_id:
            raise solver.IngestError(f"声级仪噪声峰 CSV 第 {line_no} 行：峰号为空")
        if peak_id in seen:
            raise solver.IngestError(f"声级仪噪声峰 CSV 第 {line_no} 行：峰号 {peak_id} 重复")
        seen.add(peak_id)
        peaks.append((peak_id, _parse_int(sec_text, "峰时刻", line_no)))
    return peaks


def parse_work(blob):
    rows = _read_csv(blob, ["crane_id", "start_sec", "end_sec"], "吊机作业区间")
    work, cranes = [], set()
    for line_no, cols in rows:
        crane_id, start_text, end_text = cols
        if not crane_id:
            raise solver.IngestError(f"吊机作业 CSV 第 {line_no} 行：吊机编号为空")
        if crane_id == METER_ID:
            raise solver.IngestError(
                f"吊机作业 CSV 第 {line_no} 行：{METER_ID} 是声级仪保留编号，吊机不能使用")
        start = _parse_int(start_text, "作业起始", line_no)
        end = _parse_int(end_text, "作业结束", line_no)
        if end <= start:
            raise solver.IngestError(
                f"吊机作业 CSV 第 {line_no} 行：结束时刻 {end} 必须严格大于起始时刻 {start}（含首不含尾）")
        cranes.add(crane_id)
        work.append((crane_id, start, end))
    if len(cranes) > MAX_CRANES:
        raise solver.IngestError(f"吊机数量 {len(cranes)} 超过上限 {MAX_CRANES} 台")
    return work, sorted(cranes)


def parse_clocks(blob):
    rows = _read_csv(
        blob,
        ["rec_id", "device_a", "device_b", "low_sec", "high_sec"],
        "设备间钟差",
    )
    clocks, seen_ids = [], set()
    for line_no, cols in rows:
        rec_id, a, b, low_text, high_text = cols
        rec_id = _parse_int(rec_id, "记录编号", line_no)
        if rec_id in seen_ids:
            raise solver.IngestError(f"钟差 CSV 第 {line_no} 行：记录编号 {rec_id} 重复")
        seen_ids.add(rec_id)
        if not a or not b:
            raise solver.IngestError(f"钟差 CSV 第 {line_no} 行：设备编号为空")
        if a == b:
            raise solver.IngestError(f"钟差 CSV 第 {line_no} 行：两台设备不能相同")
        low = _parse_int(low_text, "钟差下界", line_no)
        high = _parse_int(high_text, "钟差上界", line_no)
        if high < low:
            raise solver.IngestError(
                f"钟差 CSV 第 {line_no} 行：上界 {high} 小于下界 {low}，无解")
        clocks.append((rec_id, a, b, low, high))
    return clocks


def load_season(conn, peaks_blob, work_blob, clocks_blob):
    """整季三份 CSV 一次性入库。全部校验通过才提交，否则回滚不留残数据。"""
    if is_seed_loaded(conn):
        raise solver.IngestError("本季数据已导入，重复导入被拒绝；钟差请走逐条更正接口")

    peaks = parse_peaks(peaks_blob)
    work, crane_ids = parse_work(work_blob)
    clocks = parse_clocks(clocks_blob)

    clock_devices = set()
    for _rid, a, b, _lo, _hi in clocks:
        clock_devices.add(a)
        clock_devices.add(b)
    if METER_ID not in clock_devices:
        raise solver.IngestError("钟差记录中没有声级仪设备 S，无法建立对时关系")

    unknown = sorted(c for c in crane_ids if c not in clock_devices)
    if unknown:
        raise solver.IngestError(
            f"吊机 {', '.join(unknown)} 未出现在任何钟差记录中，无法连到声级仪")

    # 先入库再用同一套求解器校验可行性与连通性，保证与查询期口径一致
    cur = conn.execute("INSERT INTO events(kind, detail) VALUES ('upload', '{}')")
    upload_event = cur.lastrowid

    conn.execute(
        "INSERT INTO devices(id, role) VALUES (?, 'meter')", (METER_ID,))
    for c in crane_ids:
        conn.execute(
            "INSERT INTO devices(id, role) VALUES (?, 'crane')", (c,))
    for d in sorted(clock_devices - {METER_ID} - set(crane_ids)):
        conn.execute(
            "INSERT INTO devices(id, role) VALUES (?, 'room')", (d,))

    conn.executemany(
        "INSERT INTO peaks(peak_id, peak_sec) VALUES (?, ?)", peaks)
    conn.executemany(
        "INSERT INTO work_intervals(crane_id, start_sec, end_sec) VALUES (?, ?, ?)",
        work)
    conn.executemany(
        "INSERT INTO clock_records(rec_id, device_a, device_b, low_sec, high_sec, "
        "active, created_event) VALUES (?, ?, ?, ?, ?, 1, ?)",
        [(rid, a, b, lo, hi, upload_event) for rid, a, b, lo, hi in clocks],
    )

    bounds, conflict = solver.current_bounds(conn)
    if conflict is not None:
        raise solver.IngestError(
            f"初始钟差记录互相冲突（负环），涉及记录编号：{', '.join(map(str, conflict))}")
    unreachable = [c for c, b in bounds.items() if b is None]
    if unreachable:
        raise solver.IngestError(
            f"吊机 {', '.join(unreachable)} 无法经钟差记录连到声级仪 S")

    return {
        "event_id": upload_event,
        "peaks": len(peaks),
        "work_intervals": len(work),
        "cranes": crane_ids,
        "clock_records": len(clocks),
    }
