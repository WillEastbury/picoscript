[CmdletBinding()]
param(
    [string]$ModelDirectory = 'C:\source\Qwen3-0.6B-Base',
    [string]$WorkDirectory,
    [int]$Epochs = 2,
    [int]$CalibrationRows = 4,
    [int]$BatchSize = 1,
    [int]$GroupSize = 128,
    [double]$LearningRate = 0.001,
    [int]$Seed = 260626650,
    [switch]$Cpu
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path $ModelDirectory 'catq-full'
}
$modelFile = Join-Path $ModelDirectory 'model.safetensors'
$calibrationFile = Join-Path $WorkDirectory 'calibration.safetensors'
$manifestFile = Join-Path $WorkDirectory 'activations.tsv'
$planFile = Join-Path $WorkDirectory 'qwen06-full-catq.pc'
$plannerExe = Join-Path $WorkDirectory 'catq-plan.exe'
$runnerExe = Join-Path $WorkDirectory 'qwen06-full-catq.exe'
$shardDirectory = Join-Path $WorkDirectory 'shards'

function Read-SafeTensorHeader([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try {
        $reader = [IO.BinaryReader]::new($stream, [Text.Encoding]::UTF8, $true)
        $length = $reader.ReadUInt64()
        $json = [Text.Encoding]::UTF8.GetString(
            $reader.ReadBytes([int]$length)
        ).TrimEnd()
        return $json | ConvertFrom-Json -AsHashtable
    }
    finally {
        $stream.Dispose()
    }
}

function Is-Quantizable([string]$Name, [hashtable]$Info) {
    if (-not $Name.StartsWith('model.layers.') -or -not $Name.EndsWith('.weight')) {
        return $false
    }
    if ($Name.Contains('layernorm') -or $Name.Contains('.q_norm.') -or
        $Name.Contains('.k_norm.') -or $Name.Contains('.bias')) {
        return $false
    }
    return @($Info['shape']).Count -eq 2
}

function PicoPath([string]$Path) {
    return ([IO.Path]::GetFullPath($Path)).Replace('\', '/')
}

if (-not (Test-Path -LiteralPath $modelFile)) {
    throw "Model checkpoint is missing: $modelFile"
}
if ($Epochs -lt 1 -or $CalibrationRows -lt 1 -or $BatchSize -lt 1 -or $GroupSize -lt 1) {
    throw 'Epochs, CalibrationRows, BatchSize, and GroupSize must be positive'
}

New-Item -ItemType Directory -Force -Path $WorkDirectory, $shardDirectory | Out-Null
Get-ChildItem -LiteralPath $shardDirectory -Filter '*.safetensors' -File `
    -ErrorAction SilentlyContinue | Remove-Item -Force
$header = Read-SafeTensorHeader $modelFile
$matrices = [Collections.Generic.List[object]]::new()
$dimensions = [Collections.Generic.SortedSet[int]]::new()
foreach ($entry in $header.GetEnumerator()) {
    if ($entry.Key -eq '__metadata__' -or
        -not (Is-Quantizable $entry.Key $entry.Value)) {
        continue
    }
    $shape = @($entry.Value['shape'])
    $inputDimension = [int]$shape[-1]
    $dimensions.Add($inputDimension) | Out-Null
    $safeName = ($entry.Key -replace '[^A-Za-z0-9_.-]', '_').Replace('.', '_')
    $matrices.Add([pscustomobject]@{
        Name = $entry.Key
        InputDimension = $inputDimension
        Output = Join-Path $shardDirectory ($safeName + '.ternary.safetensors')
    })
}
if ($matrices.Count -eq 0) {
    throw 'No quantizable Qwen matrices were found'
}

$dataStream = [IO.MemoryStream]::new()
$dataWriter = [IO.BinaryWriter]::new($dataStream, [Text.Encoding]::UTF8, $true)
$calibrationHeader = [ordered]@{}
foreach ($dimension in $dimensions) {
    $start = $dataStream.Position
    $random = [Random]::new($Seed + $dimension)
    for ($i = 0; $i -lt ($CalibrationRows * $dimension); $i++) {
        $dataWriter.Write([single](($random.NextDouble() * 2.0 - 1.0) * 0.5))
    }
    $dataWriter.Flush()
    $calibrationHeader["cal_$dimension"] = [ordered]@{
        dtype = 'F32'
        shape = @($CalibrationRows, $dimension)
        data_offsets = @([long]$start, [long]$dataStream.Position)
    }
}
$calibrationData = $dataStream.ToArray()
$dataWriter.Dispose()
$dataStream.Dispose()
$headerBytes = [Text.Encoding]::UTF8.GetBytes(
    ($calibrationHeader | ConvertTo-Json -Compress -Depth 6)
)
$padding = (8 - ($headerBytes.Length % 8)) % 8
if ($padding) {
    $padded = [byte[]]::new($headerBytes.Length + $padding)
    [Array]::Copy($headerBytes, $padded, $headerBytes.Length)
    for ($i = $headerBytes.Length; $i -lt $padded.Length; $i++) {
        $padded[$i] = 0x20
    }
    $headerBytes = $padded
}
$stream = [IO.File]::Create($calibrationFile)
try {
    $writer = [IO.BinaryWriter]::new($stream, [Text.Encoding]::UTF8, $true)
    $writer.Write([uint64]$headerBytes.Length)
    $writer.Write($headerBytes)
    $writer.Write($calibrationData)
    $writer.Flush()
}
finally {
    $stream.Dispose()
}

$calibrationPath = PicoPath $calibrationFile
$lines = foreach ($matrix in $matrices) {
    @(
        $matrix.Name
        $calibrationPath
        "cal_$($matrix.InputDimension)"
        (PicoPath $matrix.Output)
    ) -join "`t"
}
[IO.File]::WriteAllLines($manifestFile, $lines)

$python = (Get-Command python -ErrorAction Stop).Source
$options = "group=$GroupSize;epochs=$Epochs;batch=$BatchSize;gamma=0.8;s0=30;lr=$LearningRate;threads=0"
if (-not $Cpu) {
    $options += ';device=cuda;cuda_required=1'
}
$provider = if ($Cpu) { 'catq' } else { 'catq-cuda' }

Push-Location $repoRoot
try {
    & $python -m ziglang cc -std=c99 -O2 tools\catq_plan.c -o $plannerExe
    if ($LASTEXITCODE -ne 0) { throw 'CAT-Q plan compiler build failed' }
    & $plannerExe qwen3 $ModelDirectory $manifestFile $planFile $options
    if ($LASTEXITCODE -ne 0) { throw 'CAT-Q plan generation failed' }
    & $python picoscript_build.py native $planFile `
        --provider $provider --profile host -o $runnerExe
    if ($LASTEXITCODE -ne 0) { throw 'Full CAT-Q executable build failed' }
}
finally {
    Pop-Location
}

$runOutput = @()
$elapsed = Measure-Command {
    $runOutput = & $runnerExe 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Full Qwen CAT-Q conversion failed' }
}

$outputs = @(Get-ChildItem -LiteralPath $shardDirectory -Filter '*.safetensors' -File)
if ($outputs.Count -ne $matrices.Count) {
    throw "Expected $($matrices.Count) output shards, found $($outputs.Count)"
}
$totalBytes = ($outputs | Measure-Object -Property Length -Sum).Sum
foreach ($output in $outputs) {
    $outputHeader = Read-SafeTensorHeader $output.FullName
    if ($outputHeader['__metadata__']['format'] -ne 'picoscript-catq-ternary-v1') {
        throw "Invalid CAT-Q output: $($output.FullName)"
    }
}

$runOutput | ForEach-Object { Write-Host $_ }
Write-Host ''
Write-Host 'Full Qwen3-0.6B CAT-Q conversion passed'
Write-Host "  backend:      $(if ($Cpu) { 'CPU' } else { 'CUDA' })"
Write-Host "  matrices:     $($matrices.Count)"
Write-Host "  input widths: $($dimensions -join ', ')"
Write-Host "  schedule:     $Epochs epochs, $CalibrationRows rows, batch $BatchSize"
Write-Host "  elapsed:      $([math]::Round($elapsed.TotalSeconds, 2)) s"
Write-Host "  ternary size: $([math]::Round($totalBytes / 1MB, 2)) MB"
Write-Host "  output dir:   $shardDirectory"
