[CmdletBinding()]
param(
    [string]$ModelDirectory = 'C:\source\developercli\models\Qwen3.6-35B-A3B',
    [string]$WorkDirectory,
    [int]$Epochs = 2,
    [int]$CalibrationRows = 4,
    [string]$CalibrationFile,
    [int]$BatchSize = 1,
    [int]$GroupSize = 128,
    [double]$LearningRate = 0.001,
    [int]$Seed = 260635003,
    [int]$MatricesPerBatch = 10,
    [int]$CudaChunkWeights = 33554432,
    [switch]$Resume,
    [switch]$Cpu,
    [string]$Layers,
    [int]$MaxMatrices = 0,
    [ValidateSet('All', 'Moe', 'Dense')]
    [string]$MatrixSet = 'All',
    [ValidateSet('All', 'GateUp', 'Down')]
    [string]$MatrixStage = 'All',
    [switch]$RequireTargets
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path $ModelDirectory 'catq-full'
}
$indexFile = Join-Path $ModelDirectory 'model.safetensors.index.json'
$syntheticCalibrationFile = Join-Path $WorkDirectory 'calibration.safetensors'
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

function Parse-Layers([string]$Text) {
    $result = [Collections.Generic.SortedSet[int]]::new()
    foreach ($item in $Text.Split(',')) {
        $match = [regex]::Match($item, '^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$')
        if (-not $match.Success) {
            throw "Invalid layer or range: '$item'"
        }
        $first = [int]$match.Groups[1].Value
        $last = if ($match.Groups[2].Success) {
            [int]$match.Groups[2].Value
        }
        else {
            $first
        }
        if ($last -lt $first) {
            throw "Invalid descending layer range: '$item'"
        }
        for ($layer = $first; $layer -le $last; $layer++) {
            $result.Add($layer) | Out-Null
        }
    }
    if ($result.Count -eq 0) {
        throw 'Layer selection must not be empty'
    }
    return $result
}

function PicoPath([string]$Path) {
    return ([IO.Path]::GetFullPath($Path)).Replace('\', '/')
}

if (-not (Test-Path -LiteralPath $indexFile)) {
    throw "Checkpoint index is missing: $indexFile"
}
if ($Epochs -lt 1 -or $BatchSize -lt 1 -or
    $GroupSize -lt 1 -or $MatricesPerBatch -lt 1 -or $CudaChunkWeights -lt 1) {
    throw 'Epochs, BatchSize, GroupSize, MatricesPerBatch, and CudaChunkWeights must be positive'
}
if (-not $CalibrationFile -and $CalibrationRows -lt 1) {
    throw 'CalibrationRows must be positive when synthetic calibration is used'
}
if ($MaxMatrices -lt 0) {
    throw 'MaxMatrices cannot be negative'
}

New-Item -ItemType Directory -Force -Path $WorkDirectory, $shardDirectory | Out-Null

$index = Get-Content -LiteralPath $indexFile -Raw | ConvertFrom-Json -AsHashtable
$headers = @{}
$matrices = [Collections.Generic.List[object]]::new()
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
    $outputDimension = if ($shape.Count -eq 3) {
        [int]$shape[-2]
    }
    else {
        [int]$shape[0]
    }
    $expertCount = if ($shape.Count -eq 3) { [int]$shape[0] } else { 1 }
    if ($expertCount -lt 1) {
        throw "Invalid expert count in source shape for $($weight.Key)"
    }
    $layerMatch = [regex]::Match(
        $weight.Key,
        '^model\.language_model\.layers\.(\d+)\.'
    )
    if (-not $layerMatch.Success) {
        throw "Cannot read decoder layer from quantizable tensor $($weight.Key)"
    }
    $layer = [int]$layerMatch.Groups[1].Value
    $safeName = ($weight.Key -replace '[^A-Za-z0-9_.-]', '_').Replace('.', '_')
    $matrices.Add([pscustomobject]@{
        Name = $weight.Key
        Layer = $layer
        InputDimension = $inputDimension
        OutputDimension = $outputDimension
        ExpertCount = $expertCount
        Output = Join-Path $shardDirectory ($safeName + '.ternary.safetensors')
    })
}
if ($matrices.Count -eq 0) {
    throw 'No quantizable Qwen3.5 matrices were found'
}

