#include "crc16.h"
// CRC-16/CCITT-FALSE poly=0x1021 init=0xFFFF no refin/refout
uint16_t crc16_ccitt_false(const uint8_t *data, int len, uint16_t init) {
    uint16_t crc = init; // 0xFFFF
    for (int i=0;i<len;i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b=0;b<8;b++) {
            if (crc & 0x8000) crc = (crc << 1) ^ 0x1021;
            else crc <<= 1;
        }
    }
    return crc;
}
