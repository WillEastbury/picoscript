[CmdletBinding()]
param(
    [string]$ModelRepo = 'Qwen/Qwen3-0.6B-Base',
    [string]$ModelDirectory = 'C:\source\Qwen3-0.6B-Base',
    [string]$TensorName = 'model.layers.0.mlp.gate_proj.weight',
    [ValidateSet('qwen3', 'qwen3.5', 'gpt-oss')]
    [string]$Architecture = 'qwen3',
    [int]$CalibrationRows = 4,
    [int]$Epochs = 2,
    [int]$BatchSize = 1,
    [int]$GroupSize = 128,
    [int]$Threads = 0,
    [int]$CudaChunkWeights = 33554432,
    [double]$LearningRate = 0.001,
    [int]$Seed = 260626650,
    [string]$WorkDirectory,
    [switch]$SkipDownload,
    [switch]$Cuda,
    [switch]$KeepBuildArtifacts
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$modelFile = Join-Path $ModelDirectory 'model.safetensors'
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path $ModelDirectory 'catq-smoke'
}
$WorkDirectory = [IO.Path]::GetFullPath($WorkDirectory)
$calibrationFile = Join-Path $WorkDirectory 'calibration.safetensors'
$manifestFile = Join-Path $WorkDirectory 'activations.tsv'
$planFile = Join-Path $WorkDirectory 'catq-smoke.pc'
$plannerExe = Join-Path $WorkDirectory 'catq-plan.exe'
$runnerExe = Join-Path $WorkDirectory 'catq-smoke.exe'
$outputFile = Join-Path $WorkDirectory 'tensor.ternary.safetensors'

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}

