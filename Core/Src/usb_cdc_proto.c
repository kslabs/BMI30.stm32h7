#include "usb_cdc_proto.h"
#include "adc_stream.h"
#include "main.h"
#include "build_info.h"
#include "stm32h7xx_ll_adc.h"
#include <string.h>
#include <stdbool.h>
#include <stdio.h>
#include <stddef.h>  // для offsetof

// === Температурный датчик ===
static ADC_HandleTypeDef s_temp_adc3;
static uint8_t s_temp_adc3_ready = 0u;
static uint8_t s_temp_have_last = 0u;
static int16_t s_temp_last_valid_c = 0;
static uint32_t s_temp_adc3_last_channel = 0xFFFFFFFFu;

static uint16_t temp_u32_to_u16_sat(uint32_t value)
{
    return (value > 0xFFFFu) ? 0xFFFFu : (uint16_t)value;
}

void temp_sensor_init(void)
{
    ADC_ChannelConfTypeDef sConfig;

    if(s_temp_adc3_ready != 0u){
        return;
    }

    memset(&s_temp_adc3, 0, sizeof(s_temp_adc3));
    memset(&sConfig, 0, sizeof(sConfig));

    s_temp_adc3.Instance = ADC3;
    s_temp_adc3.Init.ClockPrescaler = ADC_CLOCK_ASYNC_DIV1;
    s_temp_adc3.Init.Resolution = ADC_RESOLUTION_12B;
    s_temp_adc3.Init.ScanConvMode = ADC_SCAN_DISABLE;
    s_temp_adc3.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
    s_temp_adc3.Init.LowPowerAutoWait = DISABLE;
    s_temp_adc3.Init.ContinuousConvMode = DISABLE;
    s_temp_adc3.Init.NbrOfConversion = 1;
    s_temp_adc3.Init.DiscontinuousConvMode = DISABLE;
    s_temp_adc3.Init.ExternalTrigConv = ADC_SOFTWARE_START;
    s_temp_adc3.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_NONE;
    s_temp_adc3.Init.ConversionDataManagement = ADC_CONVERSIONDATA_DR;
#if defined(ADC_VER_V5_V90)
    s_temp_adc3.Init.SamplingMode = ADC_SAMPLING_MODE_NORMAL;
    s_temp_adc3.Init.DMAContinuousRequests = DISABLE;
#endif
    s_temp_adc3.Init.Overrun = ADC_OVR_DATA_OVERWRITTEN;
    s_temp_adc3.Init.LeftBitShift = ADC_LEFTBITSHIFT_NONE;
    s_temp_adc3.Init.OversamplingMode = DISABLE;
    s_temp_adc3.Init.Oversampling.Ratio = 1;

    if(HAL_ADC_Init(&s_temp_adc3) != HAL_OK){
        return;
    }
    LL_ADC_SetCommonPathInternalCh(__LL_ADC_COMMON_INSTANCE(s_temp_adc3.Instance),
                                   LL_ADC_PATH_INTERNAL_TEMPSENSOR |
                                   LL_ADC_PATH_INTERNAL_VREFINT |
                                   LL_ADC_PATH_INTERNAL_VBAT);

    if(HAL_ADCEx_Calibration_Start(&s_temp_adc3, ADC_CALIB_OFFSET, ADC_SINGLE_ENDED) != HAL_OK){
        (void)HAL_ADC_DeInit(&s_temp_adc3);
        return;
    }

    sConfig.Channel = ADC_CHANNEL_TEMPSENSOR;
    sConfig.Rank = ADC_REGULAR_RANK_1;
    sConfig.SamplingTime = ADC_SAMPLETIME_810CYCLES_5;
    sConfig.SingleDiff = ADC_SINGLE_ENDED;
    sConfig.OffsetNumber = ADC_OFFSET_NONE;
    sConfig.Offset = 0;
    sConfig.OffsetSignedSaturation = DISABLE;

    if(HAL_ADC_ConfigChannel(&s_temp_adc3, &sConfig) != HAL_OK){
        (void)HAL_ADC_DeInit(&s_temp_adc3);
        return;
    }
    s_temp_adc3_last_channel = ADC_CHANNEL_TEMPSENSOR;

    HAL_Delay(1);
    s_temp_adc3_ready = 1u;
}

