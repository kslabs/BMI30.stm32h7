#ifndef TEST_MAIN_H
#define TEST_MAIN_H
#include <stdint.h>
#define FLASH_BASE 0x08000000u
#define FLASH_PAGE_SIZE 2048u
#define FLASH_TYPEERASE_PAGES 2
#define FLASH_TYPEPROGRAM_DOUBLEWORD 1
#define FLASH_BANK_1 1
#define FLASH_FLAG_ALL_ERRORS 0xFFFF
#define FLASH_FLAG_PEMPTY 0x20000u
#define HAL_OK 0
#define HAL_ERROR 1
extern uint32_t test_flash_status;
void test_flash_clear(uint32_t flags);
#define __HAL_FLASH_GET_FLAG(flags) (test_flash_status & (flags))
#define __HAL_FLASH_CLEAR_FLAG(flags) test_flash_clear(flags)
typedef struct { uint32_t TypeErase, Banks, Page, NbPages; } FLASH_EraseInitTypeDef;
int HAL_FLASH_Unlock(void);
int HAL_FLASH_Lock(void);
int HAL_FLASHEx_Erase(FLASH_EraseInitTypeDef *erase, uint32_t *error);
int HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t data);
#endif
