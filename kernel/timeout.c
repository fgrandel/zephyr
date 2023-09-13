/*
 * Copyright (c) 2018 Intel Corporation
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/kernel.h>
#include <zephyr/spinlock.h>
#include <ksched.h>
#include <timeout_q.h>
#include <zephyr/syscall_handler.h>
#include <zephyr/drivers/timer/system_timer.h>
#include <zephyr/sys_clock.h>

#define MAX_WAIT (IS_ENABLED(CONFIG_SYSTEM_CLOCK_SLOPPY_IDLE) \
		  ? K_TICKS_FOREVER : INT_MAX)

static struct _timeout *z_timeout_q_first(const struct k_timeout_api *api)
{
	sys_dnode_t *t = sys_dlist_peek_head(&api->state->list);

	return t == NULL ? NULL : CONTAINER_OF(t, struct _timeout, node);
}

static struct _timeout *z_timeout_q_next(const struct k_timeout_api *api, struct _timeout *t)
{
	sys_dnode_t *n = sys_dlist_peek_next(&api->state->list, &t->node);

	return n == NULL ? NULL : CONTAINER_OF(n, struct _timeout, node);
}

static void z_timeout_q_remove_timeout(const struct k_timeout_api *api, struct _timeout *t)
{
	if (z_timeout_q_next(api, t) != NULL) {
		z_timeout_q_next(api, t)->dticks += t->dticks;
	}

	sys_dlist_remove(&t->node);
}

static uint64_t z_timeout_q_elapsed(const struct k_timeout_api *api)
{
	/* While z_timeout_q_timeout_announce() is executing, new relative
	 * timeouts will be scheduled relatively to the currently firing
	 * timeout's original tick value (=curr_tick) rather than relative to the
	 * current timeout_api.elapsed().
	 *
	 * This means that timeouts being scheduled from within timeout callbacks
	 * will be scheduled at well-defined offsets from the currently firing
	 * timeout.
	 *
	 * As a side effect, the same will happen if an ISR with higher priority
	 * preempts a timeout callback and schedules a timeout.
	 *
	 * The distinction is implemented by looking at announce_remaining which
	 * will be non-zero while z_timeout_q_timeout_announce() is executing
	 * and zero otherwise.
	 */
	return api->state->announce_remaining == 0 ? api->elapsed() : 0U;
}

static int64_t z_timeout_q_next_timeout(const struct k_timeout_api *api)
{
	struct _timeout *to = z_timeout_q_first(api);
	uint64_t ticks_elapsed;
	int64_t diff;

	if (to == NULL) {
		return MAX_WAIT;
	}

	ticks_elapsed = z_timeout_q_elapsed(api);
	__ASSERT_NO_MSG(ticks_elapsed <= INT64_MAX);

	diff = to->dticks - ((int64_t)ticks_elapsed);

	if (diff > (int64_t)INT_MAX) {
		return MAX_WAIT;
	}

	return MAX(0, diff);
}

int z_timeout_q_add_timeout_locked(const struct k_timeout_api *api, struct _timeout *to,
				   _timeout_func_t fn, k_timeout_t timeout)
{
	struct _timeout *t;

#ifdef CONFIG_KERNEL_COHERENCE
	__ASSERT_NO_MSG(arch_mem_coherent(to));
#endif

	__ASSERT_NO_MSG(z_is_inactive_timeout(to));

	to->fn = fn;

	if (IS_ENABLED(CONFIG_TIMEOUT_64BIT) && Z_TICK_ABS(timeout.ticks) >= 0) {
		k_ticks_t ticks = Z_TICK_ABS(timeout.ticks) - api->state->curr_tick;

		to->dticks = MAX(1, ticks);
	} else {
		to->dticks = timeout.ticks + 1 + z_timeout_q_elapsed(api);
	}

	for (t = z_timeout_q_first(api); t != NULL; t = z_timeout_q_next(api, t)) {
		if (t->dticks > to->dticks) {
			t->dticks -= to->dticks;
			sys_dlist_insert(&t->node, &to->node);
			break;
		}
		to->dticks -= t->dticks;
	}

	__ASSERT_NO_MSG(to->dticks >= 0);

	if (t == NULL) {
		sys_dlist_append(&api->state->list, &to->node);
	}

	if (to == z_timeout_q_first(api)) {
		api->set_timeout(z_timeout_q_next_timeout(api), false);
	}

	return 0;
}

