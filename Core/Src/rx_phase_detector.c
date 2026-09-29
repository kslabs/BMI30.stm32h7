#include "rx_phase_detector.h"
#include "rx_phase_coefficients.h"
#include <math.h>
#include <string.h>

_Static_assert(sizeof(rx_phase_status_t) == RX_PHASE_STATUS_SIZE, "RXP1 layout");
_Static_assert(sizeof(rx_phase_channel_status_t) == 32, "RXP1 channel layout");

#define RX_OPT __attribute__((optimize("O2")))

void RX_OPT rx_phase_detector_reset(rx_phase_detector_t *d, uint32_t now_ms)
{
    memset(d, 0, sizeof(*d));
    d->block_start_ms = now_ms;
    for (unsigned ch=0; ch<2; ++ch) {
        d->channel[ch].phase = RX_PHASE_UNKNOWN;
        d->channel[ch].flags = RX_PHASE_COLLECTING;
    }
}

void RX_OPT rx_phase_project_chunk(const uint16_t samples[2][600], uint16_t start,
                                  uint16_t stop, int32_t accum[2][6])
{
    if (start < 20) start=20;
    if (stop > 240) stop=240;
    /* Keep the six sums in registers across the chunk. In the debug build,
       updating accum[][] inside the innermost loop caused avoidable bus work. */
    for (unsigned ch=0; ch<2; ++ch) {
        int32_t a0=accum[ch][0], a1=accum[ch][1], a2=accum[ch][2];
        int32_t a3=accum[ch][3], a4=accum[ch][4], a5=accum[ch][5];
        for (unsigned i=start; i<stop; ++i) {
            const int16_t *c = rx_phase_coefficients[i-20];
            int32_t x=(int32_t)samples[ch][i]-32768;
            a0+=x*c[0]; a1+=x*c[1]; a2+=x*c[2];
            a3+=x*c[3]; a4+=x*c[4]; a5+=x*c[5];
        }
        accum[ch][0]=a0; accum[ch][1]=a1; accum[ch][2]=a2;
        accum[ch][3]=a3; accum[ch][4]=a4; accum[ch][5]=a5;
    }
}

void RX_OPT rx_phase_detector_add(rx_phase_detector_t *d, const int32_t accum[2][6],
                                 uint8_t parity, const rx_phase_sample_stats_t stats[2])
{
    if (parity>1) return;
    for (unsigned ch=0; ch<2; ++ch) {
        rx_phase_block_t *b=&d->pending[ch];
        if (!b->seen || stats[ch].roi_min<b->roi_min) b->roi_min=stats[ch].roi_min;
        if (stats[ch].roi_max>b->roi_max) b->roi_max=stats[ch].roi_max;
        ++b->seen;
        b->clipped+=stats[ch].clipped;
        b->roi_clipped+=stats[ch].roi_clipped;
        float v[3][2], mag[3];
        uint8_t weak=0;
        for (unsigned w=0; w<3; ++w) {
            v[w][0]=(float)accum[ch][2*w]/32768.0f;
            v[w][1]=(float)accum[ch][2*w+1]/32768.0f;
            mag[w]=sqrtf(v[w][0]*v[w][0]+v[w][1]*v[w][1]);
            if (mag[w]<64.0f) weak=1;
        }
        if (weak || stats[ch].clipped>143u) { ++b->weak; continue; }
        float sign=parity ? -1.0f : 1.0f;
        for (unsigned w=0; w<3; ++w)
            for (unsigned k=0; k<2; ++k)
                b->sum[parity][w][k] += sign*v[w][k]/mag[w];
        ++b->count[parity];
        b->amplitude_sum += mag[0];
    }
}