$allMatrices = @($matrices | Where-Object {
    $isMoe = $_.Name.Contains('.mlp.experts.') -or
        $_.Name.Contains('.mlp.shared_expert.')
    $setMatch = $MatrixSet -eq 'All' -or
        ($MatrixSet -eq 'Moe' -and $isMoe) -or
        ($MatrixSet -eq 'Dense' -and -not $isMoe)
    $isDown = $_.Name.Contains('.mlp.experts.down_proj') -or
        $_.Name.Contains('.mlp.shared_expert.down_proj.weight')
    $isGateUp = $_.Name.Contains('.mlp.experts.gate_up_proj') -or
        $_.Name.Contains('.mlp.shared_expert.gate_proj.weight') -or
        $_.Name.Contains('.mlp.shared_expert.up_proj.weight')
    $stageMatch = $MatrixStage -eq 'All' -or
        ($MatrixStage -eq 'Down' -and $isDown) -or
        ($MatrixStage -eq 'GateUp' -and $isGateUp)
    $setMatch -and $stageMatch
} | Sort-Object Layer, Name)
if ($allMatrices.Count -eq 0) {
    throw "No quantizable Qwen3.5 matrices matched MatrixSet=$MatrixSet"
}
$availableLayers = @($allMatrices | Select-Object -ExpandProperty Layer -Unique)
if ($Layers) {
    $requestedLayers = Parse-Layers $Layers
    $invalidLayers = @($requestedLayers | Where-Object { $_ -notin $availableLayers })
    if ($invalidLayers.Count) {
        throw "Requested layers are absent from the checkpoint: $($invalidLayers -join ', ')"
    }
    $layerMatrices = @($allMatrices | Where-Object { $_.Layer -in $requestedLayers })
}
else {
    $requestedLayers = [Collections.Generic.SortedSet[int]]::new()
    foreach ($layer in $availableLayers) {
        $requestedLayers.Add([int]$layer) | Out-Null
    }
    $layerMatrices = $allMatrices
}
$selectedLayers = @($requestedLayers)
$layerMatrixCount = $layerMatrices.Count
if ($MaxMatrices -gt 0 -and $MaxMatrices -lt $layerMatrixCount) {
    $matrices = @($layerMatrices | Select-Object -First $MaxMatrices)
}
else {
    $matrices = $layerMatrices
}
if (-not $Resume) {
    foreach ($matrix in $matrices) {
        if (Test-Path -LiteralPath $matrix.Output -PathType Leaf) {
            Remove-Item -LiteralPath $matrix.Output -Force
        }
    }
}
$partialValidation = $matrices.Count -lt $layerMatrixCount
$dimensions = [Collections.Generic.SortedSet[int]]::new()
foreach ($matrix in $matrices) {
    $dimensions.Add($matrix.InputDimension) | Out-Null
}

