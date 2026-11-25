# 码头夜间噪声峰复核系统

把声级仪记录的夜间噪声峰，交给当时正在作业的吊机班组复核的 Flask + SQLite 应用。
整季数据一次性导入，之后只允许逐条更正钟差；系统按入库先后核定复核单的签发与撤销。

## 业务规则与实现口径

### 数据模型（全部时刻/钟差为整数秒）

| CSV | 列 | 说明 |
|---|---|---|
| 声级仪噪声峰 | `peak_id, peak_sec` | 峰号与声级仪钟面时刻 |
| 吊机作业区间 | `crane_id, start_sec, end_sec` | **含首不含尾** `[start, end)`，至多 12 台吊机 |
| 设备间钟差 | `rec_id, device_a, device_b, low_sec, high_sec` | `low ≤ clock(b) − clock(a) ≤ high`；设备偏移整季固定 |

声级仪固定编号 `S`。每条钟差记录必须能让每台吊机经记录图连通到 `S`，导入时强校验。

### 钟差求解：差分约束，不逐秒试偏移

令 `offset(x) = clock(x) − clock(S)`。一条记录 `(a,b,low,high)` 等价于两条差分约束：

```
offset(b) − offset(a) ≤ high      边 a → b，权 high
offset(a) − offset(b) ≤ −low      边 b → a，权 −low
```

* **无解判定**：约束图出现负环。Bellman-Ford 找到负环后，把环上的边映射回钟差记录，
  即返回一组冲突记录编号（不是泛泛报错）。
* **每台吊机的可行偏移**：Floyd-Warshall 全源**最短路径**给出最紧上界，
  吊机 `c` 的可行偏移是整数闭区间 `[−dist[c][S], dist[S][c]]`，区间内每个整数都可取到。
  设备数 = 声级仪 + 至多 12 台吊机 + 少量设备间，图极小。
* 峰在声级仪时刻 `t` 发生，则它在吊机 `c` 钟面上的可能时刻窗口为
  `[t + lo, t + hi]`，只需要对这个窗口做一次区间覆盖判定，**与窗口宽度无关**。

### 峰归属判定（`app/solver.py`）

对每台吊机，用索引只取出与该窗口相交的作业区间，分类为：

* `always`：窗口内每个整数时刻都落在某个 `[start, end)` 内（相邻区间在整数秒上无缝拼接）；
* `never`：窗口与任何区间都不相交；
* `maybe`：部分覆盖/有缺口，即作业状态随可行对时变化。

只有当**恰好一台**吊机 `always` 且其余全部 `never` 时，才可给该吊机班组签发复核单；
多机始终作业、状态随对时变化、全部未作业、吊机不连通，一律标为**待核**。

### 复核单生命周期与事件顺序

* 上传、签发、更正、撤销都写入同一张 `events` 自增流水表，**入库先后即核定先后**。
* 复核单号 `RC-事件序号`；同一峰同时只允许一张有效单（部分唯一索引保证）。
* 更正钟差时先在内存图上试解：
  * 出现负环 → 拒绝更正，返回冲突记录编号，**库内数据和复核单一字不动**；
  * 可行 → 旧记录置 `active=0` 留痕、同号插入新版本，然后按 `created_event` 顺序
    重新核定所有有效单：峰不再唯一归属原班组（含归属改变与变为待核）的单全部撤销，
    响应中列出被撤销单号便于追回。
* 撤销的单永久保留；**即使之后钟差改回原归属，旧单也不恢复**，需要时重新签发拿新单号。
* 按单号查状态走主键点查。

### 读取量不随全季区间数线性增长

作业表上有复合覆盖索引 `(crane_id, start_sec, end_sec)`，峰核定 SQL 为

```sql
SELECT start_sec, end_sec FROM work_intervals
WHERE crane_id = ? AND start_sec <= ? AND end_sec > ?
ORDER BY start_sec
```

`EXPLAIN QUERY PLAN` 实测为 `SEARCH ... USING COVERING INDEX`，读取量只与
“该吊机与该窗口相交的区间数”有关；6 万条全季区间下查单峰实测约 3ms。
单号查询为 `SEARCH ... USING INDEX sqlite_autoindex_forms_1`。

## 目录结构

```
app/            Flask 应用（schema.sql、db、ingest、solver、routes、模板/静态资源）
tests/          pytest 用例：求解器数学、API 行为、索引/不逐秒性能约束
scripts/demo.py 端到端演示（导入→签发→更正撤单→冲突拒绝→改回不恢复→重签）
scripts/generate_bulk.py 生成大体积一季 CSV 供手工压测
sample-data/    三份示例 CSV
Dockerfile, docker-compose.yml, requirements.txt
```

## docker compose 启动

```bash
docker compose up --build
# 浏览器打开 http://localhost:8000
```

SQLite 库文件位于容器内 `/data/port_noise.sqlite3`，挂在命名卷 `port_noise_data` 上，
容器重建数据不丢。本地不带 Docker 时：

```bash
pip install -r requirements.txt
flask --app app run --port 8000        # 库默认在 ./data/port_noise.sqlite3
```

## 使用流程

1. 打开“数据导入”，一次上传三份 CSV（整季只允许导入一次，重复导入拒绝；校验失败整笔回滚）。
2. “峰查询”输入峰号查看每台吊机的可行偏移窗口与作业判定；可签峰一键签发。
3. “钟差记录/更正”逐条改 `[low, high]`；无解时页面/API 给出冲突记录，数据不动。
4. 更正生效后响应与“复核单”页列出撤销单号；按单号可随时查状态与撤销原因。
5. “事件流水”按序号回看上传/签发/更正/撤销的交错过程。

## HTTP API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 是否已导入 |
| GET | `/api/peaks/<peak_id>` | 峰归属判定（含各吊机窗口分类、冲突记录） |
| POST | `/api/forms` | body `{"peak_id": "..."}` 签发；待核峰/重复签发返回 409 |
| GET | `/api/forms/<form_no>` | 单号查状态 |
| GET | `/api/clock-records` | 钟差记录全量（含失效历史行） |
| POST | `/api/clock-records/<rec_id>/correct` | body `{"low_sec": n, "high_sec": m}`；无解返回 409 + `conflict_records` |

## 测试与演示

```bash
pip install pytest flask
pytest -q
python scripts/demo.py
python scripts/generate_bulk.py --out /tmp/bulk   # 生成 6 万区间压测数据
```
