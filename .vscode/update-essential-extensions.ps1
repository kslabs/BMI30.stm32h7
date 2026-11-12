param(
    [switch]$ListOnly
)

$ErrorActionPreference = "Stop"

$requiredExtensions = @(
    "ms-python.python",
    "ms-python.vscode-pylance",
    "ms-python.debugpy",
    "ms-python.vscode-python-envs",
    "ms-vscode.cpptools",
    "ms-vscode.cmake-tools",
    "marus25.cortex-debug"
)

$installed = @(code --list-extensions)

Write-Host "Required extensions:"
$requiredExtensions | ForEach-Object { Write-Host " - $_" }

Write-Host ""
Write-Host "Installed required extensions:"
$requiredExtensions | Where-Object { $_ -in $installed } | ForEach-Object { Write-Host " + $_" }

if ($ListOnly) {
    Write-Host ""
    Write-Host "ListOnly mode: no updates applied."
    exit 0
}

Write-Host ""
Write-Host "Updating required extensions only..."
foreach ($ext in $requiredExtensions) {
    Write-Host "Updating: $ext"
    code --install-extension $ext --force | Out-Host
}

Write-Host ""
Write-Host "Done."
