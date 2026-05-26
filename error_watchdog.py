#!/usr/bin/env python3.12
"""异常监控 — 每分钟跑一次，检测日志ERROR"""
import os, subprocess

LOG = '/home/admin/live_bollinger/bollinger_live.log'
STATE_FILE = '/tmp/bollinger_last_error_line'

def count_lines(path):
    try:
        r = subprocess.run(['wc', '-l', path], capture_output=True, text=True, timeout=3)
        return int(r.stdout.split()[0])
    except:
        return 0

def read_errors_since(path, from_line):
    """读取指定行之后的ERROR行"""
    total = count_lines(path)
    if total <= from_line:
        return [], total
    
    start = from_line + 1
    try:
        r = subprocess.run(
            ['sed', '-n', f'{start},{total}p', path],
            capture_output=True, text=True, timeout=5
        )
        lines = r.stdout.strip().split('\n')
        errors = []
        for l in lines:
            l = l.strip()
            if not l:
                continue
            # 捕获ERROR和重要WARNING
            if ' ERROR ' in l or ' CRITICAL ' in l:
                errors.append(l[:200])
            elif 'WARNING' in l and any(kw in l for kw in ['失败', '异常', '取消', '跳过', '无法', '拒绝']):
                errors.append(l[:200])
            elif '需人工检查' in l:
                errors.append(l[:200])
        return errors, total
    except:
        return [], total

def main():
    if not os.path.exists(LOG):
        return  # 还没开始跑
    
    last_line = 0
    if os.path.exists(STATE_FILE):
        try:
            last_line = int(open(STATE_FILE).read().strip())
        except:
            pass
    
    errors, total = read_errors_since(LOG, last_line)
    
    # 更新状态文件
    open(STATE_FILE, 'w').write(str(total))
    
    if errors:
        print(f"🚨 [{__import__('datetime').datetime.now().strftime('%m-%d %H:%M')}] 程序异常!")
        for e in errors[-5:]:  # 最多报5条
            print(f"  {e}")

if __name__ == '__main__':
    main()