function Get-SafeTensorHeader {
    param([Parameter(Mandatory)][string]$Path)

    $stream = [IO.File]::OpenRead($Path)
    try {
        $reader = [IO.BinaryReader]::new($stream, [Text.Encoding]::UTF8, $true)
        $headerLength = $reader.ReadUInt64()
        if ($headerLength -le 0 -or $headerLength -gt 64MB) {
            throw "Invalid safetensors header length: $headerLength"
        }
        $headerBytes = $reader.ReadBytes([int]$headerLength)
        if ($headerBytes.Length -ne [int]$headerLength) {
            throw 'Truncated safetensors header'
        }
        $json = [Text.Encoding]::UTF8.GetString($headerBytes).TrimEnd()
        [pscustomobject]@{
            HeaderLength = [long]$headerLength
            Header = $json | ConvertFrom-Json -AsHashtable
            DataOffset = 8L + [long]$headerLength
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Write-CalibrationTensor {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][int]$Rows,
        [Parameter(Mandatory)][int]$Columns,
        [Parameter(Mandatory)][int]$RandomSeed
    )

    $dataStream = [IO.MemoryStream]::new()
    try {
        $writer = [IO.BinaryWriter]::new($dataStream, [Text.Encoding]::UTF8, $true)
        $random = [Random]::new($RandomSeed)
        for ($i = 0; $i -lt ($Rows * $Columns); $i++) {
            # Bounded, deterministic synthetic activations for a wiring smoke test.
            $value = [single](($random.NextDouble() * 2.0 - 1.0) * 0.5)
            $writer.Write($value)
        }
        $writer.Flush()
        $data = $dataStream.ToArray()
    }
    finally {
        $dataStream.Dispose()
    }

    $headerObject = [ordered]@{
        calibration = [ordered]@{
            dtype = 'F32'
            shape = @($Rows, $Columns)
            data_offsets = @(0, $data.Length)
        }
    }
    $header = [Text.Encoding]::UTF8.GetBytes(
        ($headerObject | ConvertTo-Json -Compress -Depth 5)
    )
    $padding = (8 - ($header.Length % 8)) % 8
    if ($padding) {
        $padded = [byte[]]::new($header.Length + $padding)
        [Array]::Copy($header, $padded, $header.Length)
        for ($i = $header.Length; $i -lt $padded.Length; $i++) {
            $padded[$i] = 0x20
        }
        $header = $padded
    }

    $stream = [IO.File]::Create($Path)
    try {
        $writer = [IO.BinaryWriter]::new($stream, [Text.Encoding]::UTF8, $true)
        $writer.Write([uint64]$header.Length)
        $writer.Write($header)
        $writer.Write($data)
        $writer.Flush()
    }
    finally {
        $stream.Dispose()
    }
}

function Test-TernaryOutput {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][long]$ExpectedValues,
        [Parameter(Mandatory)][string]$ExpectedShape,
        [Parameter(Mandatory)][int]$ExpectedGroupSize
    )

    $document = Get-SafeTensorHeader $Path
    $metadata = $document.Header['__metadata__']
    if (-not $metadata -or $metadata['format'] -ne 'picoscript-catq-ternary-v1') {
        throw 'Output is not a PicoScript CAT-Q ternary shard'
    }
    if ($metadata['shape'] -ne $ExpectedShape) {
        throw "Output shape mismatch: $($metadata['shape']) != $ExpectedShape"
    }
    if ([int]$metadata['group_size'] -ne $ExpectedGroupSize) {
        throw "Output group size mismatch: $($metadata['group_size'])"
    }

    $codesInfo = $document.Header['codes']
    $scalesInfo = $document.Header['scales']
    $codesStart = [long]$codesInfo['data_offsets'][0]
    $codesEnd = [long]$codesInfo['data_offsets'][1]
    $scalesStart = [long]$scalesInfo['data_offsets'][0]
    $scalesEnd = [long]$scalesInfo['data_offsets'][1]

    $stream = [IO.File]::OpenRead($Path)
    try {
        $reader = [IO.BinaryReader]::new($stream, [Text.Encoding]::UTF8, $true)
        $stream.Position = $document.DataOffset + $codesStart
        $codes = $reader.ReadBytes([int]($codesEnd - $codesStart))
        $counts = [long[]]::new(4)
        foreach ($packed in $codes) {
            foreach ($shift in 0, 2, 4, 6) {
                $counts[($packed -shr $shift) -band 3]++
            }
        }
        $paddingValues = ($codes.Length * 4L) - $ExpectedValues
        if ($paddingValues -gt 0) {
            $counts[0] -= $paddingValues
        }
        if ($counts[3] -ne 0) {
            throw "Output contains $($counts[3]) invalid ternary code(s)"
        }
        if (($counts[0] + $counts[1] + $counts[2]) -ne $ExpectedValues) {
            throw 'Packed ternary value count does not match the source tensor'
        }

        $stream.Position = $document.DataOffset + $scalesStart
        $scaleCount = [int](($scalesEnd - $scalesStart) / 4)
        $minScale = [single]::PositiveInfinity
        $maxScale = [single]::NegativeInfinity
        for ($i = 0; $i -lt $scaleCount; $i++) {
            $scale = $reader.ReadSingle()
            if ([single]::IsNaN($scale) -or [single]::IsInfinity($scale) -or $scale -le 0) {
                throw "Invalid scale at index $i`: $scale"
            }
            if ($scale -lt $minScale) { $minScale = $scale }
            if ($scale -gt $maxScale) { $maxScale = $scale }
        }

        [pscustomobject]@{
            Zero = $counts[0]
            Positive = $counts[1]
            Negative = $counts[2]
            ScaleCount = $scaleCount
            MinScale = $minScale
            MaxScale = $maxScale
        }
    }
    finally {
        $stream.Dispose()
    }
}

if ($CalibrationRows -lt 1 -or $Epochs -lt 1 -or $BatchSize -lt 1 -or
    $GroupSize -lt 1 -or $Threads -lt 0 -or $CudaChunkWeights -lt 1) {
    throw 'CalibrationRows, Epochs, BatchSize, GroupSize, and CudaChunkWeights must be positive; Threads cannot be negative'
}

New-Item -ItemType Directory -Force -Path $ModelDirectory, $WorkDirectory | Out-Null

if (-not (Test-Path -LiteralPath $modelFile)) {
    if ($SkipDownload) {
        throw "Model checkpoint is missing: $modelFile"
    }
    $hf = Get-Command hf -ErrorAction SilentlyContinue
    if (-not $hf) {
        throw 'The Hugging Face `hf` CLI is required to download the smoke-test model'
    }
    $previousEncoding = $env:PYTHONIOENCODING
    try {
        $env:PYTHONIOENCODING = 'utf-8'
        Invoke-Checked $hf.Source @(
            'download', $ModelRepo,
            'model.safetensors', 'config.json',
            '--local-dir', $ModelDirectory
        )
    }
    finally {
        $env:PYTHONIOENCODING = $previousEncoding
    }
}

