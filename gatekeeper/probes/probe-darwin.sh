#!/bin/sh
echo "SYNCYARD_PROBE 1 platform=darwin"
echo "## hostname"
hostname
echo "## sysctl"
sysctl -n machdep.cpu.brand_string
echo "cores: $(sysctl -n hw.ncpu)"
echo "memsize: $(sysctl -n hw.memsize)"
echo "## vm_stat"
vm_stat | head -5
echo "## loadavg"
sysctl -n vm.loadavg
echo "## df"
df -k / | tail -1
echo "## gpu"
system_profiler SPDisplaysDataType | grep -E "Chipset Model|VRAM|Metal"
echo "## venues"
if command -v docker >/dev/null 2>&1; then echo "venue docker"; fi
echo "PROBE_END"
