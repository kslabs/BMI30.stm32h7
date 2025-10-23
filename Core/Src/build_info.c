#include "build_info.h"

const char fw_version[]    = FW_VERSION_STR;
const char fw_build_date[] = __DATE__;
const char fw_build_time[] = __TIME__;
const char fw_git_hash[]   = GIT_HASH;

const char fw_build_full[] =
#if defined(__GNUC__)
"FW " FW_VERSION_STR " (GCC) built " __DATE__ " " __TIME__ " (" GIT_HASH ")";
#else
"FW " FW_VERSION_STR " built " __DATE__ " " __TIME__ " (" GIT_HASH ")";
#endif
