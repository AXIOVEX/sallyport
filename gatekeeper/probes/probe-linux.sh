#!/bin/sh
echo "SYNCYARD_PROBE 1 platform=linux"
echo "## hostname"
hostname
echo "## cpuinfo"
grep -m1 "model name" /proc/cpuinfo
grep -c "^processor" /proc/cpuinfo | sed 's/^/processors: /'
echo "## meminfo"
grep -E "^(MemTotal|MemAvailable):" /proc/meminfo
echo "## loadavg"
cat /proc/loadavg
echo "## df"
df -kP / | tail -1
echo "## nvidia"
if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=name,memory.total --format=csv,noheader; else echo "nvidia-smi: not found"; fi
echo "## venues"
if command -v docker >/dev/null 2>&1; then echo "venue docker"; fi
echo "PROBE_END"
