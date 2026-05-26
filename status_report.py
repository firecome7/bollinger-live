#!/usr/bin/env python3.12
"""每30分钟状态汇报 — 从日志文件和API拉数据"""
import json, os, subprocess, time
from datetime import datetime

LOG_FILE = '/home/admin/live_bollinger/bollinger_live.log'

def last_n_lines(path, n=20):
    try:
        r = subprocess.run(['tail', '-n', str(n), path], capture_output=True, text=True, timeout=5)
        return r.stdout.strip().split('\n')
    except:
        return ["无法读取日志"]

def parse_summary_from_log(lines):
    """从日志提取最新状态"""
    summary = []
    for line in lines:
        if '市价平仓' in line or '限价单成交' in line or '入场成交' in line:
            summary.append(line.strip()[:120])
        if '持仓:' in line and '/' in line:
            summary.append(line.strip())
    return summary[-10:] if summary else []

def main():
    lines = last_n_lines(LOG_FILE, 50)
    recent = parse_summary_from_log(lines)
    
    now = datetime.now().strftime('%m-%d %H:%M')
    report = [f"=== Bollinger策略 状态汇报 [{now}] ==="]
    
    if recent:
        report.append("--- 最近活动 ---")
        for r in recent:
            report.append(f"  {r}")
    else:
        report.append("  无最近活动（程序可能未运行）")
    
    report.append("---")
    report.append("监控币种: 连Gate.io后确认")
    report.append("策略: BB25 + 4%挂单入场 + 固定1%止损/4%止盈 + 固定$100/笔 + 最多20仓 + 50x杠杆")
    report.append("注意: 跳空可能打穿限价单，15秒后转市价")
    
    print('\n'.join(report))

if __name__ == '__main__':
    main()