int z_timeout_q_add_timeout(const struct k_timeout_api *api, struct _timeout *to,
			    _timeout_func_t fn, k_timeout_t timeout)
{
	int ret = 0;

	if (K_TIMEOUT_EQ(timeout, K_FOREVER)) {
		return -ECANCELED;
	}

	K_SPINLOCK(&api->state->lock)
	{
		ret = z_timeout_q_add_timeout_locked(api, to, fn, timeout);
	}

	return ret;
}

int z_timeout_q_abort_timeout_locked(const struct k_timeout_api *api, struct _timeout *to)
{
	if (z_is_inactive_timeout(to)) {
		return -EINVAL;
	}

	z_timeout_q_remove_timeout(api, to);
	return 0;
}

int z_timeout_q_abort_timeout(const struct k_timeout_api *api, struct _timeout *to)
{
	int ret = 0;

	K_SPINLOCK(&api->state->lock)
	{
		ret = z_timeout_q_abort_timeout_locked(api, to);
	}

	return ret;
}

/* must be locked */
static k_ticks_t z_timeout_q_timeout_remaining(const struct k_timeout_api *api,
					       const struct _timeout *timeout)
{
	k_ticks_t ticks = 0;

	if (z_is_inactive_timeout(timeout)) {
		return 0;
	}

	for (struct _timeout *t = z_timeout_q_first(api); t != NULL; t = z_timeout_q_next(api, t)) {
		ticks += t->dticks;
		if (timeout == t) {
			break;
		}
	}

	return ticks - z_timeout_q_elapsed(api);
}

static int64_t z_timeout_q_get_next_timeout_expiry(const struct k_timeout_api *api)
{
	int64_t ret = (int64_t)K_TICKS_FOREVER;

	K_SPINLOCK(&api->state->lock) {
		ret = z_timeout_q_next_timeout(api);
	}
	return ret;

}

/* Must be called with the the timeout instance lock held */
void z_timeout_q_timeout_announce(const struct k_timeout_api *api, uint64_t ticks)
{
	struct k_timeout_state *const state = api->state;

	k_spinlock_key_t key = k_spin_lock(&state->lock);

	/* We release the lock around the callbacks below, so on SMP
	 * systems someone might be already running the loop.  Don't
	 * race (which will cause paralllel execution of "sequential"
	 * timeouts and confuse apps), just increment the tick count
	 * and return.
	 */
	if (IS_ENABLED(CONFIG_SMP) && (state->announce_remaining != 0)) {
		state->announce_remaining += ticks;
		k_spin_unlock(&state->lock, key);
		return;
	}

	state->announce_remaining = ticks;

	struct _timeout *t;

	for (t = z_timeout_q_first(api);
	     (t != NULL) && (t->dticks <= state->announce_remaining);
	     t = z_timeout_q_first(api)) {
		uint64_t dt = t->dticks;

		__ASSERT_NO_MSG(t->dticks >= 0);

		state->curr_tick += dt;
		t->dticks = 0;
		z_timeout_q_remove_timeout(api, t);

		k_spin_unlock(&state->lock, key);
		t->fn(t);
		key = k_spin_lock(&state->lock);
		state->announce_remaining -= dt;
	}

	if (t != NULL) {
		t->dticks -= state->announce_remaining;
	}

	state->curr_tick += state->announce_remaining;
	state->announce_remaining = 0;

	api->set_timeout(z_timeout_q_next_timeout(api), false);

	k_spin_unlock(&state->lock, key);
}

uint64_t z_timeout_q_tick_get(const struct k_timeout_api *api)
{
	uint64_t t = 0U;

	K_SPINLOCK(&api->state->lock) {
		t = api->state->curr_tick + z_timeout_q_elapsed(api);
	}
	return t;
}

#ifdef CONFIG_SYS_CLOCK_EXISTS
static inline uint64_t sys_clock_elapsed64(void)
{
	return sys_clock_elapsed();
}

static inline void sys_clock_set_timeout64(int64_t ticks, bool idle)
{
	sys_clock_set_timeout(ticks, idle);
}

Z_DEFINE_TIMEOUT_API(sys_clock, sys_clock_elapsed64, sys_clock_set_timeout64);

#if defined(CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME)
int z_clock_hw_cycles_per_sec = CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC;

