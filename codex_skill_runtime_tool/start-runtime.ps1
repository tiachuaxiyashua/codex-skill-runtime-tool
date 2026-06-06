[CmdletBinding()]
param(
    [string]$RuntimeEnv = "",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$CheckOnly,
    [switch]$NoBrowser,
    [switch]$NoStartUi,
    [switch]$NoStartConfiguredDeps
)

$ErrorActionPreference = "Stop"

$ToolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $ToolRoot

if ([string]::IsNullOrWhiteSpace($RuntimeEnv)) {
    $RuntimeEnv = Join-Path $ToolRoot "config\skill-runtime.env"
}

if (-not (Test-Path -LiteralPath $RuntimeEnv)) {
    throw "Runtime env file not found: $RuntimeEnv"
}

$RuntimeEnv = (Resolve-Path -LiteralPath $RuntimeEnv).Path

function Expand-RuntimeValue {
    param(
        [string]$Value,
        [hashtable]$Values
    )

    if ($null -eq $Value) {
        return ""
    }

    $expanded = $Value
    $expanded = $expanded.Replace('${SKILL_RUNTIME_TOOL_ROOT}', $ToolRoot)
    $expanded = $expanded.Replace('${SKILL_RUNTIME_WORKSPACE_ROOT}', $WorkspaceRoot)

    for ($i = 0; $i -lt 8; $i++) {
        $script:RuntimeExpandChanged = $false
        $expanded = [regex]::Replace($expanded, '\$\{([A-Za-z_][A-Za-z0-9_]*)\}', {
            param($match)
            $name = $match.Groups[1].Value
            if ($Values.ContainsKey($name)) {
                $script:RuntimeExpandChanged = $true
                return [string]$Values[$name]
            }
            $envValue = [Environment]::GetEnvironmentVariable($name)
            if ($null -ne $envValue) {
                $script:RuntimeExpandChanged = $true
                return $envValue
            }
            return $match.Value
        })
        if (-not $script:RuntimeExpandChanged) {
            break
        }
    }

    return $expanded
}

function Read-RuntimeEnv {
    param([string]$Path)

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        $equals = $trimmed.IndexOf("=")
        if ($equals -lt 1) {
            continue
        }
        $name = $trimmed.Substring(0, $equals).Trim()
        $value = $trimmed.Substring($equals + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$name] = Expand-RuntimeValue -Value $value -Values $values
    }
    return $values
}

function Get-RuntimeValue {
    param(
        [hashtable]$Values,
        [string[]]$Names,
        [string]$Default = ""
    )

    foreach ($name in $Names) {
        if ($Values.ContainsKey($name) -and -not [string]::IsNullOrWhiteSpace([string]$Values[$name])) {
            return [string]$Values[$name]
        }
    }
    return $Default
}

function Join-EndpointPath {
    param(
        [string]$BaseUrl,
        [string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        return ""
    }
    return $BaseUrl.TrimEnd("/") + "/" + $Path.TrimStart("/")
}

function Test-HttpEndpoint {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSec = 3
    )

    if ([string]::IsNullOrWhiteSpace($Url)) {
        Write-Host "[WARN] $Name endpoint is not configured."
        return $false
    }

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        Write-Host "[ OK ] $Name endpoint reachable: $Url ($($response.StatusCode))"
        return $true
    }
    catch {
        Write-Host "[MISS] $Name endpoint not reachable: $Url"
        Write-Host "       $($_.Exception.Message)"
        return $false
    }
}

function Wait-HttpEndpoint {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSec = 180
    )

    if ([string]::IsNullOrWhiteSpace($Url)) {
        return $false
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host "[ OK ] $Name endpoint became reachable: $Url ($($response.StatusCode))"
                return $true
            }
        }
        catch {
            Start-Sleep -Seconds 3
        }
    }

    Write-Host "[WARN] $Name endpoint did not become reachable within $TimeoutSec seconds: $Url"
    return $false
}

function Start-ConfiguredCommand {
    param(
        [string]$Name,
        [string]$Command
    )

    if ([string]::IsNullOrWhiteSpace($Command)) {
        Write-Host "[INFO] No startup command configured for $Name."
        return
    }

    Write-Host "[RUN ] Starting configured dependency: $Name"
    $encodedCommand = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($Command))
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encodedCommand) `
        -WorkingDirectory $WorkspaceRoot `
        -WindowStyle Hidden | Out-Null
}

