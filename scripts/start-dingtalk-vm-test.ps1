param(
    [Parameter(Mandatory)][string]$VMName,
    [Parameter(Mandatory)][string]$CredentialFile,
    [string]$GuestRoot = 'C:\AGICO-DingTalk-Test-20261019',
    [ValidateRange(1024,65535)][int]$ListenPort = 18766,
    [ValidateRange(1024,65535)][int]$GuestPort = 8766,
    [switch]$InstallStartup
)
# Host-only pilot entry. No company-wide listener or production database changes.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $root '.local/dingtalk-vm-proxy.json'
$entry = "http://127.0.0.1:$ListenPort"
$registryPath = 'HKLM:\SYSTEM\CurrentControlSet\Services\PortProxy\v4tov4\tcp'
$proxyKey = "127.0.0.1/$ListenPort"
$mutex = New-Object Threading.Mutex($false, 'Global\AGICO_DINGTALK_VM_RECOVERY')
$locked = $false
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { return }
    $vm = Get-VM -Name $VMName
    $saved = if (Test-Path -LiteralPath $statePath) { Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } else { $null }
    if ($saved -and ($saved.vm_id -ne [string]$vm.Id -or $saved.listen_port -ne $ListenPort -or $saved.guest_root -ne $GuestRoot)) {
        throw 'Pilot proxy state belongs to a different deployment.'
    }
    if ($vm.State -ne 'Running') { Start-VM -VM $vm }
    $address = $null
    for ($i=0; $i -lt 30; $i++) {
        $address = @(Get-VMNetworkAdapter -VMName $VMName | ForEach-Object { $_.IPAddresses } |
            Where-Object { $_ -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' }) | Select-Object -First 1
        if ($address) { break }
        Start-Sleep -Seconds 2
    }
    if (-not $address) { throw 'The pilot VM has not acquired a private IPv4 address.' }
    $adapter = Get-VMNetworkAdapter -VMName $VMName | Select-Object -First 1
    if ($adapter.SwitchName -ne 'Default Switch') { throw 'This pilot helper requires the verified Default Switch.' }
    $hostAddress = Get-NetIPAddress -InterfaceAlias 'vEthernet (Default Switch)' -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1 -ExpandProperty IPAddress
    if (-not $hostAddress) { throw 'Default Switch host address unavailable.' }
    $proxyValue = if (Test-Path $registryPath) { (Get-ItemProperty -LiteralPath $registryPath).PSObject.Properties[$proxyKey].Value } else { $null }
    if ($proxyValue -and (-not $saved -or $proxyValue -ne $saved.proxy_target)) { throw 'An unowned port proxy occupies the pilot endpoint.' }
    if (-not $proxyValue -and (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue)) {
        throw 'The pilot port is occupied by an unrelated listener.'
    }
    $ready = $false
    if ($saved -and $saved.proxy_target -eq "$address/$GuestPort") {
        try { $ready = (Invoke-RestMethod "$entry/health/ready" -TimeoutSec 3).status -eq 'ready' } catch {}
    }
    if (-not $ready) {
        Start-Service WinRM
        $trusted = (Get-Item WSMan:\localhost\Client\TrustedHosts).Value
        $trustedEntries = @($trusted -split ',' | Where-Object { $_ })
        if ($trustedEntries -notcontains $address) {
            Set-Item WSMan:\localhost\Client\TrustedHosts -Value (($trustedEntries + $address) -join ',') -Force
        }
        $credential = Import-Clixml -LiteralPath $CredentialFile
        Invoke-Command -ComputerName $address -Authentication Negotiate -Credential $credential -ScriptBlock {
            param($pilotRoot, $publicUrl, $currentHost, $port)
            $ErrorActionPreference = 'Stop'
            $evidence = Get-Content -Raw -LiteralPath (Join-Path $pilotRoot 'deployment-evidence.json') | ConvertFrom-Json
            if (-not $evidence.test_data) { throw 'Target is not the isolated test deployment.' }
            $configPath = Join-Path $pilotRoot 'test-data/config/runtime.json'
            $runtime = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
            if ($runtime.AGICO_KB_DATABASE_URL -notmatch 'agico_dingtalk_test_') { throw 'Refusing to change a non-test runtime.' }
            $task = Get-ScheduledTask -TaskName 'AgicoKbDingTalkTest'
            if ($task.Actions.Arguments -notlike "*$pilotRoot\run-vm.py*") { throw 'The test task is not owned by this deployment.' }
            $changed = $runtime.AGICO_KB_PUBLIC_URL -ne $publicUrl
            if ($changed) {
                $runtime.AGICO_KB_PUBLIC_URL = $publicUrl
                [IO.File]::WriteAllText($configPath, ($runtime | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
            }
            # This named rule was created for this pilot, on the host-only port.
            $rule = Get-NetFirewallRule -Name 'AgicoKbDingTalkTest-Host'
            if (($rule | Get-NetFirewallPortFilter).LocalPort -ne [string]$port) { throw 'Unexpected pilot firewall rule.' }
            Set-NetFirewallRule -Name $rule.Name -RemoteAddress $currentHost | Out-Null
            Start-Service AgicoKbDatabase
            if ($changed -or $task.State -ne 'Running') {
                Stop-ScheduledTask -TaskName $task.TaskName
                $pattern = [regex]::Escape($pilotRoot + '\run-vm.py')
                Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $pattern -and $_.Name -eq 'python.exe' } |
                    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
                Start-ScheduledTask -TaskName $task.TaskName
            }
        } -ArgumentList $GuestRoot, $entry, $hostAddress, $GuestPort
        $credential = $null
        Start-Service iphlpsvc
        $operation = if ($proxyValue) { 'set' } else { 'add' }
        & netsh.exe interface portproxy $operation v4tov4 listenaddress=127.0.0.1 listenport=$ListenPort connectaddress=$address connectport=$GuestPort | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Unable to configure the host-only pilot proxy.' }
        @{vm_id=[string]$vm.Id; vm_name=$VMName; guest_root=$GuestRoot; listen_port=$ListenPort;
            proxy_target="$address/$GuestPort"; entry=$entry} | ConvertTo-Json |
            Set-Content -LiteralPath $statePath -Encoding UTF8
        for ($i=0; $i -lt 15; $i++) {
            try { $ready = (Invoke-RestMethod "$entry/health/ready" -TimeoutSec 2).status -eq 'ready' } catch {}
            if ($ready) { break }
            Start-Sleep -Seconds 1
        }
        if (-not $ready) { throw 'Pilot startup is incomplete; inspect the protected guest API log.' }
    }
    if ($InstallStartup) {
        Set-VM -VM $vm -AutomaticStartAction Start -AutomaticStartDelay 15
        $taskName = 'AGICO-DingTalk-Pilot-Recovery'
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($existing -and $existing.Actions.Arguments -notlike "*$PSCommandPath*") { throw 'Recovery task belongs to another installation.' }
        $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -VMName "{1}" -CredentialFile "{2}" -GuestRoot "{3}" -ListenPort {4} -GuestPort {5}' -f $PSCommandPath,$VMName,$CredentialFile,$GuestRoot,$ListenPort,$GuestPort
        $action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments
        $triggers = @((New-ScheduledTaskTrigger -AtLogOn -User $user), (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)))
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -StartWhenAvailable
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
    }
    [pscustomobject]@{entry=$entry; vm_address=$address; ready=$ready; startup_installed=[bool]$InstallStartup} | ConvertTo-Json -Compress
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
