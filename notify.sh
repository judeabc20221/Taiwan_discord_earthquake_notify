#!/bin/bash
# 切換到腳本所在目錄 讓last_id.txt等相對路徑檔案正確讀寫
cd "$(dirname "$0")"
# docker容器內套件裝在/opt/venv 有的話優先使用 否則用系統python3
if [ -x /opt/venv/bin/python ]; then
    exec /opt/venv/bin/python ./discord_notify.py "$@"
else
    exec python3 ./discord_notify.py "$@"
fi
