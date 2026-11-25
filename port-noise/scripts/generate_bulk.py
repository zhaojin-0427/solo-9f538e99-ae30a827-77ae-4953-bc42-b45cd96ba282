#!/usr/bin/env python3
"""生成大体积一季数据（默认 6 万作业区间），用于手工压测索引查询。"""
import argparse
import csv
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/sample")
    parser.add_argument("--intervals-per-crane", type=int, default=20_000)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    cranes = ["C1", "C2", "C3"]
    with open(os.path.join(args.out, "peaks.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["peak_id", "peak_sec"])
        w.writerow(["P1", 1_000_000])

    with open(os.path.join(args.out, "work.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["crane_id", "start_sec", "end_sec"])
        for crane in cranes:
            for i in range(args.intervals_per_crane):
                start = 10_000_000 + i * 37 + (hash(crane) % 37)
                w.writerow([crane, start, start + 30])
        w.writerow(["C1", 1_000_000, 1_000_011])  # 覆盖 P1 的 C1 窗口

    with open(os.path.join(args.out, "clocks.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rec_id", "device_a", "device_b", "low_sec", "high_sec"])
        w.writerow([1, "S", "C1", 0, 10])
        w.writerow([2, "S", "C2", -5, 5])
        w.writerow([3, "S", "C3", 100, 200])
        w.writerow([4, "S", "R", 0, 100])
        w.writerow([5, "R", "C2", -10, 10])
    print("CSV 已生成到", args.out)


if __name__ == "__main__":
    main()
