#!/usr/bin/env python3
"""Offline pinned1.2.57 M02 feature-preserving rebuild. No programmer, device access or source-tree writes.

The --project checkout must contain the isolated exact1.2.56 source snapshot. --output must be a NEW directory outside this release and the project.
Only the BIN is expected byte-identical; ELF/DWARF/map embed build paths.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe relative build input')
    result = (root/relative).resolve()
    if not result.is_relative_to(root.resolve()) or not result.is_file():
        raise ValueError('Missing or redirected build input: '+str(relative))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--toolchain', type=Path, required=True, help='GNU Tools for STM32 GCC bin directory')
    parser.add_argument('--cube-h7', type=Path, required=True, help='STM32Cube_FW_H7_V1.12.1 SDK root')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--bootstrap-unpinned', action='store_true', help='Only first locally reviewed build before expected BIN SHA exists')
    args = parser.parse_args()
    spec = json.loads((HERE/'build-spec.json').read_text(encoding='utf-8'))
    if spec.get('expected_binary_sha256') is None and not args.bootstrap_unpinned:
        raise ValueError('Unpinned bootstrap requires explicit flag; no release image is verified yet')
    roots = {'project': args.project.resolve(), 'cube_h7': args.cube_h7.resolve(),
             'output': args.output.resolve()}
    output = roots['output']
    if output.exists() or output.is_relative_to(HERE) or output.is_relative_to(roots['project']):
        raise ValueError('--output must be new and outside release/project; existing artifacts are immutable')

    def tool(name):
        for suffix in ('.exe', ''):
            path = args.toolchain/(name+suffix)
            if path.is_file():
                return str(path.resolve())
        raise ValueError('Tool not found: '+name)

    gcc = tool('arm-none-eabi-gcc')
    compiler = subprocess.check_output([gcc, '--version'], text=True).splitlines()[0]
    if spec['compiler_required_fragment'] not in compiler:
        raise ValueError('Pinned GNU Tools for STM32 compiler version mismatch')
    project_hashes = checked_path(HERE, spec['project_tree_manifest']['path'])
    if sha(project_hashes) != spec['project_tree_manifest']['sha256']:
        raise ValueError('Project source-tree hash manifest changed')
    for relative, expected in json.loads(project_hashes.read_text(encoding='utf-8')).items():
        if sha(checked_path(roots['project'], relative)) != expected:
            raise ValueError('Project source mismatch: '+relative)
    for item in spec['sdk_headers']:
        if sha(checked_path(roots['cube_h7'], item['path'])) != item['sha256']:
            raise ValueError('Cube H7 header mismatch: '+item['path'])
    sources = []
    for item in spec['sources']:
        path = checked_path(roots[item['root']], item['path'])
        if sha(path) != item['sha256']:
            raise ValueError('Compiled source mismatch: '+item['name'])
        sources.append((item, path))
    assert len(sources) == spec['source_count'] == 66
    version = checked_path(roots['project'], 'Core/Inc/build_info.h').read_text(encoding='utf-8')
    if not re.search(r'#define\s+FW_VERSION_STR\s+"'+re.escape(spec['version'])+'"', version):
        raise ValueError('Firmware version mismatch')
    linker = checked_path(roots['project'], spec['linker_script']['path'])
    if sha(linker) != spec['linker_script']['sha256']:
        raise ValueError('Linker script mismatch')
    frozen = checked_path(HERE, spec['frozen_build_header']['path'])
    if sha(frozen) != spec['frozen_build_header']['sha256']:
        raise ValueError('Frozen build identity changed')

    # No output directory is created until all pinned inputs have passed.
    output.mkdir(parents=True, exist_ok=False)
    for name in ('objects', 'logs', 'include'):
        (output/name).mkdir()
    (output/'include/build_autogen.h').write_bytes(frozen.read_bytes())
    includes = ['-I'+str(roots[item['root']]/item['path']) for item in spec['includes']]

    def compile_one(source):
        item, path = source
        obj = output/'objects'/Path(item['name']).with_suffix('.o')
        obj.parent.mkdir(parents=True, exist_ok=True)
        flags = ['-x', 'assembler-with-cpp'] if item['language'] == 'assembler' else spec['c_flags']+includes
        command = [gcc]+spec['common_flags']+flags+item['extra_flags']+['-c', str(path), '-o', str(obj)]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
        (output/'logs'/(item['name'].replace('/', '_')+'.log')).write_text(result.stdout+result.stderr, encoding='utf-8')
        return {'name': item['name'], 'object': str(obj), 'command': command, 'returncode': result.returncode}

    with ThreadPoolExecutor(max_workers=max(1, min(16, args.jobs))) as pool:
        results = list(pool.map(compile_one, sources))
    (output/'compile-results.json').write_text(json.dumps(results, indent=2)+'\n', encoding='utf-8')
    if any(item['returncode'] for item in results):
        raise RuntimeError('Compilation failed; inspect output logs')
    response = output/'objects.rsp'
    response.write_text('\n'.join('"'+item['object'].replace('\\', '/')+'"' for item in results)+'\n', encoding='utf-8')
    name = spec['artifact_name']
    elf, binary, map_path = (output/(name+suffix) for suffix in ('.elf', '.bin', '.map'))
    link = [gcc, '-o', str(elf), '@'+str(response), '-mcpu=cortex-m7', '-T'+str(linker),
            '--specs=nosys.specs', '-Wl,-Map='+str(map_path)]+spec['link_flags_after_map']
    result = subprocess.run(link, capture_output=True, text=True, encoding='utf-8', errors='replace')
    (output/'link.log').write_text(result.stdout+result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Link failed; inspect output/link.log')
    subprocess.run([tool('arm-none-eabi-objcopy'), '-O', 'binary', str(elf), str(binary)], check=True)
    program = subprocess.check_output([tool('arm-none-eabi-readelf'), '-lW', str(elf)], text=True)
    (output/'elf-program-headers.txt').write_text(program, encoding='utf-8')
    flash = []
    for line in program.splitlines():
        fields = line.split()
        if fields and fields[0] == 'LOAD':
            physical, size = int(fields[3], 16), int(fields[4], 16)
            if size and physical < 0x08100000 and physical+size > 0x080e0000:
                raise RuntimeError('Persistent Flash sector overlap')
            if size and 0x08000000 <= physical < 0x08100000:
                flash.append((physical, physical+size))
    if not flash or min(a for a,b in flash) != 0x08000000:
        raise RuntimeError('Application Flash origin mismatch')
    flash_end = max(b for a,b in flash)
    if flash_end >= 0x080e0000 or binary.stat().st_size != flash_end-0x08000000:
        raise RuntimeError('Application Flash extent mismatch')
    data = binary.read_bytes()
    if spec['version'].encode()+b'\0' not in data or spec['git_hash'].encode()+b'\0' not in data:
        raise RuntimeError('Embedded firmware identity mismatch')
    matches = sha(binary) == spec['expected_binary_sha256'] if spec.get('expected_binary_sha256') else None
    report = {'version': spec['version'], 'compiler': compiler, 'source_count': len(sources),
              'binary_matches_pinned_release': matches, 'flash_end_exclusive': hex(flash_end),
              'persistent_sector_in_elf': False, 'flashed': False,
              'files': {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size} for p in (binary,elf,map_path)},
              'elf_map_identity_limit': 'DWARF and map contain absolute build paths; only BIN is pinned byte-for-byte.'}
    (output/'build-report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if spec.get('expected_binary_sha256') is not None and not matches:
        raise RuntimeError('Rebuilt BIN differs from pinned release; output is NOT a verified replacement')


if __name__ == '__main__':
    main()
