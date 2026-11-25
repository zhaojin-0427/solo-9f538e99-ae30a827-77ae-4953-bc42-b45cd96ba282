"""钟差约束求解与峰归属判定。

约束模型
========
每条有效钟差记录 (a, b, low, high) 表示两台设备时钟读数之差满足：
    low  <= clock(b) - clock(a) <= high          （整数秒）

令 offset(x) = clock(x) - clock(S)（S 为声级仪），一条记录等价于两条
差分约束：
    offset(b) - offset(a) <= high      边 a -> b, 权 high
    offset(a) - offset(b) <= -low      边 b -> a, 权 -low

因此：
* 无解 <=> 约束图存在负环，负环上的边映射回钟差记录即为一组冲突记录；
* 对每台吊机 c，其相对声级仪的可行偏移是一个整数闭区间
      max_Sc <= offset(c) <= min_cS
  （S -> c 的最长路径下界，c -> S 的最长路径上界，由 Floyd-Warshall 一次算出），
  区间内每个整数都可取到，绝不逐秒试探；
* 峰在声级仪时刻 t 发生，则它在吊机 c 钟面上的可能时刻为 [t+lo, t+hi]。
"""
from __future__ import annotations

from collections import defaultdict


METER_ID = "S"


class IngestError(ValueError):
    """输入数据不合法（CSV 内容/连接性/可行性问题）。"""


# --------------------------------------------------------------------------
# 约束图
# --------------------------------------------------------------------------
def build_graph(records):
    """根据有效钟差记录构造差分约束图。

    records: 可迭代对象，每项含 rec_id, device_a, device_b, low_sec, high_sec。
    返回 (nodes, edges)，edges 元素为 (u, v, weight, rec_id)。
    """
    nodes = set()
    edges = []
    for r in records:
        a, b = r["device_a"], r["device_b"]
        nodes.add(a)
        nodes.add(b)
        # offset(b) - offset(a) <= high
        edges.append((a, b, int(r["high_sec"]), r["rec_id"]))
        # offset(a) - offset(b) <= -low
        edges.append((b, a, -int(r["low_sec"]), r["rec_id"]))
    return nodes, edges


def find_negative_cycle(edges, nodes):
    """Bellman-Ford 找一个负环，返回环上涉及的钟差记录 rec_id 集合；无负环返回 None。

    节点规模 = 设备数（声级仪 + 至多 12 台吊机 + 设备间），天然很小。
    """
    dist = {n: 0 for n in nodes}  # 全零初值：等价于增设超级源点
    prev = {n: None for n in nodes}
    n_nodes = len(nodes)
    relaxed = None
    for _ in range(n_nodes):
        relaxed = None
        for u, v, w, rec_id in edges:
            nd = dist[u] + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = (u, rec_id)
                relaxed = v
    if relaxed is None:
        return None
    # 第 n 轮仍可松弛 => 有负环；沿前驱回溯 n 步必落在环上
    x = relaxed
    for _ in range(n_nodes):
        x = prev[x][0]
    cycle_records = []
    cur = x
    while True:
        prev_node, rec_id = prev[cur]
        cycle_records.append(rec_id)
        cur = prev_node
        if cur == x:
            break
    # 去重但保留顺序，便于提示
    seen = set()
    ordered = []
    for rid in cycle_records:
        if rid not in seen:
            seen.add(rid)
            ordered.append(rid)
    return ordered


def floyd_warshall(nodes, edges):
    """全源最短路径。差分约束 x_v - x_u <= w(u,v) 下，u->v 的最短路径
    就是对 x_v - x_u 的最紧上界。返回 dist[u][v]，不可达为 None，dist[u][u]=0。

    调用前应已确认无负环（即约束系统可行）。
    """
    dist = {u: {v: (0 if u == v else None) for v in nodes} for u in nodes}
    for u, v, w, _rec in edges:
        cur = dist[u][v]
        if cur is None or w < cur:
            dist[u][v] = w
    for k in nodes:
        dk = dist[k]
        for i in nodes:
            dik = dist[i][k]
            if dik is None:
                continue
            di = dist[i]
            for j in nodes:
                dkj = dk[j]
                if dkj is None:
                    continue
                cand = dik + dkj
                if di[j] is None or cand < di[j]:
                    di[j] = cand
    return dist


def meter_relative_bounds(nodes, edges, crane_ids):
    """每台吊机相对声级仪的可行偏移整数区间 (lo, hi)。

    最短路径 dist[S][c] 给出 o(c)-o(S) 的最紧上界 => hi = dist[S][c]；
    最短路径 dist[c][S] 给出 o(S)-o(c) 的最紧上界 => lo = -dist[c][S]。
    区间内每个整数都可取到，与声级仪不连通的吊机返回 None。
    """
    reachable = _reachable_from_meter(nodes, edges)
    dist = floyd_warshall(nodes, edges)
    out = {}
    for c in crane_ids:
        if c not in reachable:
            out[c] = None
            continue
        lo = -dist[c][METER_ID]
        hi = dist[METER_ID][c]
        out[c] = (lo, hi)
    return out