$modelDocument = Get-SafeTensorHeader $modelFile
$tensor = $modelDocument.Header[$TensorName]
if (-not $tensor) {
    throw "Tensor was not found in the checkpoint: $TensorName"
}
$shape = @($tensor['shape'] | ForEach-Object { [long]$_ })
if ($shape.Count -lt 2) {
    throw 'The smoke-test tensor must be a matrix'
}
$inputColumns = [int]$shape[-1]
$valueCount = [long]1
foreach ($dimension in $shape) { $valueCount *= $dimension }
$shapeText = ($shape -join ',')
$sourceBytesPerValue = switch ($tensor['dtype']) {
    'BF16' { 2 }
    'F16' { 2 }
    'F32' { 4 }
    default { throw "Unsupported source dtype: $($tensor['dtype'])" }
}

Write-CalibrationTensor $calibrationFile $CalibrationRows $inputColumns $Seed

$outputPicoPath = $outputFile.Replace('\', '/')
$calibrationPicoPath = $calibrationFile.Replace('\', '/')
$manifestLine = @(
    $TensorName
    $calibrationPicoPath
    'calibration'
    $outputPicoPath
) -join "`t"
[IO.File]::WriteAllText($manifestFile, $manifestLine + [Environment]::NewLine)

$python = (Get-Command python -ErrorAction Stop).Source
$plannerSource = Join-Path $repoRoot 'tools\catq_plan.c'
$buildDriver = Join-Path $repoRoot 'picoscript_build.py'
$options = "group=$GroupSize;epochs=$Epochs;batch=$BatchSize;gamma=0.8;s0=30;lr=$LearningRate;threads=$Threads"
if ($Cuda) {
    $options += ";device=cuda;cuda_required=1;cuda_chunk_weights=$CudaChunkWeights"
}
$provider = if ($Cuda) { 'catq-cuda' } else { 'catq' }

Push-Location $repoRoot
try {
    Invoke-Checked $python @(
        '-m', 'ziglang', 'cc', '-std=c99', '-O2',
        $plannerSource, '-o', $plannerExe
    )
    Invoke-Checked $plannerExe @(
        $Architecture, $ModelDirectory, $manifestFile, $planFile, $options
    )
    Invoke-Checked $python @(
        $buildDriver, 'native', $planFile,
        '--provider', $provider, '--profile', 'host', '-o', $runnerExe
    )

    $runnerOutput = @()
    $elapsed = Measure-Command {
        $runnerOutput = & $runnerExe 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "CAT-Q runner failed with exit code $LASTEXITCODE"
        }
    }
}
finally {
    Pop-Location
}

$result = Test-TernaryOutput `
    -Path $outputFile `
    -ExpectedValues $valueCount `
    -ExpectedShape $shapeText `
    -ExpectedGroupSize $GroupSize

$originalTensorBytes = $valueCount * $sourceBytesPerValue
$outputBytes = (Get-Item -LiteralPath $outputFile).Length
$ratio = if ($outputBytes) { $originalTensorBytes / $outputBytes } else { 0 }

$runnerOutput | ForEach-Object { Write-Host $_ }
Write-Host ''
Write-Host 'CAT-Q smoke test passed'
Write-Host "  model:       $ModelRepo"
Write-Host "  tensor:      $TensorName [$shapeText] $($tensor['dtype'])"
Write-Host "  epochs:      $Epochs"
Write-Host "  threads:     $(if ($Threads -eq 0) { 'auto' } else { $Threads })"
Write-Host "  backend:     $(if ($Cuda) { 'CUDA' } else { 'CPU' })"
Write-Host "  elapsed:     $([math]::Round($elapsed.TotalSeconds, 2)) s"
Write-Host "  output:      $outputFile"
Write-Host "  output size: $([math]::Round($outputBytes / 1MB, 2)) MB"
Write-Host "  compression: $([math]::Round($ratio, 2))x (tensor payload vs CAT-Q shard)"
Write-Host "  codes:       -1=$($result.Negative), 0=$($result.Zero), +1=$($result.Positive)"
Write-Host "  scales:      $($result.ScaleCount), range [$($result.MinScale), $($result.MaxScale)]"

if (-not $KeepBuildArtifacts) {
    foreach ($path in $plannerExe, $runnerExe, ($runnerExe + '.c')) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
}