$calibrationMode = 'synthetic dimension-global'
$expertAwareCalibration = $false
$calibrationKeys = @{}
$targetKeys = @{}
if ($CalibrationFile) {
    if (-not (Test-Path -LiteralPath $CalibrationFile -PathType Leaf)) {
        throw "External calibration file is missing: $CalibrationFile"
    }
    $effectiveCalibrationFile = (Resolve-Path -LiteralPath $CalibrationFile).Path
    $externalHeader = Read-SafeTensorHeader $effectiveCalibrationFile
    $externalRows = $null
    $externalRowsPerExpert = $null
    $perTensorNames = @($matrices | ForEach-Object { "cal.$($_.Name)" })
    $presentPerTensor = @($perTensorNames | Where-Object {
        $externalHeader.ContainsKey($_)
    })
    if ($presentPerTensor.Count -eq $perTensorNames.Count) {
        foreach ($matrix in $matrices) {
            $tensorName = "cal.$($matrix.Name)"
            $info = $externalHeader[$tensorName]
            if ([string]$info['dtype'] -ne 'F32') {
                throw "External calibration tensor $tensorName must be F32"
            }
            $shape = @($info['shape'])
            if ($shape.Count -ne 2) {
                throw "External calibration tensor $tensorName must have rank 2"
            }
            $rows = [long]$shape[0]
            $width = [long]$shape[1]
            if ($rows -lt 1) {
                throw "External calibration tensor $tensorName must have a positive row count"
            }
            if ($rows -gt [int]::MaxValue) {
                throw "External calibration tensor $tensorName row count is too large: $rows"
            }
            if ($width -ne $matrix.InputDimension) {
                throw "External calibration tensor $tensorName has width $width, expected $($matrix.InputDimension)"
            }
            if (($rows % $matrix.ExpertCount) -ne 0) {
                throw "External calibration tensor $tensorName has $rows rows, not divisible by experts=$($matrix.ExpertCount)"
            }
            $rowsPerExpert = [long]($rows / $matrix.ExpertCount)
            if ($rowsPerExpert -lt 1) {
                throw "External calibration tensor $tensorName must have a positive rows-per-expert count"
            }
            if ($null -eq $externalRowsPerExpert) {
                $externalRowsPerExpert = $rowsPerExpert
            }
            elseif ($rowsPerExpert -ne $externalRowsPerExpert) {
                throw "External calibration tensors must have the same rows per expert; $tensorName has $rowsPerExpert, expected $externalRowsPerExpert"
            }
            $calibrationKeys[$matrix.Name] = $tensorName
            $targetName = "target.$($matrix.Name)"
            if ($externalHeader.ContainsKey($targetName)) {
                $targetInfo = $externalHeader[$targetName]
                if ([string]$targetInfo['dtype'] -notin @('F32', 'BF16', 'F16')) {
                    throw "External target tensor $targetName must be F32, BF16, or F16"
                }
                $targetShape = @($targetInfo['shape'])
                if ($targetShape.Count -ne 2 -or
                    [long]$targetShape[0] -ne $rows -or
                    [long]$targetShape[1] -ne $matrix.OutputDimension) {
                    throw "External target tensor $targetName must have shape [$rows,$($matrix.OutputDimension)]"
                }
                $targetKeys[$matrix.Name] = $targetName
            }
            elseif ($RequireTargets) {
                throw "External calibration is missing required target tensor $targetName"
            }
        }
        $externalRows = $externalRowsPerExpert
        $calibrationMode = 'external per-tensor expert-aware'
        $expertAwareCalibration = $true
    }
    elseif ($presentPerTensor.Count -gt 0) {
        $missingPerTensor = @($perTensorNames | Where-Object {
            -not $externalHeader.ContainsKey($_)
        })
        $preview = ($missingPerTensor | Select-Object -First 5) -join ', '
        throw "External calibration has $($presentPerTensor.Count) of $($perTensorNames.Count) selected per-tensor keys; missing: $preview"
    }
    else {
        foreach ($dimension in $dimensions) {
            $tensorName = "cal_$dimension"
            $info = $externalHeader[$tensorName]
            if (-not $info) {
                throw "External calibration is missing tensor $tensorName"
            }
            if ([string]$info['dtype'] -ne 'F32') {
                throw "External calibration tensor $tensorName must be F32"
            }
            $shape = @($info['shape'])
            if ($shape.Count -ne 2) {
                throw "External calibration tensor $tensorName must have rank 2"
            }
            $rows = [long]$shape[0]
            $width = [long]$shape[1]
            if ($rows -lt 1) {
                throw "External calibration tensor $tensorName must have a positive row count"
            }
            if ($rows -gt [int]::MaxValue) {
                throw "External calibration tensor $tensorName row count is too large: $rows"
            }
            if ($width -ne $dimension) {
                throw "External calibration tensor $tensorName has width $width, expected $dimension"
            }
            if ($null -eq $externalRows) {
                $externalRows = $rows
            }
            elseif ($rows -ne $externalRows) {
                throw "External calibration tensors must have the same row count"
            }
        }
        foreach ($matrix in $matrices) {
            $calibrationKeys[$matrix.Name] = "cal_$($matrix.InputDimension)"
        }
        $calibrationMode = 'external dimension-global'
    }
    if ($externalRows -gt [int]::MaxValue) {
        throw "External calibration row count is too large: $externalRows"
    }
    $CalibrationRows = [int]$externalRows
}
else {
    $effectiveCalibrationFile = $syntheticCalibrationFile
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
    $stream = [IO.File]::Create($effectiveCalibrationFile)
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
    foreach ($matrix in $matrices) {
        $calibrationKeys[$matrix.Name] = "cal_$($matrix.InputDimension)"
    }
}

