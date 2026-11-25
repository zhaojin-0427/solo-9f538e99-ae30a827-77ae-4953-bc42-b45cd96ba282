-- 声级仪噪声峰复核系统 —— 单季数据结构
PRAGMA foreign_keys = ON;

-- 设备：声级仪一台 + 至多 12 台吊机 + 可能出现的中间设备间
CREATE TABLE IF NOT EXISTS devices (
    id        TEXT PRIMARY KEY,
    role      TEXT NOT NULL CHECK (role IN ('meter', 'crane', 'room'))
);

-- 声级仪记录的夜间噪声峰
CREATE TABLE IF NOT EXISTS peaks (
    peak_id   TEXT PRIMARY KEY,
    peak_sec  INTEGER NOT NULL
);

-- 吊机作业区间 [start_sec, end_sec) —— 含首不含尾
CREATE TABLE IF NOT EXISTS work_intervals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    crane_id   TEXT NOT NULL REFERENCES devices(id),
    start_sec  INTEGER NOT NULL,
    end_sec    INTEGER NOT NULL CHECK (end_sec > start_sec)
);
-- 复核单峰/验单时按吊机 + 时刻走索引区间查询，读取量与全季区间总数无关
CREATE INDEX IF NOT EXISTS idx_work_crane_time
    ON work_intervals(crane_id, start_sec, end_sec);

-- 设备间钟差记录：(device_b 时钟读数) - (device_a 时钟读数) ∈ [low, high]，整秒。
-- rec_id 是用户侧记录编号；逐条更正会插入同 rec_id 的新版本，旧版本置 active=0，
-- 行主键用隐式 rowid，版本先后由 created_event（事件序号）区分。
CREATE TABLE IF NOT EXISTS clock_records (
    rec_id  INTEGER NOT NULL,
    device_a TEXT NOT NULL,
    device_b TEXT NOT NULL,
    low_sec  INTEGER NOT NULL,
    high_sec INTEGER NOT NULL CHECK (high_sec >= low_sec),
    active   INTEGER NOT NULL DEFAULT 1,
    superseded_by INTEGER,
    created_event INTEGER,
    CHECK (device_a <> device_b)
);
CREATE INDEX IF NOT EXISTS idx_clock_rec_id ON clock_records(rec_id, active);
CREATE INDEX IF NOT EXISTS idx_clock_active ON clock_records(active);

-- 事件流水：首传、更正、签发、撤销一律按入库先后取号，核定顺序即事件序号
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT NOT NULL CHECK (kind IN ('upload', 'correction', 'issue', 'revoke')),
    detail  TEXT NOT NULL DEFAULT '{}'
);

-- 复核单。同一峰同时只允许一张有效单；旧单撤销后保留，改回原归属也不恢复
CREATE TABLE IF NOT EXISTS forms (
    form_no        TEXT PRIMARY KEY,
    peak_id        TEXT NOT NULL REFERENCES peaks(peak_id),
    crane_id       TEXT NOT NULL REFERENCES devices(id),
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    created_event  INTEGER NOT NULL REFERENCES events(id),
    revoked_event  INTEGER REFERENCES events(id),
    revoke_reason  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_form_active_per_peak
    ON forms(peak_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_form_status ON forms(status);
