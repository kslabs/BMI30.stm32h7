#define _GNU_SOURCE
#include <libusb-1.0/libusb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <stdint.h>
#include <stdbool.h>
#include <limits.h>
#include "crc16.h"
#include "ring.h"
#include "util.h"

// Базовые VID/PID (новая прошивка меняет PID на 0x4002). Попытка сперва нового, затем fallback.
#define VID 0xCafe
#define PID_FALLBACK 0x4001
#define PID_NEW      0x4002
static int g_force_pid = 0; // если задан через -W, используем его напрямую
// Endpoint адреса теперь определяются динамически по выбранному интерфейсу
static unsigned char g_ep_in = 0x81;  // fallback
static unsigned char g_ep_out = 0x01; // fallback

// Значения по умолчанию могут быть переопределены аргументами
static int g_iface = 2;          // Новый vendor интерфейс
static int g_in_q = 12;          // Глубина конвейера (8..32)
static int g_xfer_size = 16384;  // Размер одного bulk IN (16KB по умолчанию)
static int g_profile = 0;        // Профиль
static int g_full_mode = 0;      // 0=ROI, 1=FULL
static int g_dump_first = 0;     // dump first N frames
static double g_run_seconds = 0; // 0=until signal
static int g_proto_test = 0;     // protocol test mode
static int g_expect_samples = 0; // expected working samples (optional)
static int g_get_status_once = 0;// send GET_STATUS once mid-run
static int g_get_status_interval_ms = 0; // periodic GET_STATUS interval (ms)
static int g_dump_raw_small = 0; // dump first N raw small invalid packets

#define CMD_SET_PROFILE   0x14
#define CMD_SET_FULL_MODE 0x13
#define CMD_SET_ROI_US    0x15
#define CMD_START_STREAM  0x20
#define CMD_STOP_STREAM   0x21
#define CMD_GET_STATUS    0x30

#define MAGIC 0xA55A
// Ограничения/гарантии
#define MAX_XFER_LIMIT (64*1024)


#pragma pack(push,1)
typedef struct {
    uint16_t magic;
    uint8_t  version;
    uint8_t  flags;
    uint32_t seq;
    uint32_t timestamp;
    uint16_t total_samples;
    uint16_t zone_count;
    uint32_t zone1_offset;
    uint32_t zone1_length;
    uint32_t reserved;
    uint16_t reserved2;
    uint16_t crc16;
} VendorHdr;
#pragma pack(pop)

static volatile int g_stop=0;
static void on_sig(int s){ (void)s; g_stop=1; }

// Поиск и открытие устройства с явной диагностикой ошибки доступа.
static libusb_device_handle *find_and_open_ex(libusb_context *ctx, uint16_t vid, uint16_t pid, int *perm_err){
    if(perm_err) *perm_err = 0;
    libusb_device **list=NULL; ssize_t n = libusb_get_device_list(ctx,&list);
    if(n<0) return NULL;
    libusb_device_handle *handle=NULL;
    for(ssize_t i=0;i<n;i++){
        struct libusb_device_descriptor dd; if(libusb_get_device_descriptor(list[i], &dd)) continue;
        if(dd.idVendor==vid && dd.idProduct==pid){
            int r = libusb_open(list[i], &handle);
            if(r){
                if(r==LIBUSB_ERROR_ACCESS && perm_err) *perm_err=1;
                handle=NULL; // ensure NULL
            }
            break; // устройство найдено (независимо от результата открытия)
        }
    }
    libusb_free_device_list(list,1);
    return handle;
}

static int send_cmd(libusb_device_handle *h, uint8_t cmd, const void *payload, int plen){
    uint8_t buf[64];
    if(1+plen > (int)sizeof(buf)) return -2;
    buf[0]=cmd;
    if(plen>0) memcpy(buf+1,payload,plen);
    int x=0;
    int r = libusb_bulk_transfer(h, g_ep_out, buf, 1+plen, &x, 1000);
    if(r || x != (1+plen)) return -1;
    return 0;
}


typedef struct { struct libusb_transfer *t; uint8_t *buf; int busy; } InXfer;

