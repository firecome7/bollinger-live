#!/usr/bin/env python3.12
"""每2分钟检测异常 — 输出到文件"""
import sys, os, subprocess
sys.path.insert(0, '/home/admin/live_bollinger')

LOG = '/home/admin/live_bollinger/bollinger_live.log'
STATE = '/home/admin/live_bollinger/.watchdog_state'
OUTPUT = '/home/admin/live_bollinger/.watchdog_output'

if not os.path.exists(LOG):
    open(OUTPUT, 'w').close()
    exit(0)

def count_lines(path):
    r = subprocess.run(['wc', '-l', path], capture_output=True, text=True, timeout=3)
    return int(r.stdout.split()[0])

def read_errors_since(path, from_line):
    total = count_lines(path)
    if total <= from_line: return [], total
    try:
        r = subprocess.run(['sed', '-n', f'{from_line+1},{total}p', path], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split('\n')
        errors = []
        for l in lines:
            l = l.strip()
            if not l: continue
            if ' ERROR ' in l or ' CRITICAL ' in l:
                errors.append(l[:200])
            elif 'WARNING' in l and any(kw in l for kw in ['失败', '异常', '取消', '跳过']):
                errors.append(l[:200])
        return errors, total
    except:
        return [], total

last_line = 0
if os.path.exists(STATE):
    try: last_line = int(open(STATE).read().strip())
    except: pass

errors, total = read_errors_since(LOG, last_line)
open(STATE, 'w').write(str(total))

if errors:
    from datetime import datetime
    with open(OUTPUT, 'w') as f:
        f.write(f"🚨 [{datetime.now().strftime('%m-%d %H:%M')}] 程序异常!\n")
        for e in errors[-5:]:
            f.write(f"  {e}\n")
else:
    open(OUTPUT, 'w').close()
