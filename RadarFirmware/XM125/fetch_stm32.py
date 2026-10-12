"""Fetch only the official STM32CubeL4 1.17.0 build dependencies.

Checks each download against its Git blob hash. Nothing is installed globally.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parent
REV = "v1.17.0"
REPO = "STMicroelectronics/STM32CubeL4"
DEST = ROOT / "vendor/STM32Cube_FW_L4_V1.17.0"
HAL = {"stm32l4xx_hal" + suffix + ".c" for suffix in (
    "", "_cortex", "_dma", "_gpio", "_i2c", "_i2c_ex", "_rcc", "_rcc_ex",
    "_rtc", "_rtc_ex", "_pwr", "_pwr_ex", "_spi", "_uart", "_uart_ex",
    "_flash", "_flash_ex", "_exti")}


def wanted(path):
    return (path.startswith("Drivers/STM32L4xx_HAL_Driver/Inc/") and path.endswith(".h")
            or path.startswith("Drivers/CMSIS/Include/") and path.endswith(".h")
            or path.startswith("Drivers/STM32L4xx_HAL_Driver/Src/") and Path(path).name in HAL
            or path in {"Drivers/CMSIS/Device/ST/STM32L4xx/Include/stm32l4xx.h",
                        "Drivers/CMSIS/Device/ST/STM32L4xx/Include/stm32l431xx.h",
                        "Drivers/CMSIS/Device/ST/STM32L4xx/Include/system_stm32l4xx.h",
                        "Drivers/CMSIS/Device/ST/STM32L4xx/Source/Templates/gcc/startup_stm32l431xx.s",
                        "Drivers/STM32L4xx_HAL_Driver/License.md",
                        "Drivers/CMSIS/Device/ST/STM32L4xx/License.md"})


def main():
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/git/trees/{REV}?recursive=1",
        headers={"User-Agent": "BMI30-XM125-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        tree = json.load(response)
    if tree.get("truncated"):
        raise RuntimeError("Incomplete repository tree")
    files = [f for f in tree["tree"] if f["type"] == "blob" and wanted(f["path"])]
    def fetch(entry):
        path = DEST / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes() if path.is_file() else b""
        def git_hash(data):
            return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if git_hash(data) != entry["sha"]:
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(
                            f"https://raw.githubusercontent.com/{REPO}/{REV}/{entry['path']}", timeout=20) as r:
                        data = r.read()
                    break
                except (OSError, TimeoutError):
                    if attempt == 3:
                        raise
            if git_hash(data) != entry["sha"]:
                raise RuntimeError("Hash mismatch: " + entry["path"])
            path.write_bytes(data)
        return {"path": entry["path"], "git_blob": entry["sha"], "sha256": hashlib.sha256(data).hexdigest()}
    with ThreadPoolExecutor(max_workers=8) as pool:
        checks = list(pool.map(fetch, files))
    manifest = {"repository": REPO, "ref": REV, "tree": tree["sha"], "files": checks}
    (DEST / "dependency-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"downloaded_verified_files": len(checks), "tree": tree["sha"]}))


if __name__ == "__main__":
    main()