static uint8_t temp_adc3_config_channel(uint32_t channel)
{
    ADC_ChannelConfTypeDef sConfig;

    if(s_temp_adc3_last_channel == channel){
        return 1u;
    }

    memset(&sConfig, 0, sizeof(sConfig));
    sConfig.Channel = channel;
    sConfig.Rank = ADC_REGULAR_RANK_1;
    sConfig.SamplingTime = ADC_SAMPLETIME_810CYCLES_5;
    sConfig.SingleDiff = ADC_SINGLE_ENDED;
    sConfig.OffsetNumber = ADC_OFFSET_NONE;
    sConfig.Offset = 0;
    sConfig.OffsetSignedSaturation = DISABLE;

    if(HAL_ADC_ConfigChannel(&s_temp_adc3, &sConfig) != HAL_OK){
        s_temp_adc3_last_channel = 0xFFFFFFFFu;
        return 0u;
    }
    s_temp_adc3_last_channel = channel;
    return 1u;
}

static uint8_t temp_sensor_read_raw_once(uint32_t channel, uint16_t *raw)
{
    uint32_t value;

    if(raw == NULL){
        return 0u;
    }
    if(s_temp_adc3_ready == 0u){
        temp_sensor_init();
    }
    if(s_temp_adc3_ready == 0u){
        return 0u;
    }
    if(!temp_adc3_config_channel(channel)){
        return 0u;
    }

    if(HAL_ADC_Start(&s_temp_adc3) != HAL_OK){
        return 0u;
    }
    if(HAL_ADC_PollForConversion(&s_temp_adc3, 2) != HAL_OK){
        (void)HAL_ADC_Stop(&s_temp_adc3);
        return 0u;
    }

    value = HAL_ADC_GetValue(&s_temp_adc3);
    (void)HAL_ADC_Stop(&s_temp_adc3);
    *raw = (uint16_t)value;
    return 1u;
}

// Получить текущую температуру кристалла в градусах Цельсия
int16_t temp_sensor_read_celsius(void)
{
    uint16_t raw = 0u;
    int32_t temp_c;

    if(!temp_sensor_read_raw_once(ADC_CHANNEL_TEMPSENSOR, &raw)){
        return s_temp_have_last ? s_temp_last_valid_c : 0;
    }

    temp_c = __HAL_ADC_CALC_TEMPERATURE(TEMPSENSOR_CAL_VREFANALOG, raw, ADC_RESOLUTION_12B);
    if(temp_c < -40){
        temp_c = -40;
    } else if(temp_c > 130){
        temp_c = 130;
    }

    s_temp_last_valid_c = (int16_t)temp_c;
    s_temp_have_last = 1u;
    return s_temp_last_valid_c;
}

