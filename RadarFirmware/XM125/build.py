"""Build the XM125 firmware without a Unix shell or global SDK installation."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
SDK = ROOT / "vendor/xm125"
CUBE = ROOT / "vendor/STM32Cube_FW_L4_V1.17.0"
OUT = ROOT / "build"


def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError("SDK build anchor changed: " + repr(old))
    return text.replace(old, new, 1)


def main():
    OUT.mkdir(exist_ok=True)
    gen = OUT / "generated"
    gen.mkdir(exist_ok=True)
    # Keep the downloaded SDK intact and record all generated build adaptations.
    text = (SDK / "Src/main.c").read_text()
    text = once(text, "huart1.Init.BaudRate               = 921600;", "huart1.Init.BaudRate               = 115200;")
    text = once(text, "huart2.Init.HwFlowCtl              = UART_HWCONTROL_RTS_CTS;",
                "huart2.Init.HwFlowCtl              = UART_HWCONTROL_NONE;")
    text = once(text, "GPIO_InitStruct.Pin  = MISC_GPIO0_Pin;\n\tGPIO_InitStruct.Mode = GPIO_MODE_INPUT;\n"
                "\tGPIO_InitStruct.Pull = GPIO_PULLDOWN;",
                "HAL_GPIO_WritePin(MISC_GPIO0_GPIO_Port, MISC_GPIO0_Pin, GPIO_PIN_RESET);\n"
                "\tGPIO_InitStruct.Pin  = MISC_GPIO0_Pin;\n\tGPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_OD;\n"
                "\tGPIO_InitStruct.Pull = GPIO_NOPULL;")
    (gen / "main.c").write_text(text)
    text = (SDK / "Src/syscalls.c").read_text()
    text = once(text, "HAL_UART_Transmit(&DEBUG_UART_HANDLE, (uint8_t *)ptr, len, 0xFFFF);", "(void)ptr;")
    (gen / "syscalls.c").write_text(text)
    text = (SDK / "STM32L431CBYx_FLASH.ld").read_text()
    text = once(text, "FLASH (rx)      : ORIGIN = 0x8000000, LENGTH = 128K",
                "FLASH (rx)      : ORIGIN = 0x8000000, LENGTH = 124K")
    text = re.sub(r"/DISCARD/\s*:\s*\{[^}]*\}", "", text)
    linker = gen / "gate_flash.ld"
    linker.write_text(text)
    gcc = shutil.which("arm-none-eabi-gcc")
    if not gcc:
        raise RuntimeError("ARM GCC 13.3.1 is required (same LTO version as SDK)")
    includes = [ROOT / "src", SDK / "Inc", CUBE / "Drivers/CMSIS/Include",
                CUBE / "Drivers/STM32L4xx_HAL_Driver/Inc",
                CUBE / "Drivers/CMSIS/Device/ST/STM32L4xx/Include"]
    common = ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16"]
    flags = common + ["-std=c11", "-Os", "-g", "-Wall", "-Wextra", "-Werror",
                      "-ffunction-sections", "-fdata-sections", "-fno-lto", "-fno-math-errno",
                      "-DSTM32L431xx", "-DUSE_HAL_DRIVER"] + ["-I" + str(p) for p in includes]
    flags += ["-DTARGET_ARCH_cm4", "-DFLOAT_ABI_HARD"]
    startup = CUBE / "Drivers/CMSIS/Device/ST/STM32L4xx/Source/Templates/gcc/startup_stm32l431xx.s"
    sources = [startup, gen / "main.c", gen / "syscalls.c"]
    sources += [SDK / "Src" / name for name in (
        "stm32l4xx_hal_msp.c", "stm32l4xx_it.c", "system_stm32l4xx.c", "sysmem.c")]
    sources += [SDK / "Src/integration" / name for name in (
        "acc_hal_integration_stm32cube_xm.c", "acc_integration_stm32.c")]
    sources += list((ROOT / "src").glob("*.c"))
    sources += sorted((CUBE / "Drivers/STM32L4xx_HAL_Driver/Src").glob("*.c"))
    def compile_one(item):
        index, source = item
        obj = OUT / f"{index:02d}_{source.stem}.o"
        vendor_flags = ["-Wno-unused-parameter"] if source.is_relative_to(CUBE) else []
        result = subprocess.run([gcc, *flags, *vendor_flags, "-c", str(source), "-o", str(obj)], capture_output=True, text=True)
        return source, obj, result
    with ThreadPoolExecutor(max_workers=4) as pool:
        compiled = list(pool.map(compile_one, enumerate(sources)))
    failed = [(s, r) for s, o, r in compiled if r.returncode]
    if failed:
        for source, result in failed:
            print(str(source) + "\n" + result.stdout + result.stderr)
        raise SystemExit(1)
    elf = OUT / "bmi30_xm125_gate_0.1.2.elf"
    subprocess.run([gcc, *common, "-fno-lto", "-Os", "--specs=nano.specs", "--specs=nosys.specs",
                    "-Wl,--gc-sections", "-Wl,-Map=" + str(elf.with_suffix(".map")),
                    "-T" + str(linker), *[str(o) for s, o, r in compiled],
                    "-Wl,--start-group", str(SDK / "lib/libacconeer_a121.a"),
                    str(SDK / "lib/libacc_detector_distance_a121.a"),
                    "-lm", "-lc", "-lgcc", "-Wl,--end-group", "-o", str(elf)], check=True)
    for fmt, ext in (("binary", ".bin"), ("ihex", ".hex")):
        subprocess.run([shutil.which("arm-none-eabi-objcopy"), "-O", fmt, str(elf), str(elf.with_suffix(ext))], check=True)
    size = subprocess.check_output([shutil.which("arm-none-eabi-size"), str(elf)], text=True)
    print(size)
    binary = elf.with_suffix(".bin")
    assert len(binary.read_bytes()) <= 124 * 1024
    manifest = {"firmware": "0.1.2", "gpio0_mode": "open_drain", "gpio0_internal_pull": "none",
                "radar_max_step_length": 4,
                "radar_max_profile": 3, "radar_end": "config.range_end_mm",
                "sdk": (SDK / "VERSION").read_text().strip(),
                "compiler": subprocess.check_output([gcc, "--version"], text=True).splitlines()[0],
                "stm32cube": "1.17.0", "flash_reserved": ["0x0801F000", "0x0801F800"],
                "size": size, "bin_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "sources": {str(s.relative_to(ROOT)): hashlib.sha256(s.read_bytes()).hexdigest() for s in sources},
                "libraries": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                    SDK / "lib/libacconeer_a121.a", SDK / "lib/libacc_detector_distance_a121.a")}}
    (OUT / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"binary": str(binary), "bytes": binary.stat().st_size, "sha256": manifest["bin_sha256"]}))


if __name__ == "__main__":
    main()
