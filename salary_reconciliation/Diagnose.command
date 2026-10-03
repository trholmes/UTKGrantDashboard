#!/bin/zsh
# TEMPORARY: double-click to describe the reports in the data folder. Writes
# diagnostic.txt (counts and account codes only - no names or amounts) next
# to this file. Read it, then send it back.
cd "$(dirname "$0")"
exec python3 diagnose.py
