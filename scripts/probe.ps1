$ErrorActionPreference = 'Stop'
Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture
Get-CimInstance Win32_ComputerSystem | Select-Object NumberOfLogicalProcessors,TotalPhysicalMemory
Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM
Get-Volume | Where-Object DriveLetter | Select-Object DriveLetter,FileSystem,SizeRemaining,Size
Write-Output 'Development machine only. Confirm target server, service account and TLS during deployment.'