function Test-UiHealth {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSec = 2
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri (Join-EndpointPath $BaseUrl "/api/health") -TimeoutSec $TimeoutSec
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Test-UiCompatibility {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSec = 2
    )

    try {
        $projects = Invoke-WebRequest -UseBasicParsing -Uri (Join-EndpointPath $BaseUrl "/api/projects") -TimeoutSec $TimeoutSec
        return ($projects.StatusCode -ge 200 -and $projects.StatusCode -lt 500)
    }
    catch {
        return $false
    }
}

function Stop-UiListener {
    param([int]$PortValue)

    $listeners = @(Get-NetTCPConnection -LocalPort $PortValue -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $owningProcessId = [int]$listener.OwningProcess
        if ($owningProcessId -le 0 -or $owningProcessId -eq $PID) {
            continue
        }
        try {
            $process = Get-Process -Id $owningProcessId -ErrorAction Stop
            Write-Host "[RUN ] Stopping incompatible Runtime UI process: pid=$owningProcessId ($($process.ProcessName))"
            Stop-Process -Id $owningProcessId -Force
        }
        catch {
            Write-Host "[WARN] Could not stop process using port ${PortValue}: pid=$owningProcessId"
        }
    }
    for ($i = 0; $i -lt 20; $i++) {
        $stillListening = @(Get-NetTCPConnection -LocalPort $PortValue -State Listen -ErrorAction SilentlyContinue)
        if ($stillListening.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
}

function Start-RuntimeUi {
    param(
        [string]$RuntimeEnvPath,
        [string]$HostValue,
        [int]$PortValue
    )

    $baseUrl = "http://${HostValue}:${PortValue}"
    if (Test-UiHealth -BaseUrl $baseUrl) {
        if (Test-UiCompatibility -BaseUrl $baseUrl) {
            Write-Host "[ OK ] Runtime UI is already running: $baseUrl"
            return $baseUrl
        }
        Write-Host "[WARN] A Runtime UI is listening on $baseUrl, but it is missing required APIs. Restarting it."
        Stop-UiListener -PortValue $PortValue
        Start-Sleep -Seconds 1
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python was not found in PATH."
    }

    $uiEntry = Join-Path $ToolRoot "runtime-ui.py"
    if (-not (Test-Path -LiteralPath $uiEntry)) {
        throw "Runtime UI entry not found: $uiEntry"
    }

    Write-Host "[RUN ] Starting Runtime UI: $baseUrl"
    Start-Process -FilePath $python.Source `
        -ArgumentList @("-B", $uiEntry, "--runtime-env", $RuntimeEnvPath, "--host", $HostValue, "--port", [string]$PortValue) `
        -WorkingDirectory $WorkspaceRoot `
        -WindowStyle Hidden | Out-Null

    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if ((Test-UiHealth -BaseUrl $baseUrl) -and (Test-UiCompatibility -BaseUrl $baseUrl)) {
            Write-Host "[ OK ] Runtime UI started: $baseUrl"
            return $baseUrl
        }
    }

    throw "Runtime UI did not become healthy within 15 seconds."
}

$envValues = Read-RuntimeEnv -Path $RuntimeEnv

Write-Host "Codex Skill Runtime startup"
Write-Host "Workspace : $WorkspaceRoot"
Write-Host "Tool root : $ToolRoot"
Write-Host "Env file  : $RuntimeEnv"
Write-Host ""

$codexExe = Get-RuntimeValue -Values $envValues -Names @("SKILL_RUNTIME_CODEX_EXECUTABLE", "CODEX_EXECUTABLE") -Default "codex"
$codexCommand = Get-Command $codexExe -ErrorAction SilentlyContinue
if ($codexCommand) {
    Write-Host "[ OK ] Codex CLI: $($codexCommand.Source)"
}
else {
    Write-Host "[MISS] Codex CLI not found: $codexExe"
}

$apiKeyFile = Get-RuntimeValue -Values $envValues -Names @("CODEX_API_KEY_FILE") -Default ""
if (-not [string]::IsNullOrWhiteSpace($apiKeyFile) -and (Test-Path -LiteralPath $apiKeyFile)) {
    Write-Host "[ OK ] Codex API key file exists: $apiKeyFile"
}
elseif (-not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("OPENAI_API_KEY"))) {
    Write-Host "[ OK ] OPENAI_API_KEY exists in current environment."
}
else {
    Write-Host "[MISS] Codex API key not found. Configure CODEX_API_KEY_FILE or OPENAI_API_KEY."
}

$godotExe = Get-RuntimeValue -Values $envValues -Names @("SKILL_RUNTIME_ENV_GODOT_EXE", "GODOT_EXE") -Default ""
if (-not [string]::IsNullOrWhiteSpace($godotExe) -and (Test-Path -LiteralPath $godotExe)) {
    Write-Host "[ OK ] Godot executable exists: $godotExe"
}
elseif (-not [string]::IsNullOrWhiteSpace($godotExe)) {
    Write-Host "[MISS] Godot executable not found: $godotExe"
}
else {
    Write-Host "[WARN] Godot executable is not configured."
}

Write-Host ""
$forgeBase = Get-RuntimeValue -Values $envValues -Names @("SKILL_RUNTIME_ENV_FORGE_BASE_URL", "SKILL_RUNTIME_CAPABILITY_FORGE_ENDPOINT") -Default ""
$comfyBase = Get-RuntimeValue -Values $envValues -Names @("SKILL_RUNTIME_ENV_COMFYUI_BASE_URL", "SKILL_RUNTIME_CAPABILITY_COMFYUI_ENDPOINT") -Default ""

$forgeOk = Test-HttpEndpoint -Name "Forge/A1111" -Url (Join-EndpointPath $forgeBase "/sdapi/v1/options")
$comfyOk = Test-HttpEndpoint -Name "ComfyUI" -Url (Join-EndpointPath $comfyBase "/system_stats")

if ((-not $NoStartConfiguredDeps) -and (-not $CheckOnly)) {
    $startedForge = $false
    $startedComfy = $false
    if (-not $forgeOk) {
        $forgeCommand = Get-RuntimeValue -Values $envValues -Names @("SKILL_RUNTIME_START_FORGE_CMD") -Default ""
        if (-not [string]::IsNullOrWhiteSpace($forgeCommand)) {
            Start-ConfiguredCommand -Name "Forge/A1111" -Command $forgeCommand
            $startedForge = $true
        }
        else {
            Start-ConfiguredCommand -Name "Forge/A1111" -Command $forgeCommand
        }
    }
    if (-not $comfyOk) {
        $comfyCommand = Get-RuntimeValue -Values $envValues -Names @("SKILL_RUNTIME_START_COMFYUI_CMD") -Default ""
        if (-not [string]::IsNullOrWhiteSpace($comfyCommand)) {
            Start-ConfiguredCommand -Name "ComfyUI" -Command $comfyCommand
            $startedComfy = $true
        }
        else {
            Start-ConfiguredCommand -Name "ComfyUI" -Command $comfyCommand
        }
    }
    if ($startedForge) {
        $forgeOk = Wait-HttpEndpoint -Name "Forge/A1111" -Url (Join-EndpointPath $forgeBase "/sdapi/v1/options")
    }
    if ($startedComfy) {
        $comfyOk = Wait-HttpEndpoint -Name "ComfyUI" -Url (Join-EndpointPath $comfyBase "/system_stats")
    }
}
elseif ($CheckOnly) {
    Write-Host "[INFO] Check-only mode will not start configured dependencies."
}

Write-Host ""
if ($CheckOnly) {
    Write-Host "[DONE] Check-only mode finished."
    exit 0
}

if ($NoStartUi) {
    Write-Host "[DONE] UI startup skipped by -NoStartUi."
    exit 0
}

$uiUrl = Start-RuntimeUi -RuntimeEnvPath $RuntimeEnv -HostValue $HostAddress -PortValue $Port

if (-not $NoBrowser) {
    Start-Process $uiUrl | Out-Null
}

Write-Host ""
Write-Host "[DONE] Runtime UI is ready: $uiUrl"
Write-Host "       Use Ctrl+C only if you started it in foreground. This launcher starts it as a background process."
