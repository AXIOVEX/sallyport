Write-Output "SYNCYARD_PROBE 1 platform=windows"
Write-Output "## hostname"
hostname
Write-Output "## cpu"
Get-CimInstance Win32_Processor | Select-Object -First 1 | Format-List Name,NumberOfCores,NumberOfLogicalProcessors
Write-Output "## os"
Get-CimInstance Win32_OperatingSystem | Format-List TotalVisibleMemorySize,FreePhysicalMemory
Write-Output "## disk"
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Format-List DeviceID,FreeSpace,Size
Write-Output "## nvidia"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { nvidia-smi --query-gpu=name,memory.total --format=csv,noheader } else { Write-Output "nvidia-smi: not found" }
Write-Output "## venues"
if (Get-Command wsl -ErrorAction SilentlyContinue) { Write-Output "venue wsl" }
if (Get-Command docker -ErrorAction SilentlyContinue) { Write-Output "venue docker" }
Write-Output "PROBE_END"
