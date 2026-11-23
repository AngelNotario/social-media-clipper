# Descarga llama.cpp (Windows + CUDA 13.5) y Qwen3.6-35B-A3B (Q4_K_M + MTP + vision) para SOCIAL MEDIA CLIPPER.
# Se puede volver a ejecutar: retoma lo que falte (curl -C -).
$ErrorActionPreference = 'Continue'
$Build = 'b12597'
$L = 'D:\llama.cpp'
$M = 'D:\models\Qwen3.6-35B-A3B-Q4-MTP'
$Log = Join-Path $M 'descarga.log'
New-Item -ItemType Directory -Force $L, $M | Out-Null
function Log($t) { "$(Get-Date -Format 'HH:mm:ss') $t" | Tee-Object -FilePath $Log -Append }

Log "Inicio. llama.cpp $Build -> $L ; modelo -> $M"
$rel = "https://github.com/ggml-org/llama.cpp/releases/download/$Build"
foreach ($z in @("llama-$Build-bin-win-cuda-13.5-x64.zip", "cudart-llama-bin-win-cuda-13.5-x64.zip")) {
  $dst = Join-Path $L $z
  if (-not (Test-Path (Join-Path $L 'llama-server.exe')) -or -not (Test-Path $dst)) {
    Log "Bajando $z"
    curl.exe -L -C - --retry 10 --retry-all-errors -s -S -o $dst "$rel/$z"
    Expand-Archive -Force $dst $L
  }
}
if (Test-Path (Join-Path $L 'llama-server.exe')) { Log "llama-server listo" } else { Log "ERROR: no quedo llama-server.exe" }

$base = 'https://huggingface.co/ggml-org/Qwen3.6-35B-A3B-GGUF/resolve/main'
foreach ($f in @('mtp-Qwen3.6-35B-A3B-Q4_0.gguf', 'mmproj-Qwen3.6-35B-A3B-Q8_0.gguf', 'Qwen3.6-35B-A3B-Q4_K_M.gguf')) {
  Log "Bajando $f"
  curl.exe -L -C - --retry 20 --retry-all-errors --retry-delay 5 -s -S -o (Join-Path $M $f) "$base/$f`?download=true"
  Log ("Terminado {0}: {1:N2} GB" -f $f, ((Get-Item (Join-Path $M $f)).Length / 1GB))
}
Log "LISTO"
