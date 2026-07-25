[CmdletBinding()]
param(
    [string]$ModelPath = 'C:\source\Qwen3-0.6B-Instruct',
    [int]$Port = 8765,
    [switch]$KeepBuildArtifacts
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$source = Join-Path $repoRoot 'examples\llm_paris_client.pc'
$work = Join-Path $env:TEMP 'picoscript-llm-paris'
$executable = Join-Path $work 'llm_paris_client.exe'
$serverLog = Join-Path $work 'model-server.log'
$serverError = Join-Path $work 'model-server.error.log'
$healthUrl = "http://127.0.0.1:$Port/health"
$startedServer = $null

function Test-ModelHealth {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

New-Item -ItemType Directory -Force -Path $work | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $ModelPath 'model.safetensors'))) {
    throw "Qwen3 instruct checkpoint is missing: $ModelPath"
}

try {
    if (-not (Test-ModelHealth)) {
        $transformers = Get-Command transformers -ErrorAction Stop
        $startedServer = Start-Process `
            -FilePath $transformers.Source `
            -ArgumentList @(
                'serve', $ModelPath,
                '--device', 'cpu',
                '--reasoning', 'off',
                '--host', '127.0.0.1',
                '--port', $Port,
                '--log-level', 'warning'
            ) `
            -RedirectStandardOutput $serverLog `
            -RedirectStandardError $serverError `
            -PassThru

        $deadline = (Get-Date).AddMinutes(5)
        while (-not (Test-ModelHealth)) {
            if ($startedServer.HasExited) {
                throw "Model server exited early. See $serverError"
            }
            if ((Get-Date) -ge $deadline) {
                throw "Model server did not become healthy within five minutes"
            }
            Start-Sleep -Seconds 3
        }
    }

    Push-Location $repoRoot
    try {
        & python picoscript_build.py native $source --provider net -o $executable
        if ($LASTEXITCODE -ne 0) {
            throw "PicoScript client build failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    $lines = & $executable 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "PicoScript client failed with exit code $LASTEXITCODE"
    }
    $outLine = $lines | Where-Object { $_ -like 'OUT *' } | Select-Object -Last 1
    if (-not $outLine) {
        throw 'PicoScript output did not contain an OUT byte record'
    }

    $bytes = [byte[]](($outLine.Substring(4) -split ' ') |
        Where-Object { $_ } |
        ForEach-Object { [Convert]::ToByte($_, 16) })
    $http = [Text.Encoding]::UTF8.GetString($bytes)
    if (-not $http.StartsWith('HTTP/1.1 200')) {
        throw "Model request failed:`n$http"
    }
    $parts = $http -split "`r`n`r`n", 2
    if ($parts.Count -ne 2) {
        throw 'Model response did not contain an HTTP body'
    }
    $response = $parts[1] | ConvertFrom-Json
    $answer = [string]$response.choices[0].message.content
    if ($answer -notmatch '(?i)\bParis\b' -or $answer -notmatch '(?i)Eiffel Tower') {
        throw "Model response failed the factual sanity check: $answer"
    }

    Write-Host 'PicoScript LLM demonstration passed'
    Write-Host "  model:  Qwen/Qwen3-0.6B"
    Write-Host "  prompt: What is the capital of France? Include one well-known landmark."
    Write-Host "  answer: $answer"
}
finally {
    if ($startedServer -and -not $startedServer.HasExited) {
        Stop-Process -Id $startedServer.Id
        $startedServer.WaitForExit()
    }
    if (-not $KeepBuildArtifacts) {
        foreach ($path in $executable, ($executable + '.c')) {
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Force
            }
        }
    }
}