uint8_t mcu_internal_adc_read(mcu_internal_adc_v1_t *out)
{
    uint16_t raw_temp = 0u;
    uint16_t raw_vrefint = 0u;
    uint16_t raw_vbat = 0u;
    uint32_t vdda_mv = 0u;
    uint32_t vbat_mv = 0u;
    uint32_t temp_vref_mv = TEMPSENSOR_CAL_VREFANALOG;
    int32_t temp_c = 0;
    uint8_t flags = 0u;

    if(out == NULL){
        return 0u;
    }
    memset(out, 0, sizeof(*out));
    out->version = 1u;

    if(temp_sensor_read_raw_once(ADC_CHANNEL_VREFINT, &raw_vrefint) && raw_vrefint != 0u){
        flags |= 0x02u;
        vdda_mv = __HAL_ADC_CALC_VREFANALOG_VOLTAGE(raw_vrefint, ADC_RESOLUTION_12B);
        temp_vref_mv = vdda_mv;
    }

    if(temp_sensor_read_raw_once(ADC_CHANNEL_TEMPSENSOR, &raw_temp)){
        flags |= 0x01u;
        temp_c = __HAL_ADC_CALC_TEMPERATURE(temp_vref_mv, raw_temp, ADC_RESOLUTION_12B);
        if(temp_c < -40){
            temp_c = -40;
        } else if(temp_c > 130){
            temp_c = 130;
        }
        s_temp_last_valid_c = (int16_t)temp_c;
        s_temp_have_last = 1u;
    }

    if((vdda_mv != 0u) && temp_sensor_read_raw_once(ADC_CHANNEL_VBAT, &raw_vbat)){
        uint32_t vbat_div_mv = __HAL_ADC_CALC_DATA_TO_VOLTAGE(vdda_mv, raw_vbat, ADC_RESOLUTION_12B);
        vbat_mv = vbat_div_mv * 4u;
        if(raw_vbat != 0u){
            flags |= 0x04u;
        }
    }

    out->flags = flags;
    out->temp_c = (flags & 0x01u) ? s_temp_last_valid_c : 0;
    out->vdda_mv = temp_u32_to_u16_sat(vdda_mv);
    out->vbat_mv = temp_u32_to_u16_sat(vbat_mv);
    out->raw_temp = raw_temp;
    out->raw_vrefint = raw_vrefint;
    out->raw_vbat = raw_vbat;
    return (flags != 0u) ? 1u : 0u;
}

// --- Новая секция: глобальные счётчики по спецификации ---
static uint32_t g_pair_seq = 0;              // seq стереопары (оба кадра делят одно значение)
static uint16_t g_locked_samples = 0;        // cur_samples_per_frame (0 пока не зафиксирован)
static uint32_t g_sent_adc0 = 0;             // отправлено кадров ADC0
static uint32_t g_sent_adc1 = 0;             // отправлено кадров ADC1
static uint32_t g_dbg_partial_frame_abort = 0; // кадров отброшено до фиксации размера
static uint32_t g_dbg_size_mismatch = 0;     // кадров с неправильным размером после фиксации
static uint32_t g_dbg_tx_cplt = 0;           // завершений передачи (низкоуровневый колбэк)
static uint32_t g_test_frames = 0;           // отправлено тестовых кадров

// Переопределяем weak-хук из adc_stream.c: уведомление о приходе новых кадров DMA
// ВНИМАНИЕ: в проекте активен USB Vendor путь (usb_vendor_app.c) с собственной реализацией
// adc_stream_on_new_frames(). Чтобы избежать конфликта, локальный хук здесь НЕ определяется.
static volatile uint8_t g_new_frames_flag = 0;
static volatile uint8_t g_service_lock = 0; // защита от реентрантности

// Прототип локального сервиса
static void usb_stream_service(void);

void usb_stream_on_tx_complete(void) { g_dbg_tx_cplt++; usb_stream_service(); }

// Статусная структура (v1) согласно USBprotocol.txt
#pragma pack(push,1)
typedef struct {
    char     sig[4];
    uint8_t  version;
    uint8_t  reserved0;
    uint16_t cur_samples;
    uint16_t frame_bytes;
    uint16_t test_frames;
    uint32_t produced_seq;    // g_pair_seq (пар полностью сформировано)
    uint32_t sent0;
    uint32_t sent1;
    uint32_t dbg_tx_cplt;
    uint32_t dbg_partial_frame_abort;
    uint32_t dbg_size_mismatch;
    uint32_t dma_done0;  // из debug adc_stream
    uint32_t dma_done1;
    uint32_t frame_wr_seq_copy;
    uint16_t flags_runtime; // bit0=streaming, bit1=size_locked
    uint16_t reserved1;
} vendor_status_v1_t;
#pragma pack(pop)

