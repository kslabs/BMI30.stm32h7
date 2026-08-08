#ifndef BUILD_INFO_H
#define BUILD_INFO_H

#ifdef __cplusplus
extern "C" {
#endif

// Семантическая версия прошивки
#define FW_VERSION_MAJOR 1
#define FW_VERSION_MINOR 2
#define FW_VERSION_PATCH 16  // Debounced phase relation and resilient master-status cache

#define FW_VERSION_STR  "1.2.16"

// Опционально: можно переопределить через ключ компиляции -DGIT_HASH="..."
#ifndef GIT_HASH
#define GIT_HASH "nogit"
#endif

extern const char fw_version[];       // "1.0.0"
extern const char fw_build_date[];    // __DATE__
extern const char fw_build_time[];    // __TIME__
extern const char fw_build_full[];    // "FW 1.0.0 built <date> <time> (<hash>)"
extern const char fw_git_hash[];      // хеш (или nogit)

#ifdef __cplusplus
}
#endif

#endif // BUILD_INFO_H
