[CmdletBinding()]
param(
    [string]$ModelDirectory = 'C:\source\Qwen3-0.6B-Base',
    [string]$WorkDirectory,
    [switch]$RebuildSmokeShard
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $WorkDirectory) {
    $WorkDirectory = Join-Path $ModelDirectory 'catq-smoke'
}
$packed = Join-Path $WorkDirectory 'tensor.ternary.safetensors'
$calibration = Join-Path $WorkDirectory 'calibration.safetensors'
$output = Join-Path $WorkDirectory 'gate-projection.safetensors'
$source = Join-Path $WorkDirectory 'catq-ternary-infer.pc'
$executable = Join-Path $WorkDirectory 'catq-ternary-infer.exe'

if ($RebuildSmokeShard -or
    -not (Test-Path -LiteralPath $packed) -or
    -not (Test-Path -LiteralPath $calibration)) {
    & (Join-Path $PSScriptRoot 'run_catq_smoke.ps1') `
        -ModelDirectory $ModelDirectory `
        -WorkDirectory $WorkDirectory `
        -SkipDownload
    if ($LASTEXITCODE -ne 0) {
        throw 'CAT-Q smoke conversion failed'
    }
}

function PicoPath([string]$Path) {
    return ([IO.Path]::GetFullPath($Path)).Replace('\', '/')
}

$program = @"
int packedShard = Shard.Load("$(PicoPath $packed)", "mmap");
int packed = Tensor.Map(packedShard, "catq_packed=1");
if (packed == 0) { raise 7001; }
int calibrationShard = Shard.Load("$(PicoPath $calibration)", "mmap");
int samples = Tensor.Map(calibrationShard, "tensor=calibration");
int activation = Tensor.View(samples, "row_start=0;row_count=1");
int projection = BitLinear.MatVecCatQ(packed, activation);
if (projection == 0) { raise 7002; }
int saved = Shard.Save(projection, "$(PicoPath $output)");
if (saved == 0) { raise 7003; }
return saved;
"@
[IO.File]::WriteAllText($source, $program)

Push-Location $repoRoot
try {
    & python picoscript_build.py native $source --provider catq -o $executable
    if ($LASTEXITCODE -ne 0) { throw 'PicoScript ternary inference build failed' }
}
finally {
    Pop-Location
}

$runnerOutput = @()
$elapsed = Measure-Command {
    $runnerOutput = & $executable 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'PicoScript ternary inference failed' }
}

$stream = [IO.File]::OpenRead($output)
try {
    $reader = [IO.BinaryReader]::new($stream, [Text.Encoding]::UTF8, $true)
    $headerLength = $reader.ReadUInt64()
    $header = [Text.Encoding]::UTF8.GetString(
        $reader.ReadBytes([int]$headerLength)
    ).TrimEnd() | ConvertFrom-Json -AsHashtable
    $shape = @($header['tensor']['shape'])
    if ($shape.Count -ne 2 -or [int]$shape[1] -ne 1) {
        throw "Unexpected projection shape: $($shape -join ',')"
    }
    $count = [int]$shape[0]
    $min = [single]::PositiveInfinity
    $max = [single]::NegativeInfinity
    $sum = 0.0
    for ($i = 0; $i -lt $count; $i++) {
        $value = $reader.ReadSingle()
        if ([single]::IsNaN($value) -or [single]::IsInfinity($value)) {
            throw "Projection contains an invalid value at index $i"
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
Write-Host 'PicoScript CAT-Q ternary inference passed'
Write-Host "  operation: BitLinear.MatVecCatQ"
Write-Host "  input:     1 x 1024 activation"
Write-Host "  output:    $count x 1 projection"
Write-Host "  elapsed:   $([math]::Round($elapsed.TotalSeconds, 3)) s"
Write-Host "  range:     [$min, $max]"
Write-Host "  mean:      $($sum / $count)"
