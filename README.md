# BMI30.stm32h7

Проект STM32 (STM32H723). Репозиторий хранит исходники, конфигурации CubeMX/CubeIDE и скрипты сборки/прошивки.

## Сборка (GNU Make)
```bash
make -C Debug all
```

Очистка:
```bash
make -C Debug clean
```

## Прошивка
### Вариант 1: STM32CubeProgrammer (рекомендуется)
Используется скрипт VS Code:
```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .vscode/build-and-flash.ps1
```

### Вариант 2: OpenOCD (альтернативно)
```bash
openocd -f interface/stlink.cfg -f target/stm32h7x.cfg -c "program Debug/BMI30.stm32h7.elf verify reset exit"
```

## Что хранится в репозитории
- Core/, Drivers/, Middlewares/, USB_DEVICE/ и связанные исходники
- CubeIDE/CubeMX файлы: .project, .cproject, .settings/, *.ioc
- Скрипты прошивки/отладки: flash.ps1, flash.bat, .vscode/*
- Линкерные скрипты: *.ld
- Документация проекта

## Что не хранится
- Артефакты сборки (Debug/*.o, *.elf, *.map, *.list, *.log и т.п.)
- Локальные логи/дампы

## Окружение
См. ENVIRONMENT.md

## Примечание про синхронизацию
Рекомендуется использовать Git (commit/PR) вместо облачных синхронизаторов.
