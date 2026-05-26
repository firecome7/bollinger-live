#!/usr/bin/env python3.12
"""如果.watchdog_output有内容就输出（no_agent模式推送）"""
import os

WATCHDOG = '/home/admin/live_bollinger/.watchdog_output'
if os.path.exists(WATCHDOG):
    content = open(WATCHDOG).read().strip()
    if content:
        print(content)
