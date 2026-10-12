#include "storage.h"
#include "main.h"
#include <stddef.h>
#include <string.h>

#define MAGIC 0x584D4731u
#define COMMIT 0x434F4D4D4954584Dull
typedef struct {
    uint32_t magic, schema, sequence;
    gate_config_t config;
    uint16_t gap_mm, reserved;
    uint32_t crc;
    uint64_t commit;
} record_t;
_Static_assert(offsetof(record_t, commit) % 8 == 0, "Flash doubleword alignment");

void gate_storage_prepare(void)
{
    /* RM0394: writing 1 TOGGLES PEMPTY, so never write it unconditionally.
     * CubeProgrammer can leave this flag set after programming valid flash.
     * HAL treats it as an error, but FLASH_FLAG_ALL_ERRORS excludes it. */
    if (__HAL_FLASH_GET_FLAG(FLASH_FLAG_PEMPTY))
        __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_PEMPTY);
}

static uint32_t crc32(const void *data, size_t size)
{
    const uint8_t *p = data;
    uint32_t crc = UINT32_MAX;
    while (size--) {
        crc ^= *p++;
        for (unsigned i = 0; i < 8; i++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
static bool read_record(uint32_t address, record_t *r)
{
    memcpy(r, (const void *)address, sizeof(*r));
    return r->magic == MAGIC && r->schema == 1 && r->commit == COMMIT &&
        r->crc == crc32(r, offsetof(record_t, crc)) && gate_config_valid(&r->config) &&
        r->gap_mm >= r->config.gap_min_mm && r->gap_mm <= r->config.gap_max_mm;
}
static uint32_t latest(record_t *r)
{
    record_t a, b;
    bool va = read_record(GATE_FLASH_A, &a), vb = read_record(GATE_FLASH_B, &b);
    if (!va && !vb) return 0;
    if (vb && (!va || (int32_t)(b.sequence - a.sequence) > 0)) { *r = b; return GATE_FLASH_B; }
    *r = a; return GATE_FLASH_A;
}
bool gate_storage_load(void *context, gate_config_t *c, uint16_t *gap)
{
    (void)context;
    record_t r;
    if (!latest(&r)) return false;
    *c = r.config; *gap = r.gap_mm;
    return true;
}
bool gate_storage_save(void *context, const gate_config_t *c, uint16_t gap)
{
    (void)context;
    if (!gate_config_valid(c) || gap < c->gap_min_mm || gap > c->gap_max_mm) return false;
    record_t old, r;
    uint32_t previous = latest(&old);
    uint32_t target = previous == GATE_FLASH_A ? GATE_FLASH_B : GATE_FLASH_A;
    memset(&r, 0, sizeof(r));
    r.magic = MAGIC; r.schema = 1; r.sequence = previous ? old.sequence + 1 : 1;
    r.config = *c; r.gap_mm = gap;
    r.crc = crc32(&r, offsetof(record_t, crc)); r.commit = COMMIT;
    if (HAL_FLASH_Unlock() != HAL_OK) return false;
    gate_storage_prepare();
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_ALL_ERRORS);
    FLASH_EraseInitTypeDef erase = {.TypeErase = FLASH_TYPEERASE_PAGES, .Banks = FLASH_BANK_1,
                                    .Page = (target - FLASH_BASE) / FLASH_PAGE_SIZE, .NbPages = 1};
    uint32_t page_error;
    bool success = HAL_FLASHEx_Erase(&erase, &page_error) == HAL_OK;
    for (size_t i = 0; success && i < sizeof(r); i += 8) {
        uint64_t data;
        memcpy(&data, (const uint8_t *)&r + i, sizeof(data));
        success = HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD, target + i, data) == HAL_OK;
    }
    HAL_FLASH_Lock();
    record_t verify;
    return success && read_record(target, &verify) && verify.sequence == r.sequence;
}