static void usage(const char *prg){
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  -i <iface>    Interface number (default %d)\n"
        "  -q <depth>    Queue depth transfers (default %d)\n"
        "  -s <kbytes>   Transfer size in KB (16..32 recommended, default %d)\n"
    "  -p <profile>  Profile index (default %d)\n"
    "  -d <N>        Dump first N frames (seq/flags/len)\n"
    "  -T <sec>     Auto-stop after <sec> seconds (default: infinite)\n"
    "  -P           Protocol self-test (test frame + first stereo pair)\n"
    "  -E <samples> Expect working total_samples (assert)\n"
    "  -g           Send GET_STATUS (0x30) once after start\n"
    "  -G <ms>      Periodic GET_STATUS every <ms> milliseconds (>=50)\n"
    "  -r <N>       Dump first N small invalid packets (hex)\n"
    "  -W <pid>     Override USB PID (hex, e.g. 0x4002)\n"
        "  --full        Full mode (default ROI)\n"
        "  -h            Help\n",
        prg,g_iface,g_in_q,g_xfer_size/1024,g_profile);
}

static void parse_args(int argc, char **argv){
    for(int a=1;a<argc;a++){
        if(!strcmp(argv[a],"-i") && a+1<argc){ g_iface=atoi(argv[++a]); }
        else if(!strcmp(argv[a],"-q") && a+1<argc){ g_in_q=atoi(argv[++a]); }
        else if(!strcmp(argv[a],"-s") && a+1<argc){ g_xfer_size=atoi(argv[++a])*1024; }
        else if(!strcmp(argv[a],"-p") && a+1<argc){ g_profile=atoi(argv[++a]); }
    else if(!strcmp(argv[a],"--full")){ g_full_mode=1; }
    else if(!strcmp(argv[a],"-d") && a+1<argc){ g_dump_first=atoi(argv[++a]); }
    else if(!strcmp(argv[a],"-T") && a+1<argc){ g_run_seconds=atof(argv[++a]); }
    else if(!strcmp(argv[a],"-P")){ g_proto_test=1; }
    else if(!strcmp(argv[a],"-E") && a+1<argc){ g_expect_samples=atoi(argv[++a]); }
    else if(!strcmp(argv[a],"-g")){ g_get_status_once=1; }
    else if(!strcmp(argv[a],"-G") && a+1<argc){ g_get_status_interval_ms=atoi(argv[++a]); }
    else if(!strcmp(argv[a],"-r") && a+1<argc){ g_dump_raw_small=atoi(argv[++a]); }
    else if(!strcmp(argv[a],"-W") && a+1<argc){
        const char *s = argv[++a];
        if(!strncmp(s,"0x",2) || !strncmp(s,"0X",2)) g_force_pid = (int)strtol(s,NULL,16);
        else g_force_pid = atoi(s);
    }
        else if(!strcmp(argv[a],"-h") || !strcmp(argv[a],"--help")){ usage(argv[0]); exit(0);} }
    if(g_xfer_size % 64) { g_xfer_size = (g_xfer_size/64)*64; fprintf(stderr,"[adj] align xfer size to %d\n", g_xfer_size); }
    if(g_xfer_size < 1024) g_xfer_size = 1024;
    if(g_xfer_size > MAX_XFER_LIMIT) g_xfer_size = MAX_XFER_LIMIT;
    if(g_in_q < 4) g_in_q = 4;
    if(g_in_q > 32) g_in_q = 32;
}

// глобальные счётчики для callback
static FrameRing rA, rB;
static uint64_t bytes_ok=0, frames_ok=0, crc_bad=0, magic_bad=0;
static uint64_t hdr_bytes_ok=0; // header bytes accumulated
// Stereo pairing / gap detection
static uint32_t cur_seq=0;          // expected (current) sequence index
static uint8_t cur_mask=0;          // bit0=ADC0 seen, bit1=ADC1 seen for cur_seq
static uint64_t stereo_pairs=0;
static uint64_t seq_gaps=0;         // count of missing sequences (forward jumps)
static uint64_t seq_dup=0;          // duplicate full sequences after completion
static uint64_t seq_back=0;         // backwards jumps (old sequence << expected)
static uint64_t adc_dup=0;          // duplicate ADC frame inside same sequence
static int seq_initialized=0;
// Transfer statistics
static uint64_t transfers=0;
static uint64_t transfers_size_sum=0;
static int transfer_len_min=INT_MAX;
static int transfer_len_max=0;
static uint64_t transfer_len_bad=0; // actual_length != frame size
static uint32_t exp_frame_size=0;   // last expected frame size (for stable check)
static uint16_t exp_samples=0;      // baseline samples per frame (first observed)
static uint64_t sample_variations=0; // how many times samples changed vs baseline
static uint64_t sample_counts[2048]; // frequency histogram (supports up to 2047 samples)
static uint64_t test_frames=0;      // count of special test frames (flags bit7)
// Status packet parsing
typedef struct {
    char sig[4];
    uint8_t version;
    uint8_t r0;
    uint16_t cur_samples;
    uint16_t frame_bytes;
    uint16_t test_frames;
    uint32_t produced_seq;
    uint32_t sent0;
    uint32_t sent1;
    uint32_t dbg_tx_cplt;
    uint32_t dbg_partial_abort;
    uint32_t dbg_size_mismatch;
    uint32_t dma_done0;
    uint32_t dma_done1;
    uint32_t frame_wr_seq;
    uint16_t flags_runtime;
    uint16_t r1;
} VendorStatus;
static VendorStatus g_last_status; static int g_have_status=0; static int g_last_status_len=0;
static uint8_t g_last_status_raw[64];

