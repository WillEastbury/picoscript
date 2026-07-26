[CmdletBinding()]
param(
    [string]$ModelDirectory = 'C:\source\developercli\models\Qwen3.6-35B-A3B',
    [string]$WorkDirectory,
    [int]$Epochs = 2,
    [int]$CalibrationRows = 4,
    [int]$BatchSize = 1,
    [int]$GroupSize = 128,
    [double]$LearningRate = 0.001,
    [int]$Seed = 260635003,
    [int]$MatricesPerBatch = 10,
    [int]$CudaChunkWeights = 33554432,
    [switch]$Resume,
    [switch]$Cpu
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path $ModelDirectory 'catq-full'
}
$indexFile = Join-Path $ModelDirectory 'model.safetensors.index.json'
$calibrationFile = Join-Path $WorkDirectory 'calibration.safetensors'
$manifestFile = Join-Path $WorkDirectory 'activations.tsv'
$plannerExe = Join-Path $WorkDirectory 'catq-plan.exe'
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
    if (-not $Name.StartsWith('model.language_model.layers.')) {
        return $false
    }
    if ($Name.Contains('layernorm') -or $Name.Contains('.norm.') -or
        $Name.Contains('.mlp.gate.weight') -or
        $Name.Contains('shared_expert_gate') -or
        $Name.Contains('.bias') -or $Name.Contains('.conv1d.') -or
        $Name.EndsWith('.A_log') -or $Name.EndsWith('.dt_bias')) {
        return $false
    }
    return @($Info['shape']).Count -ge 2
}

function PicoPath([string]$Path) {
    return ([IO.Path]::GetFullPath($Path)).Replace('\', '/')
}

if (-not (Test-Path -LiteralPath $indexFile)) {
    throw "Checkpoint index is missing: $indexFile"
}
if ($Epochs -lt 1 -or $CalibrationRows -lt 1 -or $BatchSize -lt 1 -or
    $GroupSize -lt 1 -or $MatricesPerBatch -lt 1 -or $CudaChunkWeights -lt 1) {
    throw 'Epochs, CalibrationRows, BatchSize, GroupSize, MatricesPerBatch, and CudaChunkWeights must be positive'
}

New-Item -ItemType Directory -Force -Path $WorkDirectory, $shardDirectory | Out-Null
if (-not $Resume) {
    Get-ChildItem -LiteralPath $shardDirectory -Filter '*.safetensors' -File `
        -ErrorAction SilentlyContinue | Remove-Item -Force
}

$index = Get-Content -LiteralPath $indexFile -Raw | ConvertFrom-Json -AsHashtable
$headers = @{}
$matrices = [Collections.Generic.List[object]]::new()
$dimensions = [Collections.Generic.SortedSet[int]]::new()
foreach ($weight in ($index['weight_map'].GetEnumerator() | Sort-Object Key)) {
    $sourceShard = [string]$weight.Value
    if (-not $headers.ContainsKey($sourceShard)) {
        $headers[$sourceShard] = Read-SafeTensorHeader (Join-Path $ModelDirectory $sourceShard)
    }
    $info = $headers[$sourceShard][$weight.Key]
    if (-not $info -or -not (Is-Quantizable $weight.Key $info)) {
        continue
    }
    $shape = @($info['shape'])
    $inputDimension = [int]$shape[-1]
    $dimensions.Add($inputDimension) | Out-Null
    $safeName = ($weight.Key -replace '[^A-Za-z0-9_.-]', '_').Replace('.', '_')
    $matrices.Add([pscustomobject]@{
        Name = $weight.Key
        InputDimension = $inputDimension
        Output = Join-Path $shardDirectory ($safeName + '.ternary.safetensors')
    })
}
if ($matrices.Count -eq 0) {
    throw 'No quantizable Qwen3.5 matrices were found'
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
$pendingMatrices = if ($Resume) {
    @($matrices | Where-Object { -not (Test-Path -LiteralPath $_.Output) })
}
else {
    @($matrices)
}
$pendingLines = foreach ($matrix in $pendingMatrices) {
    @(
        $matrix.Name
        $calibrationPath
        "cal_$($matrix.InputDimension)"
        (PicoPath $matrix.Output)
    ) -join "`t"
}

$python = (Get-Command python -ErrorAction Stop).Source
$options = "group=$GroupSize;epochs=$Epochs;batch=$BatchSize;gamma=0.8;s0=30;lr=$LearningRate;threads=0"
if (-not $Cpu) {
    $options += ";device=cuda;cuda_required=1;cuda_chunk_weights=$CudaChunkWeights"
}
$provider = if ($Cpu) { 'catq' } else { 'catq-cuda' }

Push-Location $repoRoot
try {
    & $python -m ziglang cc -std=c99 -O2 tools\catq_plan.c -o $plannerExe
    if ($LASTEXITCODE -ne 0) { throw 'CAT-Q plan compiler build failed' }
}
finally {
    Pop-Location
}

$runOutput = [Collections.Generic.List[string]]::new()
$executionSeconds = 0.0
$batchCount = [int][Math]::Ceiling($pendingMatrices.Count / [double]$MatricesPerBatch)
for ($batch = 0; $batch -lt $batchCount; $batch++) {
    $start = $batch * $MatricesPerBatch
    $count = [Math]::Min($MatricesPerBatch, $pendingMatrices.Count - $start)
    $batchManifest = Join-Path $WorkDirectory ("batch-{0:D3}.tsv" -f $batch)
    $batchPlan = Join-Path $WorkDirectory ("batch-{0:D3}.pc" -f $batch)
    $batchRunner = Join-Path $WorkDirectory ("batch-{0:D3}.exe" -f $batch)
    [IO.File]::WriteAllLines($batchManifest, $pendingLines[$start..($start + $count - 1)])

    Push-Location $repoRoot
    try {
        & $plannerExe qwen3.5 $ModelDirectory $batchManifest $batchPlan $options
        if ($LASTEXITCODE -ne 0) { throw "CAT-Q plan generation failed for batch $batch" }
        & $python picoscript_build.py native $batchPlan `
            --provider $provider --profile host -o $batchRunner
        if ($LASTEXITCODE -ne 0) { throw "CAT-Q executable build failed for batch $batch" }
    }
    finally {
        Pop-Location
    }
    $batchOutput = @()
    $batchElapsed = Measure-Command {
        $batchOutput = & $batchRunner 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Qwen3.5 CAT-Q batch $batch failed" }
    }
    $executionSeconds += $batchElapsed.TotalSeconds
    foreach ($line in $batchOutput) { $runOutput.Add([string]$line) }
    Write-Host "Completed batch $($batch + 1)/$batchCount ($count tensors) in $([math]::Round($batchElapsed.TotalSeconds, 2)) s"
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
Write-Host 'Full Qwen3.5-35B-A3B CAT-Q conversion passed'
Write-Host "  backend:      $(if ($Cpu) { 'CPU' } else { 'CUDA' })"
Write-Host "  tensors:      $($matrices.Count)"
Write-Host "  converted:    $($pendingMatrices.Count)"
Write-Host "  input widths: $($dimensions -join ', ')"
Write-Host "  schedule:     $Epochs epochs, $CalibrationRows rows, batch $BatchSize"
Write-Host "  plan batches: $batchCount x <= $MatricesPerBatch matrices"
if (-not $Cpu) {
    Write-Host "  CUDA chunk:   $CudaChunkWeights weights"
}
Write-Host "  execution:    $([math]::Round($executionSeconds, 2)) s"
Write-Host "  ternary size: $([math]::Round($totalBytes / 1GB, 2)) GB"
Write-Host "  output dir:   $shardDirectory"