// Внешняя низкоуровневая отправка
extern bool usb_cdc_ll_write(const uint8_t *data, size_t len);

// Локальная функция отправки статусного пакета
void usb_stream_send_status(void) {
    vendor_status_v1_t st; memset(&st,0,sizeof(st));
    memcpy(st.sig, "STAT", 4);
    st.version = 1;
    st.cur_samples = g_locked_samples;
    uint16_t ws = g_locked_samples ? (uint16_t)(32 + 2u * g_locked_samples) : 0;
    st.frame_bytes = ws;
    st.test_frames = (uint16_t)g_test_frames;
    st.produced_seq = g_pair_seq;
    st.sent0 = g_sent_adc0; st.sent1 = g_sent_adc1;
    st.dbg_tx_cplt = g_dbg_tx_cplt;
    st.dbg_partial_frame_abort = g_dbg_partial_frame_abort;
    st.dbg_size_mismatch = g_dbg_size_mismatch;
    adc_stream_debug_t dbg; adc_stream_get_debug(&dbg);
    st.dma_done0 = dbg.dma_full0; st.dma_done1 = dbg.dma_full1;
    st.frame_wr_seq_copy = dbg.frame_wr_seq;
    st.flags_runtime = (usb_stream_cfg()->streaming?1u:0u) | (g_locked_samples?2u:0u);
    usb_cdc_ll_write((uint8_t*)&st, sizeof(st));
}

// Тестовый кадр (8 сэмплов, флаги TEST+ADC0, не влияет на фиксацию)
void usb_stream_send_test_frame(void) {
    uint16_t pattern[8]; for (uint16_t i=0;i<8;i++) pattern[i]=i;
    vendor_frame_hdr_t hdr; memset(&hdr,0,sizeof(hdr));
    hdr.magic = 0xA55A; hdr.version = 1; hdr.flags = (uint8_t)(VFLAG_TEST | VFLAG_ADC0);
    hdr.seq = 0; // seq=0 для тестового (не относится к рабочим парам)
    hdr.timestamp = HAL_GetTick();
    hdr.total_samples = 8;
    // CRC
    hdr.flags |= VFLAG_CRC;
    hdr.crc16 = 0;
    uint8_t buf[32 + 8*2 + 64]; // header + payload + возможный паддинг
    memcpy(buf, &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), pattern, sizeof(pattern));
    // CRC16 по 30 байтам заголовка + payload
    uint16_t crc = 0xFFFFu; const uint8_t* p = buf; size_t len = 30 + sizeof(pattern);
    for (size_t i=0;i<len;i++){ crc ^= (uint16_t)p[i] << 8; for (int b=0;b<8;b++){ crc = (crc & 0x8000)? (uint16_t)((crc<<1)^0x1021):(uint16_t)(crc<<1); } }
    ((vendor_frame_hdr_t*)buf)->crc16 = crc;
    // Паддинг до 64
    size_t total = sizeof(hdr) + sizeof(pattern);
    size_t pad = (64 - (total & 63u)) & 63u; if (pad) memset(buf+total,0,pad), total+=pad;
    usb_cdc_ll_write(buf, total);
    g_test_frames++;
}

// --- Отправка рабочих кадров ---
// Состояние текущего извлечённого кадра из FIFO (общий буфер для обоих ADC)
static uint16_t *s_frame_ch0 = NULL; // ADC1 буфер (логически ADC0)
static uint16_t *s_frame_ch1 = NULL; // ADC2 буфер (логически ADC1)
static uint16_t  s_frame_samples = 0; // полное количество выборок
static uint32_t  s_frame_seq = 0;     // seq текущего кадра (для получения parity)
static uint32_t  s_frame_ring_seq = 0; // ring buffer seq из adc_stream (для правильного parity из s_buffer_parity[])
static uint8_t   s_next_channel_to_send = 0; // 0 -> отправим ADC0, 1 -> ADC1
static uint8_t   s_frame_active = 0;

