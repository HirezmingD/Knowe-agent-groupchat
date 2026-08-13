$ErrorActionPreference = 'Stop'

$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$manifest = Join-Path $repository 'native\knowe-sandbox-launcher\Cargo.toml'
$target = Join-Path $repository 'build\native-target'
$outputDirectory = Join-Path $repository 'build\native'
$output = Join-Path $outputDirectory 'knowe-sandbox-launcher.exe'

$rustVersion = rustc -vV
if ($LASTEXITCODE -ne 0) {
    throw "rustc -vV failed with exit code $LASTEXITCODE."
}
$rustHost = ($rustVersion | Select-String '^host:\s+(.+)$').Matches.Groups[1].Value
if ($rustHost -ne 'x86_64-pc-windows-msvc') {
    throw "Sandbox launcher packaging requires x86_64-pc-windows-msvc rustc; found '$rustHost'."
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$env:CARGO_TARGET_DIR = $target
cargo build --locked --release --manifest-path $manifest
if ($LASTEXITCODE -ne 0) {
    throw "Sandbox launcher cargo build failed with exit code $LASTEXITCODE."
}
Copy-Item -LiteralPath (Join-Path $target 'release\knowe-sandbox-launcher.exe') -Destination $output -Force

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $output).Hash
Write-Output "Built $output"
Write-Output "SHA256 $hash"