if ($expertAwareCalibration) {
    $expertCounts = @($matrices | Select-Object -ExpandProperty ExpertCount -Unique | Sort-Object)
    Write-Host "[calibration] mode=$calibrationMode rows/expert=$CalibrationRows experts=$($expertCounts -join ',') widths=$($dimensions -join ',') path=$effectiveCalibrationFile"
}
else {
    Write-Host "[calibration] mode=$calibrationMode rows=$CalibrationRows widths=$($dimensions -join ',') path=$effectiveCalibrationFile"
}
$calibrationPath = PicoPath $effectiveCalibrationFile
$lines = @(foreach ($matrix in $matrices) {
    $fields = @(
        $matrix.Name
        $calibrationPath
        $calibrationKeys[$matrix.Name]
        (PicoPath $matrix.Output)
    )
    if ($expertAwareCalibration) {
        $fields += ''
        $fields += "experts=$($matrix.ExpertCount)"
    }
    elseif ($targetKeys.ContainsKey($matrix.Name)) {
        $fields += ''
        $fields += ''
    }
    if ($targetKeys.ContainsKey($matrix.Name)) {
        $fields += $targetKeys[$matrix.Name]
    }
    $fields -join "`t"
})
[IO.File]::WriteAllLines($manifestFile, $lines)
$pendingMatrices = @(if ($Resume) {
    $matrices | Where-Object { -not (Test-Path -LiteralPath $_.Output) }
}
else {
    $matrices
})
$pendingLines = @(foreach ($matrix in $pendingMatrices) {
    $fields = @(
        $matrix.Name
        $calibrationPath
        $calibrationKeys[$matrix.Name]
        (PicoPath $matrix.Output)
    )
    if ($expertAwareCalibration) {
        $fields += ''
        $fields += "experts=$($matrix.ExpertCount)"
    }
    elseif ($targetKeys.ContainsKey($matrix.Name)) {
        $fields += ''
        $fields += ''
    }
    if ($targetKeys.ContainsKey($matrix.Name)) {
        $fields += $targetKeys[$matrix.Name]
    }
    $fields -join "`t"
})

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

$missingOutputs = @($matrices | Where-Object { -not (Test-Path -LiteralPath $_.Output -PathType Leaf) })
if ($missingOutputs.Count) {
    throw "Expected $($matrices.Count) output shards, missing $($missingOutputs.Count)"
}
$outputs = @($matrices | ForEach-Object { Get-Item -LiteralPath $_.Output })
$totalBytes = ($outputs | Measure-Object -Property Length -Sum).Sum
foreach ($output in $outputs) {
    $outputHeader = Read-SafeTensorHeader $output.FullName
    if ($outputHeader['__metadata__']['format'] -ne 'picoscript-catq-ternary-v1') {
        throw "Invalid CAT-Q output: $($output.FullName)"
    }
}

$runOutput | ForEach-Object { Write-Host $_ }
Write-Host ''
$tensorSummary = [string]$matrices.Count
if ($partialValidation) {
    $tensorSummary += " of $layerMatrixCount after layer filter (MaxMatrices=$MaxMatrices)"
}
Write-Host "$(if ($partialValidation) { 'Partial validation' } else { 'Full' }) Qwen3.5-35B-A3B CAT-Q conversion passed"
Write-Host "  backend:      $(if ($Cpu) { 'CPU' } else { 'CUDA' })"
Write-Host "  tensors:      $tensorSummary"
Write-Host "  converted:    $($pendingMatrices.Count)"
Write-Host "  matrix set:   $MatrixSet"
Write-Host "  matrix stage: $MatrixStage"
Write-Host "  targets:      $($targetKeys.Count)$(if ($RequireTargets) { ' required' } else { '' })"
Write-Host "  layers:       $($selectedLayers -join ', ')"
Write-Host "  input widths: $($dimensions -join ', ')"
Write-Host "  calibration:  $calibrationMode ($effectiveCalibrationFile)"
Write-Host "  schedule:     $Epochs epochs, $CalibrationRows $(if ($expertAwareCalibration) { 'rows/expert' } else { 'rows' }), batch $BatchSize"
if ($expertAwareCalibration) {
    Write-Host "  experts:      per source tensor rank-3 first dimension (shared/dense = 1)"
}
Write-Host "  plan batches: $batchCount x <= $MatricesPerBatch matrices"
if (-not $Cpu) {
    Write-Host "  CUDA chunk:   $CudaChunkWeights weights"
}
Write-Host "  execution:    $([math]::Round($executionSeconds, 2)) s"
Write-Host "  ternary size: $([math]::Round($totalBytes / 1GB, 2)) GB"
Write-Host "  output dir:   $shardDirectory"