// Protocol test state machine
enum { PT_WAIT_TEST=0, PT_WAIT_ADC0, PT_WAIT_ADC1, PT_DONE, PT_FAIL };
static int pt_state=PT_WAIT_TEST; static uint32_t pt_seq0=0; static int pt_samples=0; static const char *pt_fail_reason=NULL;

static void submit_again(struct libusb_transfer *t){
    InXfer *slot = (InXfer*)t->user_data;
    int rr = libusb_submit_transfer(t);
    if(rr==0) slot->busy=1; else slot->busy=0; // если ошибка — не перезапускаем
}

static int first_activity = 0; // 0 нет, 1 получали кадры, -1 предупреждение уже выдано

static void cb(struct libusb_transfer *t){
    InXfer *slot = (InXfer*)t->user_data; slot->busy=0;
    if(t->status != LIBUSB_TRANSFER_COMPLETED){ submit_again(t); return; }
    int x = t->actual_length;
    transfers++; transfers_size_sum += x;
    if(x < transfer_len_min) transfer_len_min = x;
    if(x > transfer_len_max) transfer_len_max = x;
    // 1) Пытаться распознать статус (любой размер 16..192) по сигнатуре 'STAT' или 'ST2T'
    if(x >= 16 && x <= 192){
        int is_stat_sig = 0; int is_diag_variant=0;
        if(t->buffer[0]=='S' && t->buffer[1]=='T'){
            if(t->buffer[2]=='A' && t->buffer[3]=='T') is_stat_sig=1; // STAT
            else if(t->buffer[2]=='2' && t->buffer[3]=='T'){ is_stat_sig=1; is_diag_variant=1; } // ST2T
        }
        if(is_stat_sig){
            memset(&g_last_status, 0, sizeof(g_last_status));
            int copy = x; if(copy > (int)sizeof(g_last_status)) copy = sizeof(g_last_status);
            memcpy(&g_last_status, t->buffer, copy);
            g_have_status = 1; g_last_status_len = x;
            int rawcopy = x; if(rawcopy>64) rawcopy=64; memcpy(g_last_status_raw, t->buffer, rawcopy);
            uint8_t ver = (uint8_t)g_last_status.version;
            fprintf(stderr,"[status_rx sig=%c%c%c%c len=%d ver=0x%02X%s] cur_samples=%u seq=%u dma0=%u dma1=%u sent0=%u sent1=%u test_fw=%u flags=0x%04X\n",
                t->buffer[0], t->buffer[1], t->buffer[2], t->buffer[3], x, ver,
                is_diag_variant?" diag":"",
                g_last_status.cur_samples, g_last_status.produced_seq, g_last_status.dma_done0, g_last_status.dma_done1,
                g_last_status.sent0, g_last_status.sent1, g_last_status.test_frames, g_last_status.flags_runtime);
            // Печатаем hex: до 64 байт полно, если >64 — укажем обрезание
            fprintf(stderr,"[status_hex %dB%s]", x, x>64?" trunc":"");
            int show = x; if(show>64) show=64;
            for(int i=0;i<show;i++) fprintf(stderr," %02X", t->buffer[i]);
            if(x>64) fprintf(stderr," ...");
            fprintf(stderr,"\n");
            submit_again(t); return;
        }
    }
    // 2) Попытка распознать кадр (заголовок + payload)
    if(x < (int)sizeof(VendorHdr)){
        // Сырый малый пакет (не статус) — опциональный дамп
        if(g_dump_raw_small>0){
            fprintf(stderr,"[raw_small %dB]", x);
            int show = x; if(show>48) show=48;
            for(int i=0;i<show;i++) fprintf(stderr," %02X", t->buffer[i]);
            if(show < x) fprintf(stderr," ...");
            fprintf(stderr,"\n");
            g_dump_raw_small--;
        }
        magic_bad++; submit_again(t); return; 
    }
    VendorHdr *h = (VendorHdr*)t->buffer;
    if(h->magic != MAGIC){ 
        if(g_dump_raw_small>0){
            fprintf(stderr,"[raw_small %dB nonmagic]", x);
            int show = x; if(show>48) show=48;
            for(int i=0;i<show;i++) fprintf(stderr," %02X", t->buffer[i]);
            if(show < x) fprintf(stderr," ...");
            fprintf(stderr,"\n");
            g_dump_raw_small--;
        }
        magic_bad++; submit_again(t); return; 
    }
    int payload_len = h->total_samples * 2;
    int frame_bytes = (int)sizeof(VendorHdr) + payload_len;
    if(payload_len < 0 || frame_bytes > x){ magic_bad++; submit_again(t); return; }
    int is_test = (h->flags & 0x80) ? 1 : 0; // тестовый кадр — не фиксируем размер
    if(is_test){
        test_frames++;
    } else {
        if(exp_frame_size == 0) exp_frame_size = frame_bytes; // capture first expected size (реальный поток)
        if(exp_samples == 0) exp_samples = h->total_samples; else if(h->total_samples != exp_samples) sample_variations++;
        if(h->total_samples < (int)(sizeof(sample_counts)/sizeof(sample_counts[0]))) sample_counts[h->total_samples]++;
    }
    if(x != frame_bytes) transfer_len_bad++;
    if(h->flags & 0x04){
        uint16_t calc = crc16_ccitt_false((uint8_t*)h, sizeof(VendorHdr)-2, 0xFFFF);
        calc = crc16_ccitt_false((uint8_t*)h + sizeof(VendorHdr), payload_len, calc);
        if(calc != h->crc16){ crc_bad++; submit_again(t); return; }
    }
    uint8_t adc_id = (h->flags & 0x02) ? 1 : 0; // 0x01 -> ADC0, 0x02 -> ADC1
    FrameMeta m = { .seq=h->seq, .timestamp_ms=h->timestamp, .samples=h->total_samples, .adc_id=adc_id, .flags=h->flags, .data_len=(uint16_t)payload_len };
    if(adc_id==0) ring_push(&rA,&m,(uint8_t*)h + sizeof(VendorHdr)); else ring_push(&rB,&m,(uint8_t*)h + sizeof(VendorHdr));
    bytes_ok += payload_len; hdr_bytes_ok += sizeof(VendorHdr); frames_ok++;
    if(first_activity==0) first_activity=1;
    if(g_dump_first>0){
        printf("dump frame seq=%u flags=0x%02X%s adc=%u samples=%u frame_len=%d actual_len=%d\n", h->seq, h->flags, is_test?"(TEST)":"", adc_id, h->total_samples, frame_bytes, x);
        fflush(stdout);
        g_dump_first--;
    }
    // Protocol test logic
    if(g_proto_test && pt_state != PT_DONE && pt_state != PT_FAIL){
        if(pt_state==PT_WAIT_TEST){
            if(is_test && h->total_samples==8){ pt_state=PT_WAIT_ADC0; }
            else { pt_state=PT_FAIL; pt_fail_reason="no test frame first"; }
        } else if(pt_state==PT_WAIT_ADC0){
            if(!is_test && (h->flags & 0x01) && !(h->flags & 0x02)){
                pt_seq0 = h->seq; pt_samples = h->total_samples; pt_state=PT_WAIT_ADC1;
            } else if(!is_test) { pt_state=PT_FAIL; pt_fail_reason="expected ADC0 frame"; }
        } else if(pt_state==PT_WAIT_ADC1){
            if(!is_test && (h->flags & 0x02) && h->seq==pt_seq0){
                // verify samples expectation
                if(g_expect_samples && pt_samples != g_expect_samples) { pt_state=PT_FAIL; pt_fail_reason="samples mismatch"; }
                else pt_state=PT_DONE;
            } else if(!is_test) { pt_state=PT_FAIL; pt_fail_reason="expected ADC1 same seq"; }
        }
    }
    if(!seq_initialized){ cur_seq = h->seq; cur_mask=0; seq_initialized=1; }
    if(h->seq == cur_seq){
        // Same sequence we are assembling
        uint8_t bit = (adc_id==0)?0x1:0x2;
        if(cur_mask & bit) {
            // Duplicate ADC frame within same sequence
            adc_dup++;
            // ignore for pairing purposes
        } else {
            cur_mask |= bit;
            if(cur_mask==0x3){
                stereo_pairs++;
                cur_seq++; // advance expectation
                cur_mask=0;
            }
        }
    } else if(h->seq == cur_seq-1){
        // Full sequence already completed; treat as duplicate sequence replay
        seq_dup++;
        // do not change cur_seq / mask
    } else if(h->seq > cur_seq){
        // Forward jump => missing sequences
        seq_gaps += (uint64_t)(h->seq - cur_seq);
        cur_seq = h->seq;
        cur_mask = (adc_id==0)?0x1:0x2;
    } else { // h->seq < cur_seq-1
        seq_back++;
        // Resync to the new (older) sequence? Keep current expectation to avoid cascade.
        // Optionally could: cur_seq = h->seq; cur_mask=(adc_id==0)?0x1:0x2;
    }
    submit_again(t);
}

