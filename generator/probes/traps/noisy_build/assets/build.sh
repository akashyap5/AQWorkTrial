#!/bin/bash
cd /app
python3 pack.py 2>&1 | python3 build_report.py
