[CmdletBinding()]
param(
    [string]$ModelDirectory = 'C:\source\Qwen3-0.6B-Base',
    [string]$WorkDirectory,
    [int]$Epochs = 2,
    [int]$CalibrationRows = 4,
    [switch]$RebuildWeights
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path $ModelDirectory 'catq-mlp'
}
$gateWork = Join-Path $WorkDirectory 'gate'
$upWork = Join-Path $WorkDirectory 'up'
$downWork = Join-Path $WorkDirectory 'down'
$output = Join-Path $WorkDirectory 'mlp-output.safetensors'
$source = Join-Path $WorkDirectory 'qwen-ternary-mlp.pc'
$executable = Join-Path $WorkDirectory 'qwen-ternary-mlp.exe'
$smoke = Join-Path $PSScriptRoot 'run_catq_smoke.ps1'

function Ensure-CatQWeight {
    param(
        [string]$Tensor,
        [string]$Directory,
        [int]$Seed
    )
    $packed = Join-Path $Directory 'tensor.ternary.safetensors'
    if ($RebuildWeights -or -not (Test-Path -LiteralPath $packed)) {
        & $smoke `
            -ModelDirectory $ModelDirectory `
            -TensorName $Tensor `
            -WorkDirectory $Directory `
            -Epochs $Epochs `
            -CalibrationRows $CalibrationRows `
            -BatchSize 1 `
            -Seed $Seed `
            -SkipDownload
        if ($LASTEXITCODE -ne 0) {
            throw "CAT-Q conversion failed for $Tensor"
        }
    }
}

function PicoPath([string]$Path) {
    return ([IO.Path]::GetFullPath($Path)).Replace('\', '/')
}

New-Item -ItemType Directory -Force -Path $WorkDirectory | Out-Null
Ensure-CatQWeight 'model.layers.0.mlp.gate_proj.weight' $gateWork 260626650
Ensure-CatQWeight 'model.layers.0.mlp.up_proj.weight' $upWork 260626650
Ensure-CatQWeight 'model.layers.0.mlp.down_proj.weight' $downWork 260626651

$program = @"
int model = Shard.Load("$(PicoPath (Join-Path $ModelDirectory 'model.safetensors'))", "mmap");
int gamma = Tensor.Map(model, "tensor=model.layers.0.post_attention_layernorm.weight");
int inputShard = Shard.Load("$(PicoPath (Join-Path $gateWork 'calibration.safetensors'))", "mmap");
int inputs = Tensor.Map(inputShard, "tensor=calibration");
int input = Tensor.View(inputs, "row_start=0;row_count=1");
int normalized = Tensor.RmsNorm(input, gamma);
int gateShard = Shard.Load("$(PicoPath (Join-Path $gateWork 'tensor.ternary.safetensors'))", "mmap");
int upShard = Shard.Load("$(PicoPath (Join-Path $upWork 'tensor.ternary.safetensors'))", "mmap");
int downShard = Shard.Load("$(PicoPath (Join-Path $downWork 'tensor.ternary.safetensors'))", "mmap");
int gateWeights = Tensor.Map(gateShard, "catq_packed=1");
int upWeights = Tensor.Map(upShard, "catq_packed=1");
int downWeights = Tensor.Map(downShard, "catq_packed=1");
int gate = BitLinear.MatVecCatQ(gateWeights, normalized);
int up = BitLinear.MatVecCatQ(upWeights, normalized);
int hidden = Tensor.SwiGLU(gate, up);
int down = BitLinear.MatVecCatQ(downWeights, hidden);
int result = Tensor.Add(input, down);
int saved = Shard.Save(result, "$(PicoPath $output)");
if (saved == 0) { raise 8001; }
return saved;
"@
[IO.File]::WriteAllText($source, $program)

Push-Location $repoRoot
try {
    & python picoscript_build.py native $source --provider catq --profile host -o $executable
    if ($LASTEXITCODE -ne 0) { throw 'Qwen ternary MLP build failed' }
}
finally {
    Pop-Location
}

$runnerOutput = @()
$elapsed = Measure-Command {
    $runnerOutput = & $executable 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Qwen ternary MLP execution failed' }
}

$stream = [IO.File]::OpenRead($output)
try {
    $reader = [IO.BinaryReader]::new($stream, [Text.Encoding]::UTF8, $true)
    $headerLength = $reader.ReadUInt64()
    $header = [Text.Encoding]::UTF8.GetString(
        $reader.ReadBytes([int]$headerLength)
    ).TrimEnd() | ConvertFrom-Json -AsHashtable
    $shape = @($header['tensor']['shape'])
    if ($shape.Count -ne 2 -or [int]$shape[0] -ne 1 -or [int]$shape[1] -ne 1024) {
        throw "Unexpected MLP output shape: $($shape -join ',')"
    }
    $min = [single]::PositiveInfinity
    $max = [single]::NegativeInfinity
    $sum = 0.0
    for ($i = 0; $i -lt 1024; $i++) {
        $value = $reader.ReadSingle()
        if ([single]::IsNaN($value) -or [single]::IsInfinity($value)) {
            throw "MLP output contains an invalid value at index $i"
        }
        if ($value -lt $min) { $min = $value }
        if ($value -gt $max) { $max = $value }
        $sum += $value
    }
}
finally {
    $stream.Dispose()
}

$runnerOutput | ForEach-Object { Write-Host $_ }
Write-Host ''
Write-Host 'PicoScript Qwen CAT-Q MLP passed'
Write-Host '  graph:    RMSNorm -> gate/up CAT-Q -> Tensor.SwiGLU -> down CAT-Q -> residual'
Write-Host '  input:    1 x 1024'
Write-Host '  hidden:   1 x 3072'
Write-Host '  output:   1 x 1024'
Write-Host "  elapsed:  $([math]::Round($elapsed.TotalSeconds, 3)) s"
Write-Host "  range:    [$min, $max]"
Write-Host "  mean:     $($sum / 1024)"