int main(int argc, char **argv){
    parse_args(argc, argv);
    signal(SIGINT,on_sig); signal(SIGTERM,on_sig);
    libusb_context *ctx=NULL; if(libusb_init(&ctx)) { fprintf(stderr,"libusb_init fail\n"); return 1; }
    libusb_device_handle *h=NULL;
    int tried_new=0, tried_fallback=0;
    int perm_err_new=0, perm_err_fb=0; // флаги отказа в доступе
    if(g_force_pid){
    h = find_and_open_ex(ctx, VID, g_force_pid, &perm_err_new);
        if(!h){
            if(perm_err_new) fprintf(stderr,"Device %04X:%04X present but permission denied (forced). Use sudo or udev rule.\n", VID, g_force_pid);
            else fprintf(stderr,"Device %04X:%04X not found (forced)\n",VID,g_force_pid);
            return 2;
        }
        fprintf(stderr,"[info] using forced PID=0x%04X\n", g_force_pid);
    } else {
    h = find_and_open_ex(ctx, VID, PID_NEW, &perm_err_new); if(h) tried_new=1;
    if(!h){ h = find_and_open_ex(ctx, VID, PID_FALLBACK, &perm_err_fb); if(h) tried_fallback=1; }
        if(!h){
            if(perm_err_new || perm_err_fb){
                fprintf(stderr,"Device %04X:{%04X|%04X} present but permission denied (perm new=%d fb=%d). Add udev rule or run with sudo.\n", VID, PID_NEW, PID_FALLBACK, perm_err_new, perm_err_fb);
            } else {
                fprintf(stderr,"Device %04X:{%04X|%04X} not found\n",VID, PID_NEW, PID_FALLBACK);
            }
            return 2;
        }
        fprintf(stderr,"[info] opened PID=%04X (new=%d fallback=%d)\n", tried_new?PID_NEW:PID_FALLBACK, tried_new, tried_fallback);
    }
    // Авто-детач драйвера (если поддерживается)
#ifdef LIBUSB_API_VERSION
    libusb_set_auto_detach_kernel_driver(h,1);
#endif
    if(libusb_claim_interface(h,g_iface)) { fprintf(stderr,"claim interface %d failed\n", g_iface); return 3; }

    // Получение endpoint'ов выбранного интерфейса
    struct libusb_config_descriptor *cfgd=NULL;
    if(libusb_get_active_config_descriptor(libusb_get_device(h), &cfgd)) { fprintf(stderr,"get config desc fail\n"); return 4; }
    if(g_iface >= cfgd->bNumInterfaces){ fprintf(stderr,"iface %d out of range (have %d)\n", g_iface, cfgd->bNumInterfaces); return 5; }
    const struct libusb_interface *iface = &cfgd->interface[g_iface];
    const struct libusb_interface_descriptor *alt0 = &iface->altsetting[0];
    for(int i=0;i<alt0->bNumEndpoints;i++){
        const struct libusb_endpoint_descriptor *ed = &alt0->endpoint[i];
        if((ed->bmAttributes & LIBUSB_TRANSFER_TYPE_MASK)==LIBUSB_TRANSFER_TYPE_BULK){
            if(ed->bEndpointAddress & LIBUSB_ENDPOINT_IN) g_ep_in = ed->bEndpointAddress; else g_ep_out = ed->bEndpointAddress;
        }
    }
    fprintf(stderr,"[info] iface=%d ep_in=0x%02X ep_out=0x%02X xfer=%dB depth=%d profile=%d full=%d\n", g_iface, g_ep_in, g_ep_out, g_xfer_size, g_in_q, g_profile, g_full_mode);
    if(!g_ep_in || !g_ep_out){ fprintf(stderr,"bulk endpoints not found\n"); return 6; }

    // configure selected profile & mode (defer START until after IN queue armed)
    uint8_t profile = (uint8_t)g_profile; send_cmd(h,CMD_SET_PROFILE,&profile,1);
    uint8_t full=(uint8_t)g_full_mode; send_cmd(h,CMD_SET_FULL_MODE,&full,1);
    // optional ROI cmd skipped

    InXfer *in = calloc(g_in_q, sizeof(InXfer));
    ring_init(&rA, 4000000); ring_init(&rB, 4000000);

    double start_time = now_sec();
    double t0 = start_time; // для периодического вывода
    double last_status_req = start_time; 
    int status_once_sent = 0; // одноразовый GET_STATUS

    // allocate + submit transfers (arm IN before START to avoid missing first test frame)
    for(int i=0;i<g_in_q;i++){
        in[i].buf = malloc(g_xfer_size);
    in[i].t = libusb_alloc_transfer(0);
    libusb_fill_bulk_transfer(in[i].t,h,g_ep_in,in[i].buf,g_xfer_size,cb,&in[i],0);
    int rr = libusb_submit_transfer(in[i].t);
    if(rr){ fprintf(stderr,"submit %d fail=%d\n",i,rr);} else in[i].busy=1;
    }

    // now start streaming so first frame (test frame) is captured by queued URBs
    send_cmd(h,CMD_START_STREAM,NULL,0);

    while(!g_stop){
        libusb_handle_events_completed(ctx,NULL);
        double now = now_sec();
        double dt_period = now - t0;
        double dt_total  = now - start_time;
        if(g_run_seconds>0 && dt_total >= g_run_seconds){ g_stop=1; }
        if(dt_total > 2.0 && first_activity==0){
            fprintf(stderr,"[warn] no data received for %.1fs after START_STREAM (check firmware)\n", dt_total);
            first_activity=-1; // чтобы не спамить
        }
        if(dt_period >= 1.0){
            uint32_t cA = ring_count(&rA), cB = ring_count(&rB);
            double total_bytes = bytes_ok + hdr_bytes_ok;
            double total_kB = total_bytes/1000.0;
            double avg_tlen = transfers? (double)transfers_size_sum/transfers : 0.0;
            printf("frames=%lu stereo=%lu fps=%.1f payload=%.1fkB/s total=%.1fkB/s gaps=%lu dup_seq=%lu back=%lu dup_adc=%lu crc_bad=%lu magic_bad=%lu t_len(avg=%.1f min=%d max=%d bad=%lu exp=%u) qA=%u qB=%u\n",
                frames_ok, stereo_pairs, frames_ok/dt_period, bytes_ok/1000.0, total_kB/dt_period, seq_gaps, seq_dup, seq_back, adc_dup, crc_bad, magic_bad,
                avg_tlen, transfer_len_min==INT_MAX?0:transfer_len_min, transfer_len_max, transfer_len_bad, exp_frame_size, cA, cB);
            frames_ok=0; bytes_ok=0; hdr_bytes_ok=0; stereo_pairs=0; t0=now_sec();
        }
        // Одноразовый GET_STATUS после получения тестового кадра (или таймаут 0.25s)
        if(g_get_status_once && !status_once_sent){
            if(test_frames>0 || (now - start_time) > 0.25){
                send_cmd(h,CMD_GET_STATUS,NULL,0);
                status_once_sent=1;
            }
        }
        // Периодический GET_STATUS
        if(g_get_status_interval_ms>0){
            if(g_get_status_interval_ms < 50) g_get_status_interval_ms = 50; // минимальный предел
            if( (now - last_status_req) * 1000.0 >= g_get_status_interval_ms){
                send_cmd(h,CMD_GET_STATUS,NULL,0);
                last_status_req = now;
            }
        }
    }

    send_cmd(h,CMD_STOP_STREAM,NULL,0);
    // Cancel all transfers
    for(int i=0;i<g_in_q;i++){ if(in[i].t) libusb_cancel_transfer(in[i].t); }
    // Drain events a few times to let cancellations complete
    for(int it=0; it<20; ++it){ struct timeval tv={0,100000}; libusb_handle_events_timeout(ctx,&tv); }
    // Final summary
    fprintf(stderr,"\n[summary] transfers=%lu test_frames=%lu t_min=%d t_max=%d exp=%u gaps=%lu dup_seq=%lu back=%lu dup_adc=%lu crc_bad=%lu magic_bad=%lu sample_var=%lu base_samp=%u\n",
            transfers, test_frames, transfer_len_min==INT_MAX?0:transfer_len_min, transfer_len_max, exp_frame_size, seq_gaps, seq_dup, seq_back, adc_dup, crc_bad, magic_bad, sample_variations, exp_samples);
    if(g_proto_test){
        const char *state_str = (pt_state==PT_DONE?"PASS": (pt_state==PT_FAIL?"FAIL":"INCOMPLETE"));
        fprintf(stderr,"[proto_test] state=%s", state_str);
        if(pt_state==PT_FAIL && pt_fail_reason) fprintf(stderr," reason=%s", pt_fail_reason);
        if(pt_state>=PT_WAIT_ADC0) fprintf(stderr," seq0=%u samples=%d", pt_seq0, pt_samples);
        if(g_expect_samples) fprintf(stderr," expect=%d", g_expect_samples);
        fprintf(stderr,"\n");
    }
    if(g_have_status){
        fprintf(stderr,"[status] cur_samples=%u frame_bytes=%u produced_seq=%u sent0=%u sent1=%u tx_cplt=%u dma0=%u dma1=%u partial=%u size_mis=%u fw_seq=%u flags=0x%04X test_frames_fw=%u\n",
            g_last_status.cur_samples, g_last_status.frame_bytes, g_last_status.produced_seq, g_last_status.sent0, g_last_status.sent1,
            g_last_status.dbg_tx_cplt, g_last_status.dma_done0, g_last_status.dma_done1, g_last_status.dbg_partial_abort,
            g_last_status.dbg_size_mismatch, g_last_status.frame_wr_seq, g_last_status.flags_runtime, g_last_status.test_frames);
        if(g_last_status_len>0){
            fprintf(stderr,"[status_last_hex %dB]", g_last_status_len);
            for(int i=0;i<g_last_status_len && i<64;i++) fprintf(stderr," %02X", g_last_status_raw[i]);
            fprintf(stderr,"\n");
        }
    }
    // Histogram (print only non-zero up to 4 entries)
    int printed=0; fprintf(stderr,"[samples] ");
    for(size_t i=0;i<sizeof(sample_counts)/sizeof(sample_counts[0]) && printed<8;i++) if(sample_counts[i]){ fprintf(stderr,"%zu:%lu ", i, sample_counts[i]); printed++; }
    fprintf(stderr,"\n");
    // Try read a possible status packet (64B) after STOP
    if(g_ep_in){
        uint8_t statbuf[64]; int rx=0; int r = libusb_bulk_transfer(h,g_ep_in,statbuf,sizeof(statbuf),&rx,200);
        if(r==0 && rx>0){
            fprintf(stderr,"[status_raw %dB]", rx);
            for(int i=0;i<rx;i++) fprintf(stderr," %02X", statbuf[i]);
            fprintf(stderr,"\n");
        } else {
            fprintf(stderr,"[status] no status packet (r=%d rx=%d)\n", r, rx);
        }
    }
    for(int i=0;i<g_in_q;i++){ if(in[i].t) libusb_free_transfer(in[i].t); free(in[i].buf);}    
    free(in);
    ring_free(&rA); ring_free(&rB);
    libusb_release_interface(h,g_iface); libusb_close(h); libusb_exit(ctx);
    return 0;
}
