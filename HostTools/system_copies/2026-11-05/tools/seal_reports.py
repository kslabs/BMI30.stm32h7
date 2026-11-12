"""Root-only local sealing step, after both real eMMC and USB boots on both boards.

No remote imports or calls. Refuses missing/failed proofs and an existing package.
It copies an explicit allowlist; never copies plans, images, config or credentials.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import uuid

WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
DESTINATION = ROOT / 'HostTools/system_copies/2026-11-05'
IDENTITIES = {
    's07': {'host': 'BMI30-C9775FBAE', 'cm5': 'abdd2dbc9775fbae', 'serial': '25072933730021',
            'usb': '868b9fea', 'emmc': '58350b37', 'role': 'slave', 'id': 7},
    'm02': {'host': 'BMI30-E29805C7C', 'cm5': '490df72e29805c7c', 'serial': '25072913740186',
            'usb': 'd6e578d8', 'emmc': 'cd5a1133', 'role': 'master', 'id': 2},
}
TOOLS = ('clone_remote.py', 'test_clone_remote.py', 'check_source_runtime.py',
         'test_source_runtime.py', 'boot_control.py', 'test_boot_control.py', 'seal_reports.py')
ATTESTATIONS = ('independent-clone-review.json', 'source-checker-final-validation.json',
                'boot-control-local-validation.json', 'boot-controller-independent-review.json',
                'boot-controller-semantic-review.json')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Nonfinite JSON value: ' + value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def dumps(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def validate_board(board, documents):
    expected = IDENTITIES[board]
    copy = documents[board + '-copy-result.json']
    source = documents[board + '-source-runtime-verified.json']
    comparison = documents[board + '-config-comparison.json']
    require(copy.get('ok') is True and copy.get('verified_copy') is True and copy.get('copied') is True,
            board + ': copy is not verified')
    require(source.get('ok') is True and source.get('source_reverified') is True and source.get('usb_unmounted') is True,
            board + ': original eMMC runtime verification missing')
    for record in (copy, source):
        require(record.get('host') == expected['host'] and record.get('cm5') == expected['cm5'], board + ': wrong board')
        require(record.get('usb_serial') == expected['serial'], board + ': wrong USB')
    require(copy.get('network_profiles_personalized') is False, board + ': unexpected personalization')
    require(copy['root_partuuid'] == expected['usb'] + '-02' and copy['boot_partuuid'] == expected['usb'] + '-01',
            board + ': wrong copied USB partitions')
    require(copy['source_root_partuuid'] == expected['emmc'] + '-02' and copy['source_boot_partuuid'] == expected['emmc'] + '-01',
            board + ': wrong source eMMC partitions')
    require(source['copy_job_id'] == copy['job_id'], board + ': source proof belongs to another copy')
    require(source['copied_config_sha256'] == copy['source_config_sha256_at_final_sync'], board + ': copied config anchor changed')
    require(comparison.get('changed_paths') == [] and comparison.get('count') == 0 and comparison.get('snapshot_matches_result') is True,
            board + ': config comparison differs')
    proof = comparison['proof']
    require(proof.get('semantic_equal') is True and proof.get('snapshot_matches_result') is True,
            board + ': semantic proof is not positive')
    require(proof['job_id'] == copy['job_id'] and proof['host'] == expected['host'] and proof['cm5'] == expected['cm5'],
            board + ': semantic proof belongs to another board/copy')
    require(proof['target_root_partuuid'] == copy['root_partuuid'] and proof['target_root_uuid'] == copy['root_uuid']
            and proof['usb_serial'] == expected['serial'], board + ': semantic proof belongs to another target')
    require(proof['snapshot_byte_sha256'] == copy['source_config_sha256_at_final_sync']
            and proof['config_semantic_sha256'] == source['config_semantic_sha256'], board + ': semantic anchor mismatch')
    boots = {}
    for medium, mode in (('emmc', 1), ('usb', 4)):
        report = documents[board + '-' + medium + '-boot-verified.json']
        require(report.get('ok') is True and report.get('boot_tested') is True,
                board + ': missing actual ' + medium + ' boot')
        require(report['host'] == expected['host'] and report['cm5'] == expected['cm5'], board + ': boot on another CM5')
        require(report['boot_id'] != report['prior_boot_id'], board + ': boot ID did not change')
        selected = report['medium']
        prefix = expected[medium]
        devices = ('/dev/mmcblk0p2', '/dev/mmcblk0p1') if medium == 'emmc' else ('/dev/sda2', '/dev/sda1')
        require(selected['current'] == medium and selected['root'] == devices[0] and selected['boot'] == devices[1],
                board + ': root/boot medium mismatch')
        require(selected['root_partuuid'] == prefix + '-02' and selected['boot_partuuid'] == prefix + '-01',
                board + ': boot partition identity mismatch')
        require(selected['boot_mode'] == mode and selected['usb_serial'] == expected['serial']
                and selected['usb_size'] == 31457280000 and selected['inactive_medium_unmounted'] is True,
                board + ': incomplete physical medium proof')
        require(report['bootloader']['order'] == '0xf2614' and report['bootloader']['eeprom_write_performed'] is False,
                board + ': persistent boot configuration changed')
        require(report['role']['value'] == expected['role'] and report['role']['device_id'] == expected['id'], board + ': role changed')
        require(report['source_version'] == copy['source_version'] == '2026-11-04-185916', board + ': active source version changed')
        require(report['source_sha256'] == copy['source_active_hashes'], board + ': active source hashes differ')
        require(report['network_source_sha256'] == copy['network_source_hashes_at_final_sync'], board + ': network code differs')
        require(report['selection_env_sha256'] == copy['source_env_sha256_at_final_sync'], board + ': source selection differs')
        require(report['copied_config_sha256'] == copy['source_config_sha256_at_final_sync']
                and report['source_config_values_match_final_copy'] is True
                and report['config_semantic_sha256'] == source['config_semantic_sha256'], board + ': boot config values differ')
        require(report['mode'] == copy['final_runtime']['mode'], board + ': runtime mode changed')
        require(report['worker_results_start_end'][1] > report['worker_results_start_end'][0]
                and report['noise_reference_valid'] is True and report['portal_http'] == 200, board + ': runtime not ready')
        require(report['network']['group_fresh'] is True, board + ': group proof stale')
        boots[medium] = report
    require(boots['emmc']['boot_id'] != boots['usb']['boot_id'], board + ': same boot reused for two media')
    return {'identity': expected, 'copy': copy, 'source': source, 'comparison': comparison, 'boots': boots}


def load_and_validate():
    names = list(TOOLS) + list(ATTESTATIONS)
    for board in IDENTITIES:
        names += [board + suffix for suffix in ('-copy-result.json', '-source-runtime-verified.json',
                  '-config-comparison.json', '-emmc-boot-verified.json', '-usb-boot-verified.json')]
    raw = {}
    for name in names:
        path = WORK / name
        require(path.is_file() and not path.is_symlink(), 'Required proof/tool missing: ' + name)
        raw[name] = path.read_bytes()
    documents = {name: strict_json(data) for name, data in raw.items() if name.endswith('.json')}
    boards = {board: validate_board(board, documents) for board in IDENTITIES}
    clone = documents['independent-clone-review.json']
    require(clone['tests_run'] == 31 and clone['failures'] == clone['errors'] == clone['skipped'] == 0, 'Clone test attestation failed')
    require(clone['source']['sha256'] == digest(raw['clone_remote.py'])
            and clone['test_source']['sha256'] == digest(raw['test_clone_remote.py']), 'Clone code/test changed after attestation')
    checker = documents['source-checker-final-validation.json']
    require(checker['tests_run'] == 17 and checker['failures'] == checker['errors'] == checker['skipped'] == 0, 'Source-checker attestation failed')
    require(checker['source']['sha256'] == digest(raw['check_source_runtime.py'])
            and checker['tests']['sha256'] == digest(raw['test_source_runtime.py']), 'Source checker/test changed after attestation')
    boot = documents['boot-control-local-validation.json']
    require(boot['tests'] == boot['passed'] == 15 and boot['controller_sha256'] == digest(raw['boot_control.py'])
            and boot['test_sha256'] == digest(raw['test_boot_control.py']), 'Boot controller/test attestation mismatch')
    require(not documents['boot-controller-semantic-review.json']['remaining_blockers'], 'Boot review has blockers')
    require(documents['boot-controller-semantic-review.json']['source']['sha256'] == digest(raw['boot_control.py']), 'Final boot review is stale')
    provenance = []
    for board, bundle in boards.items():
        name = board + '-copy-result.json'
        current = raw[name]
        recorded = bundle['source']['provenance']['copy_result_sha256']
        alternative = current.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        require(digest(current) == recorded or digest(alternative) == recorded,
                board + ': source proof copy-result digest is not byte/newline-equivalent')
        provenance.append({'board': board, 'current_file_sha256': digest(current),
            'source_checker_recorded_sha256': recorded,
            'equivalence': 'exact bytes' if digest(current) == recorded else 'LF/CRLF only; JSON bytes otherwise identical'})
    # No output directory or files are created until every actual boot and attestation passed.
    return raw, documents, boards, provenance


def readme(boards):
    rows = []
    configs = []
    for board, bundle in boards.items():
        value = bundle['identity']; copy = bundle['copy']; usb = bundle['boots']['usb']
        rows.append(f"| {board.upper()} | {value['role']} {value['id']} | `{value['serial']}` | `{value['emmc']}-02` | `{value['usb']}-02` | PASS / PASS |")
        configs.append(f"- **{board.upper()}**: копия config SHA `{copy['source_config_sha256_at_final_sync']}`, "
            f"после USB-загрузки SHA `{usb['config_sha256']}`, строгий canonical SHA `{usb['config_semantic_sha256']}`.")
    return '''# Собственные копии eMMC → USB: S07 и M02, 05.11.2026

Обе копии созданы и проверены. На **каждой физической плате** подтверждена новая загрузка сначала с собственной eMMC, затем с собственной USB: новый boot ID, фактические root/boot, PARTUUID, serial флешки и режим загрузки DT. Последние сохранённые подтверждения показывают работу **обоих приборов с USB**; неактивная eMMC при этих USB-проверках не смонтирована.

**Из работающей с USB системы флешку извлекать нельзя.** Для загрузки со встроенной памяти штатно завершите работу и отключите питание, извлеките USB и включите питание. С подключённой флешкой обычная загрузка выбирает USB первой. Постоянный `BOOT_ORDER=0xf2614` и EEPROM сохранены; для проверки eMMC использовался одноразовый порядок `0xf41`, после чего фактический DT boot mode был 1 для eMMC и 4 для USB. Историческое `safe_to_remove=true` в исходных copy-result относится к размонтированному USB **до** тестовой загрузки и не является разрешением извлекать текущий загрузочный носитель.

| Плата | Роль STM32 | Serial её USB | eMMC root PARTUUID | USB root PARTUUID | Реальная загрузка eMMC / USB |
|---|---|---|---|---|---|
''' + '\n'.join(rows) + '''

У каждой USB также есть свой boot PARTUUID с окончанием `-01`. Флешки принадлежат указанным платам: настройки, калибровка, сетевые профили и доступы сохранены от своей eMMC. Перенос конфигурации S07 на M02, персонализация переносного образа и скрытие eMMC не выполнялись. Это копии на момент записи, **автоматической синхронизации между eMMC и USB нет**. Эти собственные копии не являются образами для переноса на другую плату.

## Что проверено

Полная файловая копия root с исключением живых виртуальных файловых систем, swap и каталога текущей операции; `/home/techaid/Documents/.tmp`, `/var/backups`, собственные SSH/идентификационные данные и завершённые guard-транзакции сохранены на носителях. В финальном проходе Core, портал и agent были временно остановлены; NetworkManager, SSH, network и access оставались работать. Проверены критические деревья, актуальные code/env/config и собственная идентичность; target fstab/cmdline изменены только на новые USB-разделы. Boot FAT активирован после проверки и синхронизации root, образы kernel опубликованы последними. Статические тесты не доказывают устойчивость к любому отключению питания во время записи.

После копирования отдельно подтверждена работа исходной eMMC. После каждого настоящего перезапуска подтверждены активная пара Core/Engine `2026-11-04-185916`, прежние базовые bundles, точные SHA четырёх detector-модулей и сетевого кода, selection env, собственная роль, режим 6 и усреднение 8/12, продвижение worker, готовность шума, HTTP 200, Wi-Fi/собственная AP и свежая группа из двух приборов. Исходные copy-result сохранены без исправления исторических флагов; более поздние source/boot-отчёты документируют последующие этапы.

Это подтверждение системной копии, загрузки и ограниченной проверки runtime. Оно не оценивает физическую чувствительность Casino на 35 см, выходы звука/света или улучшение ложных срабатываний; не является полной записью ADC с доказательством отсутствия потерь.

## Конфигурация: байты и значения

После обычного запуска Core сериализация JSON изменилась: raw SHA текущего config отличается от замороженных байтов копии. Независимая проверка прочитала точную USB-копию read-only и подтвердила **полное совпадение значений и типов**: изменённых JSON-путей 0, никакие поля не исключались. Строгая canonical-проверка отвергает duplicate keys, NaN/Infinity, смену типа числа/boolean и порядка списков. Это не утверждение о побайтном совпадении config после перезапуска.

''' + '\n'.join(configs) + '''

Два `config-comparison.json` сохраняют привязку к плате, job, USB serial, root UUID/PARTUUID и исходному byte SHA; полное содержимое config здесь отсутствует. У копий локальных copy-result JSON также имеется только LF/CRLF-различие с прежними документальными SHA source-checker; эквивалентность исходных байтов с заменой окончания строк явно записана в манифесте. Сами отчёты при упаковке не переписываются.

## Состав и воспроизведение доказательств

В `s07/` и `m02/` находятся побайтные копии пяти окончательных отчётов: copy, source-runtime, canonical comparison, реальная eMMC boot и реальная USB boot. Промежуточные неудачные попытки не представлены как итоговые PASS. В `validation/` — независимые аттестации **31** теста clone worker, **17** тестов source-checker и **15** тестов boot controller, а также два статических boot-review с исходными пределами доказательств. Локальные CPU-тесты и аппаратные проверки, выполненные основным агентом, разделены.

В `tools/` — точные проверенные исходники и тесты для аудита, включая этот упаковщик. Fixtures и SSH-transport из рабочего каталога не включены; эти копии фиксируют реализацию, а не самостоятельный интерфейс управления устройствами. В пакет не входят Linux images, планы удалённых заданий, приватные critical fingerprints, содержимое config/env, пароли и резервные архивы.

`manifest.json` связывает отчёты, источники и SHA; `SHA256SUMS` проверяет все файлы, включая JSON-манифест, кроме самого checksum-файла. Пакет создан только после успешной проверки всех четырёх фактических загрузок и отказывается перезаписывать существующий каталог.

Первичная документация Raspberry Pi, использованная при статическом boot-review: [BOOT_ORDER](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#BOOT_ORDER) и [одноразовый set_reboot_order](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/config_txt/boot.adoc#set_reboot_order). Фактические boot mode и носители подтверждены приложенными отчётами.
'''


def main():
    raw, documents, boards, newline_provenance = load_and_validate()
    require(not DESTINATION.exists(), 'Sealed destination already exists; refuse overwrite')
    # Freeze source bytes and ensure no producer changed an input during validation.
    require(all((WORK / name).read_bytes() == value for name, value in raw.items()), 'Input changed while validating')
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    staging = DESTINATION.with_name('.' + DESTINATION.name + '.sealing-' + uuid.uuid4().hex)
    staging.mkdir()
    mapping = {}
    for board in IDENTITIES:
        for suffix in ('copy-result', 'source-runtime-verified', 'config-comparison', 'emmc-boot-verified', 'usb-boot-verified'):
            mapping[board + '/' + suffix + '.json'] = board + '-' + suffix + '.json'
    mapping.update({'validation/' + name: name for name in ATTESTATIONS})
    mapping.update({'tools/' + name: name for name in TOOLS})
    for relative, source in mapping.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw[source])
    (staging / 'README_RU.md').write_bytes(readme(boards).encode('utf-8'))
    entries = [{'path': p.relative_to(staging).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p.read_bytes())}
               for p in sorted(staging.rglob('*')) if p.is_file()]
    manifest = {'format': 'bmi30-own-emmc-usb-copies-evidence-v1', 'client_date': '2026-11-05',
        'all_four_actual_boots_verified': True, 'active_source_version': '2026-11-04-185916',
        'persistent_boot_order': '0xf2614', 'eeprom_write_performed': False,
        'last_verified_medium': {'s07': 'usb', 'm02': 'usb'}, 'current_safe_to_remove_claim': False,
        'point_in_time_own_board_copies': True, 'automatic_sync': False, 'cross_board_adoption': False,
        'tests': {'clone': 31, 'source_checker': 17, 'boot_controller': 15},
        'copy_result_newline_provenance': newline_provenance,
        'input_provenance': {relative: {'source': '.tmp/emmc-usb-copies-20261105/' + source,
                                      'sha256': digest(raw[source]), 'copy_mode': 'byte_identical'}
                             for relative, source in mapping.items()},
        'boards': {board: {'identity': bundle['identity'],
            'copy_job_id': bundle['copy']['job_id'], 'source_config_snapshot_sha256': bundle['copy']['source_config_sha256_at_final_sync'],
            'config_semantic_sha256': bundle['source']['config_semantic_sha256'],
            'source_values_and_types_match_copy': True,
            'emmc_boot_id': bundle['boots']['emmc']['boot_id'], 'usb_boot_id': bundle['boots']['usb']['boot_id'],
            'source_active_hashes': bundle['copy']['source_active_hashes']}
                   for board, bundle in boards.items()},
        'excluded': ['full Linux images', 'private config/env contents', 'critical fingerprint files',
                     'credentials', 'backup archives', 'remote job plans', 'SSH transport', 'failed boot attempts'],
        'physical_tag_performance_claim': False, 'exact_adc_zero_loss_claim': False,
        'checksum_convention': 'files excludes manifest.json/SHA256SUMS; SHA256SUMS covers all files except itself',
        'files': entries}
    (staging / 'manifest.json').write_bytes(dumps(manifest))
    sums = [digest(p.read_bytes()) + '  ' + p.relative_to(staging).as_posix()
            for p in sorted(staging.rglob('*')) if p.is_file()]
    (staging / 'SHA256SUMS').write_bytes(('\n'.join(sums) + '\n').encode())
    for entry in manifest['files']:
        value = (staging / entry['path']).read_bytes()
        require(len(value) == entry['bytes'] and digest(value) == entry['sha256'], 'Manifest verification failed')
    for line in (staging / 'SHA256SUMS').read_text().splitlines():
        expected, relative = line.split('  ', 1)
        require(digest((staging / relative).read_bytes()) == expected, 'Checksum verification failed')
    require(all((WORK / name).read_bytes() == value for name, value in raw.items()), 'Input changed while packaging')
    require(not DESTINATION.exists(), 'Destination appeared during packaging')
    staging.rename(DESTINATION)
    print(json.dumps({'ok': True, 'package': str(DESTINATION), 'files': len(sums) + 1,
        'manifest_sha256': digest((DESTINATION / 'manifest.json').read_bytes()),
        'SHA256SUMS_sha256': digest((DESTINATION / 'SHA256SUMS').read_bytes()),
        'all_four_actual_boots_verified': True}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        print('NOT SEALED: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
