#include "ring.h"
#include <stdlib.h>
#include <string.h>

bool ring_init(FrameRing *r, uint32_t arena_size) {
    memset(r,0,sizeof(*r));
    r->arena = malloc(arena_size);
    if(!r->arena) return false;
    r->arena_size = arena_size;
    return true;
}

void ring_free(FrameRing *r) {
    free(r->arena); r->arena=NULL;
}

static uint32_t next(uint32_t v){ return (v+1)%RING_CAP; }
uint32_t ring_count(const FrameRing *r){ return (r->head + RING_CAP - r->tail)%RING_CAP; }

bool ring_push(FrameRing *r, const FrameMeta *m, const uint8_t *data){
    uint32_t nxt = next(r->head);
    if(nxt == r->tail) { r->drops++; return false; }
    // naive: store data at start of arena circularly (simple demo)
    if(m->data_len > r->arena_size) return false;
    // just overwrite from 0 each time (demo only)
    memcpy(r->arena, data, m->data_len);
    r->meta[r->head] = *m;
    r->meta[r->head].data_off = 0;
    r->head = nxt;
    return true;
}

bool ring_pop(FrameRing *r, FrameMeta *m, uint8_t *out){
    if(r->tail == r->head) return false;
    *m = r->meta[r->tail];
    memcpy(out, r->arena + m->data_off, m->data_len);
    r->tail = next(r->tail);
    return true;
}
