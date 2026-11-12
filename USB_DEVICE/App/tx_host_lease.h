#ifndef TX_HOST_LEASE_H
#define TX_HOST_LEASE_H
#include <stdint.h>

/* Independent of the 1500 ms HOST_RX_ALIVE display bit. No Flash state.
 * All accesses to a shared instance must be inside the caller's IRQ lock. */
#define TX_HOST_LEASE_MS 5000u
typedef struct {
    uint32_t last_progress_ms;
    uint32_t last_counter;
    uint8_t session_active;
    uint8_t tx_requested;
    uint8_t counter_valid;
} tx_host_lease_t;

static inline void tx_host_lease_revoke(volatile tx_host_lease_t *s)
{
    s->session_active = 0u;
    s->tx_requested = 0u;
}

static inline uint8_t tx_host_lease_poll(volatile tx_host_lease_t *s, uint32_t now)
{
    /* Signed age also tolerates an older main-loop time snapshot after an IRQ
     * renewed the lease. All normal service intervals are below 2^31 ms. */
    if (s->session_active && (int32_t)(now - s->last_progress_ms) >= (int32_t)TX_HOST_LEASE_MS) {
        tx_host_lease_revoke(s);
        return 1u;
    }
    return 0u;
}

static inline void tx_host_lease_start(volatile tx_host_lease_t *s, uint32_t now)
{
    s->last_progress_ms = now; /* Allows the first frames before their first ACK. */
    s->last_counter = 0u;
    s->counter_valid = 0u;
    s->tx_requested = 0u; /* START never replays a previous session's switch. */
    s->session_active = 1u;
}

static inline uint8_t tx_host_lease_ack(volatile tx_host_lease_t *s, uint32_t now, uint32_t counter)
{
    (void)tx_host_lease_poll(s, now); /* A late ACK cannot resurrect an expired session. */
    if (!s->session_active) return 0u;
    if (!s->counter_valid) {
        /* Hosts may retain a cumulative counter across START. The first value
         * establishes a baseline; only later progress renews the lease. */
        s->last_counter = counter;
        s->counter_valid = 1u;
        return 0u;
    }
    if ((int32_t)(counter - s->last_counter) <= 0) return 0u;
    s->last_counter = counter;
    s->last_progress_ms = now;
    return 1u;
}

static inline void tx_host_lease_clear_ack(volatile tx_host_lease_t *s)
{
    /* Accounting reset only: never extend the deadline or resurrect TX. */
    s->last_counter = 0u;
    s->counter_valid = 0u;
}

static inline void tx_host_lease_set_tx(volatile tx_host_lease_t *s, uint32_t now, uint8_t enable)
{
    (void)tx_host_lease_poll(s, now);
    s->tx_requested = (uint8_t)(s->session_active && enable);
}

static inline uint8_t tx_host_lease_effective(const volatile tx_host_lease_t *s)
{
    return (uint8_t)(s->session_active && s->tx_requested);
}
#endif
