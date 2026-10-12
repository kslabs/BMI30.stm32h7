#ifndef BMI30_RADAR_STORAGE_H
#define BMI30_RADAR_STORAGE_H
#include "gate.h"

/* Last two 2-KiB pages of STM32L431CB, excluded from the firmware linker.
 * Two independent records: commit is written last; the previous page stays
 * readable after interruption of an erase/program operation. */
#define GATE_FLASH_A 0x0801F000u
#define GATE_FLASH_B 0x0801F800u
void gate_storage_prepare(void);
bool gate_storage_load(void *context, gate_config_t *config, uint16_t *gap_mm);
bool gate_storage_save(void *context, const gate_config_t *config, uint16_t gap_mm);

#endif
