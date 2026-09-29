/* Native test: gcc -O2 -ICore/Inc HostTools/test_rx_phase_detector.c
 * Core/Src/rx_phase_detector.c -lm -o test_rx_phase_detector */
#include "rx_phase_detector.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static uint32_t now_ms;
static unsigned ordinal;
static void feed(rx_phase_detector_t *d, float angle_a, float angle_b,
                 float amplitude, float offset, unsigned frames,
                 unsigned forced_clipped, unsigned bad_parity)
{
    for (unsigned n=0; n<frames; ++n) {
        uint16_t samples[2][600];
        int32_t accum[2][6]={{0}};
        rx_phase_sample_stats_t stats[2]={{0}};
        unsigned parity=ordinal++&1;
        for (unsigned ch=0; ch<2; ++ch) {
            float phase=(110.0f+(ch ? angle_b : angle_a))*0.01745329251994f;
            stats[ch].clipped=(uint16_t)forced_clipped;
            stats[ch].roi_min=65535;
            for (unsigned i=0; i<600; ++i) {
                float signal=amplitude*expf(-(float)i/400.0f)*
                    cosf(6.28318530718f*6250.0f*i/240000.0f+phase);
                float value=offset+(parity ? -signal : signal);
                assert(value>=0.0f && value<=65535.0f);
                samples[ch][i]=(uint16_t)lroundf(value);
                if (i>=300 && i<500) {
                    if (samples[ch][i]<stats[ch].roi_min) stats[ch].roi_min=samples[ch][i];
                    if (samples[ch][i]>stats[ch].roi_max) stats[ch].roi_max=samples[ch][i];
                }
            }
        }
        /* Match the foreground chunking; compare with a whole projection. */
        for (unsigned i=20; i<240; i+=24)
            rx_phase_project_chunk(samples,i,i+24,accum);
        int32_t whole[2][6]={{0}};
        rx_phase_project_chunk(samples,20,240,whole);
        assert(memcmp(whole,accum,sizeof(whole))==0);
        now_ms+=25;
        rx_phase_detector_tick(d,now_ms);
        rx_phase_detector_add(d,accum,bad_parity ? 0 : parity,stats);
    }
}

static void expect(const rx_phase_detector_t *d, unsigned a, unsigned b)
{
    assert(d->channel[0].valid && d->channel[1].valid);
    assert(d->channel[0].phase==a && d->channel[1].phase==b);
}

int main(void)
{
    rx_phase_detector_t d;
    rx_phase_detector_reset(&d,0);
    assert(d.channel[0].phase==255 && !d.channel[0].valid);
    feed(&d,0,180,12000,32768,100,0,0); expect(&d,0,1);
    /* Offset and gain changes must preserve binary phase. */
    feed(&d,0,180,2000,20000,100,0,0); expect(&d,0,1);
    feed(&d,35,150,9000,44000,100,0,0); expect(&d,0,1);
    assert(d.channel[0].changes==0 && d.channel[1].changes==0);
    feed(&d,180,0,9000,32768,100,0,0); expect(&d,1,0);
    assert(d.channel[0].changes==1 && d.channel[1].changes==1);
    /* A short contradictory burst cannot immediately flip a valid result. */
    feed(&d,0,180,9000,32768,4,0,0);
    assert(d.channel[0].phase==1 && d.channel[1].phase==0);
    feed(&d,90,90,9000,32768,100,0,0);
    assert(!d.channel[0].valid && !d.channel[1].valid);
    assert(d.channel[0].flags&RX_PHASE_AMBIGUOUS);
    feed(&d,0,180,0,32768,100,0,0);
    assert(!d.channel[0].valid && (d.channel[0].flags&RX_PHASE_WEAK));
    feed(&d,0,180,9000,32768,100,160,0);
    assert(!d.channel[0].valid && (d.channel[0].flags&RX_PHASE_CLIPPED));
    feed(&d,0,180,9000,32768,100,0,1);
    assert(!d.channel[0].valid && !d.channel[1].valid);
    /* Starting with inverted input must not learn it as a new zero. */
    rx_phase_detector_reset(&d,now_ms);
    feed(&d,180,0,9000,32768,100,0,0); expect(&d,1,0);
    now_ms+=1100;
    assert(rx_phase_detector_tick(&d,now_ms));
    assert(!d.channel[0].valid && d.channel[0].phase==255);
    puts("RX phase C tests passed");
    return 0;
}