static void RX_OPT evaluate(rx_phase_detector_t *d, unsigned ch, uint32_t now)
{
    rx_phase_channel_status_t *s=&d->channel[ch];
    float v[3][2]={{0}}, amp=0.0f;
    uint32_t counts[2]={0}, clip=0, weak=0, roi_clip=0, seen=0;
    uint16_t roi_min=65535u,roi_max=0;
    uint8_t complete=d->completed>=RX_PHASE_BLOCKS;
    for (unsigned j=0; j<RX_PHASE_BLOCKS; ++j) {
        const rx_phase_block_t *b=&d->blocks[ch][j];
        for (unsigned p=0; p<2; ++p) {
            counts[p]+=b->count[p];
            if (b->count[p]<4u) complete=0;
            if (b->count[p])
                for (unsigned w=0; w<3; ++w)
                    for (unsigned k=0; k<2; ++k)
                        v[w][k]+=b->sum[p][w][k]/(float)b->count[p]/6.0f;
        }
        amp+=b->amplitude_sum; clip+=b->clipped; weak+=b->weak;
        roi_clip+=b->roi_clipped; seen+=b->seen;
        if (b->seen && b->roi_min<roi_min) roi_min=b->roi_min;
        if (b->roi_max>roi_max) roi_max=b->roi_max;
    }
    uint32_t n=counts[0]+counts[1];
    s->even_count=(uint16_t)counts[0]; s->odd_count=(uint16_t)counts[1];
    s->amplitude=n ? (uint16_t)fminf(65535.0f,amp/(float)n) : 0u;
    s->clipped_permille=seen ? (uint16_t)(clip*1000u/(220u*seen)) : 0u;
    s->roi_clipped_permille=seen ? (uint16_t)(roi_clip*1000u/(200u*seen)) : 0u;
    s->roi_min=seen ? roi_min : 0u; s->roi_max=roi_max;
    s->roi_span=seen ? roi_max-roi_min : 0u;
    float magnitude=sqrtf(v[0][0]*v[0][0]+v[0][1]*v[0][1]);
    s->coherence_permille=(uint16_t)(fminf(1.0f,magnitude)*1000.0f);
    s->angle_cdeg=(int16_t)(atan2f(v[0][1],v[0][0])*5729.5779513f);
    float axis=magnitude>0.0f ? fabsf(v[0][0])/magnitude : 0.0f;
    s->confidence=(uint8_t)(fminf(1.0f,magnitude)*axis*100.0f);
    s->valid=0;
    s->flags=s->clipped_permille>50u ? RX_PHASE_CLIPPED : 0u;
    if (!complete) {
        s->flags |= weak ? RX_PHASE_WEAK : RX_PHASE_COLLECTING;
        return;
    }
    /* Near the 90-degree decision boundary keep the last binary result, but
       mark it invalid. A stable 30/150-degree estimate still becomes 0/180. */
    uint8_t consistent=1;
    for (unsigned w=1; w<3; ++w) {
        float m=sqrtf(v[w][0]*v[w][0]+v[w][1]*v[w][1]);
        float dot=v[0][0]*v[w][0]+v[0][1]*v[w][1];
        if (m<0.75f || dot<0.5f*m*magnitude) consistent=0;
    }
    if (magnitude<0.80f || axis<0.20f || !consistent) {
        s->flags |= RX_PHASE_AMBIGUOUS;
        return;
    }
    uint8_t phase=v[0][0]<0.0f ? 1u : 0u;
    if (s->phase!=RX_PHASE_UNKNOWN && s->phase!=phase) ++s->changes;
    s->phase=phase; s->valid=1; s->last_valid_ms=now;
}

uint8_t RX_OPT rx_phase_detector_tick(rx_phase_detector_t *d, uint32_t now_ms)
{
    if ((uint32_t)(now_ms-d->block_start_ms)<RX_PHASE_BLOCK_MS) return 0;
    if ((uint32_t)(now_ms-d->block_start_ms)>=2u*RX_PHASE_BLOCK_MS) {
        /* Missing foreground time must not make old blocks appear fresh. */
        rx_phase_detector_reset(d,now_ms);
        return 1;
    }
    for (unsigned ch=0; ch<2; ++ch) {
        d->blocks[ch][d->next_block]=d->pending[ch];
        memset(&d->pending[ch],0,sizeof(d->pending[ch]));
    }
    d->next_block=(uint8_t)((d->next_block+1u)%RX_PHASE_BLOCKS);
    if (d->completed<RX_PHASE_BLOCKS) ++d->completed;
    d->block_start_ms=now_ms;
    for (unsigned ch=0; ch<2; ++ch) evaluate(d,ch,now_ms);
    return 1;
}