// CRC16 helper
static uint16_t crc16_buf(const uint8_t* data, size_t len){ uint16_t crc=0xFFFFu; for(size_t i=0;i<len;i++){ crc^=(uint16_t)data[i]<<8; for(int b=0;b<8;b++){ if(crc&0x8000) crc=(uint16_t)((crc<<1)^0x1021); else crc=(uint16_t)(crc<<1);} } return crc; }

// Попытка отправить один USB кадр (один канал). Возврат 1 если отправлено.
static uint8_t try_send_one_adc_frame(void){
    if (!s_frame_active) return 0;
    uint8_t ch = s_next_channel_to_send; // 0 или 1
    
    // Каждый канал использует СВОЙ seq счётчик для parity
    static uint32_t s_frame_seq_ch0 = 0;
    static uint32_t s_frame_seq_ch1 = 0;
    uint32_t current_seq = (ch == 0) ? s_frame_seq_ch0++ : s_frame_seq_ch1++;
    
    // Формируем заголовок
    vendor_frame_hdr_t hdr; memset(&hdr,0,sizeof(hdr));
    hdr.magic = 0xA55A; hdr.version = 1; hdr.flags = (ch==0)? VFLAG_ADC0 : VFLAG_ADC1;
    // В протоколе v1 seq = отдельный счётчик кадров per ADC (по нему на хосте можно делать even/odd)
    hdr.seq = current_seq;
    hdr.timestamp = HAL_GetTick();
    hdr.total_samples = s_frame_samples; // уже проверено / зафиксировано
    hdr.zone_count = 0; // пока не используем зоны
    hdr.reserved = 0;
    // ВАЖНО: записываем parity из adc_stream (PA2 GPIO state) в reserved2
    // Используем s_frame_ring_seq (ring buffer index), а не current_seq (per-ADC counter)!
    hdr.reserved2 = (uint16_t)adc_get_buffer_parity(s_frame_ring_seq);

    // CRC присутствует всегда для рабочих кадров
    hdr.flags |= VFLAG_CRC;
    hdr.crc16 = 0;
    
    size_t payload_bytes = (size_t)s_frame_samples * 2u;
    size_t max_needed = sizeof(hdr) + payload_bytes + 64; // +паддинг
    static uint8_t txbuf[4096]; // с запасом
    if (max_needed > sizeof(txbuf)) return 0; // слишком большой (не должен происходить)
    
    memcpy(txbuf, &hdr, sizeof(hdr));
    
    const uint16_t *src = (ch==0)? s_frame_ch0 : s_frame_ch1;
    memcpy(txbuf + sizeof(hdr), src, payload_bytes);
    
    // CRC только по payload (согласно текущему хост-парсеру)
    uint16_t crc = crc16_buf(txbuf + sizeof(hdr), payload_bytes);
    
    // Записываем CRC напрямую в байты (offset 30-31)
    txbuf[30] = (uint8_t)(crc & 0xFF);
    txbuf[31] = (uint8_t)((crc >> 8) & 0xFF);
    size_t total = sizeof(hdr) + payload_bytes;
    size_t pad = (64 - (total & 63u)) & 63u; if (pad){ memset(txbuf+total,0,pad); total += pad; }
    if (!usb_cdc_ll_write(txbuf, total)) return 0; // endpoint занят
    // Учёт
    if (ch==0) g_sent_adc0++; else g_sent_adc1++;
    // s_frame_seq уже увеличен выше (line 119: current_seq = s_frame_seq++)
    // Переключение или завершение пары
    if (s_next_channel_to_send == 0){ s_next_channel_to_send = 1; }
    else { // пара завершена
        s_frame_active = 0; s_next_channel_to_send = 0; g_pair_seq++; }
    return 1;
}