#ifdef CONFIG_USERSPACE
static inline int z_vrfy_sys_clock_hw_cycles_per_sec_runtime_get(void)
{
	return z_impl_sys_clock_hw_cycles_per_sec_runtime_get();
}
#include <syscalls/sys_clock_hw_cycles_per_sec_runtime_get_mrsh.c>
#endif /* CONFIG_USERSPACE */
#endif /* CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME */

void z_add_timeout(struct _timeout *to, _timeout_func_t fn, k_timeout_t timeout)
{
	(void)z_timeout_q_add_timeout(&Z_SYS_CLOCK_TIMEOUT_API, to, fn, timeout);
}

int z_abort_timeout(struct _timeout *to)
{
	return z_timeout_q_abort_timeout(&Z_SYS_CLOCK_TIMEOUT_API, to);
}

k_ticks_t z_timeout_remaining(const struct _timeout *timeout)
{
	k_ticks_t ticks = 0;

	K_SPINLOCK(&Z_SYS_CLOCK_TIMEOUT_API.state->lock)
	{
		ticks = z_timeout_q_timeout_remaining(&Z_SYS_CLOCK_TIMEOUT_API, timeout);
	}

	return ticks;
}

k_ticks_t z_timeout_expires(const struct _timeout *timeout)
{
	k_ticks_t ticks = 0;

	K_SPINLOCK(&Z_SYS_CLOCK_TIMEOUT_API.state->lock)
	{
		ticks = Z_SYS_CLOCK_TIMEOUT_API.state->curr_tick +
			z_timeout_q_timeout_remaining(&Z_SYS_CLOCK_TIMEOUT_API, timeout);
	}

	return ticks;
}

int32_t z_get_next_timeout_expiry(void)
{
	return (int32_t)z_timeout_q_get_next_timeout_expiry(&Z_SYS_CLOCK_TIMEOUT_API);
}

void sys_clock_announce(int32_t ticks)
{
	__ASSERT_NO_MSG(ticks >= 0);
	z_timeout_q_timeout_announce(&Z_SYS_CLOCK_TIMEOUT_API, ticks);

#ifdef CONFIG_TIMESLICING
	z_time_slice();
#endif
}

int64_t sys_clock_tick_get(void)
{
	return z_timeout_q_tick_get(&Z_SYS_CLOCK_TIMEOUT_API);
}

uint32_t sys_clock_tick_get_32(void)
{
#ifdef CONFIG_TICKLESS_KERNEL
	return (uint32_t)sys_clock_tick_get();
#else
	return (uint32_t)Z_SYS_CLOCK_TIMEOUT_API.state->curr_tick;
#endif
}

int64_t z_impl_k_uptime_ticks(void)
{
	return sys_clock_tick_get();
}

#ifdef CONFIG_USERSPACE
static inline int64_t z_vrfy_k_uptime_ticks(void)
{
	return z_impl_k_uptime_ticks();
}
#include <syscalls/k_uptime_ticks_mrsh.c>
#endif

k_timepoint_t sys_timepoint_calc(k_timeout_t timeout)
{
	k_timepoint_t timepoint;
	k_ticks_t dt;

	if (K_TIMEOUT_EQ(timeout, K_FOREVER)) {
		return K_TIMEPOINT_NEVER;
	}
	if (K_TIMEOUT_EQ(timeout, K_NO_WAIT)) {
		return K_TIMEPOINT_ZERO;
	}

	dt = timeout.ticks;

	if (IS_ENABLED(CONFIG_TIMEOUT_64BIT) && Z_TICK_ABS(dt) >= 0) {
		timepoint.tick = Z_TICK_ABS(dt);
	} else {
		timepoint.tick = sys_clock_tick_get() + MAX(1, dt);
	}

	return timepoint;
}

k_timeout_t sys_timepoint_timeout(k_timepoint_t timepoint)
{
	uint64_t now, remaining;

	if (timepoint.tick == UINT64_MAX) {
		return K_FOREVER;
	}
	if (timepoint.tick == 0) {
		return K_NO_WAIT;
	}

	now = sys_clock_tick_get();
	remaining = (timepoint.tick > now) ? (timepoint.tick - now) : 0;
	return K_TICKS(remaining);
}

#ifdef CONFIG_ZTEST
void z_impl_sys_clock_tick_set(uint64_t tick)
{
	Z_SYS_CLOCK_TIMEOUT_API.state->curr_tick = tick;
}

void z_vrfy_sys_clock_tick_set(uint64_t tick)
{
	z_impl_sys_clock_tick_set(tick);
}
#endif
#endif
