#include "build_info.h"
/* Автогенерируемый при сборке заголовок с актуальными датой/временем.
	Если отсутствует, используются __DATE__/__TIME__. */
#include "build_autogen.h"

/* Фолбэки, если build_autogen.h не создан перед сборкой */
#ifndef BUILD_AUTOGEN_DATE
#define BUILD_AUTOGEN_DATE __DATE__
#endif
#ifndef BUILD_AUTOGEN_TIME
#define BUILD_AUTOGEN_TIME __TIME__
#endif

const char fw_version[]    = FW_VERSION_STR;
const char fw_build_date[] = BUILD_AUTOGEN_DATE;
const char fw_build_time[] = BUILD_AUTOGEN_TIME;
const char fw_git_hash[]   = GIT_HASH;

const char fw_build_full[] =
#if defined(__GNUC__)
"FW " FW_VERSION_STR " (GCC) built " BUILD_AUTOGEN_DATE " " BUILD_AUTOGEN_TIME " (" GIT_HASH ")";
#else
"FW " FW_VERSION_STR " built " BUILD_AUTOGEN_DATE " " BUILD_AUTOGEN_TIME " (" GIT_HASH ")";
#endif