// Сервис автоматической отправки (ограниченные итерации, без зависимости от main loop)
static void usb_stream_service(void) {
    if (!usb_stream_cfg()->streaming) return;
    if (g_service_lock) return; // избегаем вложенности
    g_service_lock = 1;
    // Ограничим количество попыток за одно обращение, чтобы ISR были короткими
    for (uint8_t i=0; i<4; ++i) {
        if (!usb_stream_try_send_frame()) break; // либо нечего, либо endpoint занят
    }
    g_service_lock = 0;
}

uint8_t usb_stream_try_send_frame(void) {
    if (!usb_stream_cfg()->streaming) return 0;
    // Если нет активного кадра — попробуем взять новый из FIFO
    if (!s_frame_active){
        uint16_t *c0=NULL,*c1=NULL; uint16_t samples=0;
        uint32_t pair_seq = 0;
        // КРИТИЧНО: используем ПАРНЫЙ API, чтобы seq был в том же домене, что и s_frame_buffer_idx[]
        if (!adc_get_frame_pair(&c0, &c1, &samples, &pair_seq)) return 0;
        s_frame_seq = pair_seq;
        s_frame_ring_seq = pair_seq;
        
        // Фиксация размера
        if (g_locked_samples == 0){
            g_locked_samples = samples; // фиксируем
        } else if (samples != g_locked_samples){
            // Несоответствие размера — отбрасываем кадр целиком
            g_dbg_size_mismatch++;
            return 1; // «формально обработали» чтобы попробовать следующий
        }
        // Подготовка активного
        s_frame_ch0 = c0; s_frame_ch1 = c1; s_frame_samples = samples; s_frame_active = 1; s_next_channel_to_send = 0;
    }
    // Пытаемся отправить следующий канал кадра
    if (try_send_one_adc_frame()) return 1;
    return 0;
}

void usb_stream_poll(void) {
    // Оставлено для совместимости; основная логика теперь в usb_stream_service()
    if (g_new_frames_flag) { g_new_frames_flag = 0; usb_stream_service(); }
}

// Глобальная конфигурация потока (доступ через usb_stream_cfg())
static usb_stream_cfg_t g_cfg = { .streaming = 0, .full_mode = 1, .profile_id = 0, .roi_offset_us = 0, .roi_length_us = 0, .seq_adc = {0,0} };
usb_stream_cfg_t* usb_stream_cfg(void) { return &g_cfg; }

// ACK/NACK helpers
static void stream_send_ack(uint8_t cmd) { uint8_t pkt[2] = { RSP_ACK, cmd }; usb_cdc_ll_write(pkt, sizeof(pkt)); }
void stream_send_ack_param(uint8_t cmd, uint8_t param) { uint8_t pkt[3] = { RSP_ACK, cmd, param }; usb_cdc_ll_write(pkt, sizeof(pkt)); }
static void stream_send_nack(uint8_t cmd, uint8_t code) { uint8_t pkt[3] = { RSP_NACK, cmd, code }; usb_cdc_ll_write(pkt, sizeof(pkt)); }

