"""Compile the actual C state machine for Cortex-M4 and run it in Unicorn.

Test dependencies are confined to .tmp/xm125_test_deps in the BMI30 workspace.
This proves algorithm behavior, not the radar SDK or electrical GPIO output.
"""
from pathlib import Path
import json
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
sys.path.insert(0, str(WORKSPACE / ".tmp/xm125_test_deps"))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR


def main():
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    elf_path = build / "gate_tests.elf"
    gcc = shutil.which("arm-none-eabi-gcc")
    if not gcc:
        raise RuntimeError("arm-none-eabi-gcc is required")
    subprocess.run([
        gcc, "-mcpu=cortex-m4", "-mthumb", "-Os", "-ffreestanding",
        "-fno-builtin", "-Wall", "-Wextra", "-Werror", "-nostdlib",
        "-I" + str(ROOT / "src"), str(ROOT / "src/gate.c"),
        "-I" + str(ROOT / "tests/stubs"), str(ROOT / "src/storage.c"),
        str(ROOT / "src/protocol.c"), str(ROOT / "tests/gate_tests.c"),
        "-T" + str(ROOT / "tests/test_link.ld"),
        "-Wl,--start-group", "-lc", "-lgcc", "-Wl,--end-group", "-o", str(elf_path)
    ], check=True)
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    uc.mem_map(0x08000000, 128 * 1024)
    uc.mem_map(0x20000000, 64 * 1024)
    with elf_path.open("rb") as f:
        elf = ELFFile(f)
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                uc.mem_write(segment["p_vaddr"], segment.data())
        symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
        entry = elf.header["e_entry"]
    uc.reg_write(UC_ARM_REG_SP, 0x2000FFF0)
    uc.reg_write(UC_ARM_REG_LR, 0x08000001)
    def completed(uc, address, size, user):
        if int.from_bytes(uc.mem_read(symbols["test_done"], 4), "little"):
            uc.emu_stop()
    uc.hook_add(UC_HOOK_CODE, completed)
    uc.emu_start(entry | 1, 0, timeout=30_000_000, count=30_000_000)
    values = {key: struct.unpack("<I", uc.mem_read(symbols[key], 4))[0]
              for key in ("test_failure", "test_count", "test_done")}
    print(json.dumps(values))
    if values["test_failure"] or values["test_count"] != 92 or not values["test_done"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