def _reachable_from_meter(nodes, edges):
    adj = defaultdict(list)
    for u, v, _w, _rid in edges:
        adj[u].append(v)
    seen = {METER_ID}
    stack = [METER_ID]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


# --------------------------------------------------------------------------
# 作业区间判定（含首不含尾的整数区间）
# --------------------------------------------------------------------------
def classify_window(intervals, win_lo, win_hi):
    """吊机一组作业区间对目标窗口 [win_lo, win_hi]（含两端）的覆盖分类。

    intervals: 已按 start_sec 排序的 (start, end) 列表，且 end > start。
    返回 'always'（窗口每个整数时刻都在某个含首不含尾区间内）、
         'never'（没有任何区间与窗口相交）、
         'maybe'（部分相交 / 部分覆盖）。
    """
    if not intervals:
        return "never"
    if intervals[0][0] > win_hi or intervals[-1][1] <= win_lo:
        return "never"

    covered_until = win_lo - 1  # 已连续覆盖到该时刻
    for start, end in intervals:
        if end <= win_lo:
            continue
        if start > win_hi:
            break
        if start > covered_until + 1:
            return "maybe"  # 出现缺口
        if end - 1 > covered_until:
            covered_until = end - 1
        if covered_until >= win_hi:
            return "always"
    return "always" if covered_until >= win_hi else "maybe"


# --------------------------------------------------------------------------
# 峰归属判定
# --------------------------------------------------------------------------
def determine_peak(conn, peak_row, crane_ids, bounds):
    """判定单个噪声峰的归属。

    bounds: meter_relative_bounds 的结果，吊机 -> (lo, hi) 或 None（不连通）。
    返回 dict：
      result: 'issue' | 'pending'
      crane:  签发对象吊机号（issue 时）
      windows: 各吊机的偏移窗口与分类，供页面展示
      reason: pending 原因
    """
    t = peak_row["peak_sec"]
    windows = {}
    always_cranes = []
    unreachable = []
    ambiguous = []
    for c in crane_ids:
        b = bounds[c]
        if b is None:
            unreachable.append(c)
            windows[c] = {"lo": None, "hi": None, "classification": "unreachable"}
            continue
        lo, hi = b
        win_lo, win_hi = t + lo, t + hi
        # 索引区间查询：只读与该窗口相交的行，不扫全季区间
        rows = conn.execute(
            "SELECT start_sec, end_sec FROM work_intervals "
            "WHERE crane_id = ? AND start_sec <= ? AND end_sec > ? "
            "ORDER BY start_sec",
            (c, win_hi, win_lo),
        ).fetchall()
        intervals = [(r["start_sec"], r["end_sec"]) for r in rows]
        cls = classify_window(intervals, win_lo, win_hi)
        windows[c] = {"lo": lo, "hi": hi, "classification": cls,
                      "win_lo": win_lo, "win_hi": win_hi}
        if cls == "always":
            always_cranes.append(c)
        elif cls == "maybe":
            ambiguous.append(c)

    if unreachable:
        return {"result": "pending", "crane": None, "windows": windows,
                "reason": f"吊机 {', '.join(unreachable)} 无法经钟差记录连到声级仪"}
    if len(always_cranes) == 1 and not ambiguous:
        return {"result": "issue", "crane": always_cranes[0],
                "windows": windows, "reason": None}
    if len(always_cranes) > 1:
        reason = f"多台吊机在全部可行对时均作业：{', '.join(always_cranes)}"
    elif ambiguous:
        reason = f"吊机 {', '.join(ambiguous)} 的作业状态随可行对时变化，归属不唯一"
    else:
        reason = "所有可行对时下均无吊机在作业"
    return {"result": "pending", "crane": None, "windows": windows, "reason": reason}


def active_clock_records(conn):
    return conn.execute(
        "SELECT rec_id, device_a, device_b, low_sec, high_sec "
        "FROM clock_records WHERE active = 1 ORDER BY rec_id"
    ).fetchall()


def list_cranes(conn):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM devices WHERE role = 'crane' ORDER BY id")]


def current_bounds(conn):
    """基于当前有效钟差记录计算可行偏移；返回 (bounds, conflict_rec_ids)。"""
    cranes = list_cranes(conn)
    records = active_clock_records(conn)
    nodes, edges = build_graph(records)
    conflict = find_negative_cycle(edges, nodes)
    if conflict is not None:
        return None, conflict
    bounds = meter_relative_bounds(nodes, edges, cranes)
    return bounds, None