// --- Команды --- (переработанные согласно спецификации)
void usb_stream_on_rx_bytes(const uint8_t* data, size_t len) {
    if (!data || !len) return;
    size_t i = 0;
    while (i < len) {
        uint8_t cmd = data[i++];
        switch (cmd) {
            case CMD_PING: { stream_send_ack(cmd); break; }
            case CMD_START_STREAM: {
                // Сброс состояния
                g_pair_seq = 1; // начинаем с 1, тестовый кадр seq=0
                g_sent_adc0=g_sent_adc1=0; g_locked_samples=0;
                g_dbg_partial_frame_abort=0; g_dbg_size_mismatch=0; s_frame_active=0; s_next_channel_to_send=0;
                usb_stream_cfg()->streaming = 1;
                
                // ДИАГНОСТИКА: проверяем состояние DMA и ADC В МОМЕНТ START
                printf("[START_DIAG] ADC1->CR=0x%08lX (ADEN=%lu ADSTART=%lu)\r\n",
                       (unsigned long)ADC1->CR,
                       (unsigned long)((ADC1->CR >> 0) & 1),
                       (unsigned long)((ADC1->CR >> 2) & 1));
                printf("[START_DIAG] DMA1_Stream0->CR=0x%08lX (EN=%lu TCIE=%lu) NDTR=%lu\r\n",
                       (unsigned long)DMA1_Stream0->CR,
                       (unsigned long)((DMA1_Stream0->CR >> 0) & 1),
                       (unsigned long)((DMA1_Stream0->CR >> 4) & 1),
                       (unsigned long)DMA1_Stream0->NDTR);
                printf("[START_DIAG] DMA1_Stream0: M0AR=0x%08lX PAR=0x%08lX\r\n",
                       (unsigned long)DMA1_Stream0->M0AR,
                       (unsigned long)DMA1_Stream0->PAR);
                
                usb_stream_send_test_frame();
                stream_send_ack(cmd);
                // Попытка немедленной передачи если уже есть буферы
                usb_stream_service();
                break; }
            case CMD_STOP_STREAM: {
                usb_stream_cfg()->streaming = 0; s_frame_active=0; // остановка
                stream_send_ack(cmd);
                usb_stream_send_status();
                break; }
            case CMD_GET_STATUS: {
                usb_stream_send_status(); break; }
            case CMD_SET_FULL_MODE: {
                if (i>=len){ stream_send_nack(cmd,1); break; }
                uint8_t m = data[i++]; usb_stream_cfg()->full_mode = m?1:0; stream_send_ack_param(cmd, usb_stream_cfg()->full_mode); break; }
            case CMD_SET_PROFILE: {
                if (i>=len){ stream_send_nack(cmd,1); break; }
                uint8_t p = data[i++]; if (adc_stream_set_profile(p)!=0){ stream_send_nack(cmd,2); break; }
                usb_stream_cfg()->profile_id = p; stream_send_ack_param(cmd,p); break; }
            case CMD_SET_ROI_US: {
                if (i+8>len){ stream_send_nack(cmd,1); i=len; break; }
                uint32_t off_us,len_us; memcpy(&off_us,&data[i],4); memcpy(&len_us,&data[i+4],4); i+=8;
                usb_stream_cfg()->roi_offset_us = off_us; usb_stream_cfg()->roi_length_us = len_us; stream_send_ack(cmd); break; }
            case CMD_GET_TEMP: {
                // Получить температуру кристалла
                // Ответ: RSP_ACK + cmd + 2 байта температуры (int16_t LE)
                int16_t temp_c = temp_sensor_read_celsius();
                uint8_t pkt[4] = { RSP_ACK, cmd, (uint8_t)(temp_c & 0xFF), (uint8_t)((temp_c >> 8) & 0xFF) };
                usb_cdc_ll_write(pkt, sizeof(pkt)); break; }
            case CMD_GET_VERSION: {
                // Получить версию прошивки
                // Ответ: RSP_ACK + cmd + версия_строка (null-terminated) или версия в виде 4 байт (major.minor.patch.build)
                // Отправляем версию в формате: [RSP_ACK, CMD_GET_VERSION, major, minor, patch, build]
                uint8_t pkt[6] = { 
                    RSP_ACK, 
                    cmd, 
                    FW_VERSION_MAJOR,
                    FW_VERSION_MINOR,
                    FW_VERSION_PATCH,
                    0  // build число (резерв)
                };
                usb_cdc_ll_write(pkt, sizeof(pkt)); break; }
            default: { stream_send_nack(cmd,0xFF); break; }
        }
    }
}

// Инициализация оставлена (profile id уже установлен в adc_stream)
void usb_stream_init(void) { g_cfg.profile_id = adc_stream_get_profile(); g_cfg.streaming = 0; g_cfg.full_mode = 1; g_cfg.roi_offset_us = 0; g_cfg.roi_length_us = 0; }
