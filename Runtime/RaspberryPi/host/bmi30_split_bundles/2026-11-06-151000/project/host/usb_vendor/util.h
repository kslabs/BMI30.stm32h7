#pragma once
#include <stdint.h>
#include <time.h>
#include <sys/time.h>
static inline double now_sec(){ struct timeval tv; gettimeofday(&tv,NULL); return tv.tv_sec + tv.tv_usec*1e-6; }
