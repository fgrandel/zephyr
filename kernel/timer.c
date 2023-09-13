/*
 * Copyright (c) 1997-2016 Wind River Systems, Inc.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/kernel.h>

#include <zephyr/init.h>
#include <zephyr/syscall_handler.h>
#include <stdbool.h>
#include <zephyr/spinlock.h>
#include <ksched.h>
#include <wait_q.h>

/**
 * @brief Handle expiration of a kernel timer object.
 *
 * @param to  Timeout used by the timer.
 */
void z_timer_expiration_handler(struct _timeout *to)
{
	struct k_timer *timer = CONTAINER_OF(to, struct k_timer, timeout);
	const struct k_timeout_api *timeout_api = timer->timeout_api;
	struct k_timeout_state *const timeout_state = timeout_api->state;
	k_spinlock_key_t to_key;
	struct k_thread *thread;

	to_key = k_spin_lock(&timeout_state->lock);

	/* In z_timeout_q_timeout_announce(), when a timeout expires, it is first
	 * removed from the timeout list, then its expiration handler is called
	 * (with unlocked interrupts). For kernel timers, the expiration handler
	 * is this function. Usually, the timeout structure related to the timer
	 * that is handled here will not be linked to the timeout list at this
	 * point. But it may happen that before this function is executed and
	 * interrupts are locked again, a given timer gets restarted from an
	 * interrupt context that has a priority higher than the system timer
	 * interrupt. Then, the timeout structure for this timer will turn out
	 * to be linked to the timeout list. And in such case, since the timer
	 * was restarted, its expiration handler should not be executed then,
	 * so the function exits immediately.
	 */
	if (z_is_active_timeout(to)) {
		k_spin_unlock(&timeout_state->lock, to_key);
		return;
	}

	/* update timer's status */
	timer->status++;

	/* If the timer is periodic, start it again; don't add _TICK_ALIGN since
	 * we're already aligned to a tick boundary
	 */
	if (!K_TIMEOUT_EQ(timer->period, K_NO_WAIT) && !K_TIMEOUT_EQ(timer->period, K_FOREVER)) {
		k_timeout_t next = timer->period;

		/* see note about z_add_timeout() in z_impl_k_timer_start() */
		next.ticks = MAX(next.ticks - 1, 0);

#ifdef CONFIG_TIMEOUT_64BIT
		/* Exploit the fact that uptime during a kernel
		 * timeout handler reflects the time of the scheduled
		 * event and not real time to get some inexpensive
		 * protection against late interrupts.  If we're
		 * delayed for any reason, we still end up calculating
		 * the next expiration as a regular stride from where
		 * we "should" have run.  Requires absolute timeouts.
		 * (Note offset by one: we're nominally at the
		 * beginning of a tick, so need to defeat the "round
		 * down" behavior on timeout addition).
		 */
		next = K_TIMEOUT_ABS_TICKS(z_timeout_q_tick_get(timeout_api) + 1 + next.ticks);
#endif
		z_timeout_q_add_timeout_locked(timeout_api, &timer->timeout,
					       z_timer_expiration_handler, next);
	}

	/* Invoke timer expiry function. */
	if (timer->expiry_fn != NULL) {
		k_spin_unlock(&timeout_state->lock, to_key);
		timer->expiry_fn(timer);
		to_key = k_spin_lock(&timeout_state->lock);
	}

	if (!IS_ENABLED(CONFIG_MULTITHREADING)) {
		k_spin_unlock(&timeout_state->lock, to_key);
		return;
	}

	thread = z_waitq_head(&timer->wait_q);

	if (thread == NULL) {
		k_spin_unlock(&timeout_state->lock, to_key);
		return;
	}

	z_unpend_thread_no_timeout(thread);

	arch_thread_return_value_set(thread, 0);

	k_spin_unlock(&timeout_state->lock, to_key);

	z_ready_thread(thread);
}

void k_timer_init(struct k_timer *timer,
			 k_timer_expiry_t expiry_fn,
			 k_timer_stop_t stop_fn)
{
	timer->expiry_fn = expiry_fn;
	timer->stop_fn = stop_fn;
	timer->status = 0;
	timer->timeout_api =
		IS_ENABLED(CONFIG_SYS_CLOCK_EXISTS) ? &Z_SYS_CLOCK_TIMEOUT_API : NULL;

	if (IS_ENABLED(CONFIG_MULTITHREADING)) {
		z_waitq_init(&timer->wait_q);
	}

	z_init_timeout(&timer->timeout);

	SYS_PORT_TRACING_OBJ_INIT(k_timer, timer);

	timer->user_data = NULL;

	z_object_init(timer);
}


void z_impl_k_timer_start(struct k_timer *timer, k_timeout_t duration,
			  k_timeout_t period)
{
	const struct k_timeout_api *timeout_api = timer->timeout_api;

	SYS_PORT_TRACING_OBJ_FUNC(k_timer, start, timer, duration, period);

	if (K_TIMEOUT_EQ(duration, K_FOREVER)) {
		return;
	}

	/* z_add_timeout() always adds one to the incoming tick count
	 * to round up to the next tick (by convention it waits for
	 * "at least as long as the specified timeout"), but the
	 * period interval is always guaranteed to be reset from
	 * within the timer ISR, so no round up is desired and 1 is
	 * subtracted in there.
	 *
	 * Note that the duration (!) value gets the same treatment
	 * for backwards compatibility.  This is unfortunate
	 * (i.e. k_timer_start() doesn't treat its initial sleep
	 * argument the same way k_sleep() does), but historical.  The
	 * timer_api test relies on this behavior.
	 */
	if (Z_TICK_ABS(duration.ticks) < 0) {
		duration.ticks = MAX(duration.ticks - 1, 0);
	}

	K_SPINLOCK(&timeout_api->state->lock)
	{
		(void)z_timeout_q_abort_timeout_locked(timeout_api, &timer->timeout);

		timer->period = period;
		timer->status = 0;

		(void)z_timeout_q_add_timeout_locked(timeout_api, &timer->timeout,
						     z_timer_expiration_handler, duration);
	}
}

#ifdef CONFIG_USERSPACE
static inline void z_vrfy_k_timer_start(struct k_timer *timer,
					k_timeout_t duration,
					k_timeout_t period)
{
	Z_OOPS(Z_SYSCALL_OBJ(timer, K_OBJ_TIMER));
	z_impl_k_timer_start(timer, duration, period);
}
#include <syscalls/k_timer_start_mrsh.c>
#endif

void z_impl_k_timer_stop(struct k_timer *timer)
{
	const struct k_timeout_api *timeout_api = timer->timeout_api;
	bool was_inactive = false;

	SYS_PORT_TRACING_OBJ_FUNC(k_timer, stop, timer);

	K_SPINLOCK(&timeout_api->state->lock)
	{
		was_inactive =
			(z_timeout_q_abort_timeout_locked(timeout_api, &timer->timeout) != 0);
		timer->status = 0;
	}

	if (was_inactive) {
		return;
	}

	if (timer->stop_fn != NULL) {
		timer->stop_fn(timer);
	}

	if (IS_ENABLED(CONFIG_MULTITHREADING)) {
		struct k_thread *pending_thread = z_unpend1_no_timeout(&timer->wait_q);

		if (pending_thread != NULL) {
			z_ready_thread(pending_thread);
			z_reschedule_unlocked();
		}
	}
}

#ifdef CONFIG_USERSPACE
static inline void z_vrfy_k_timer_stop(struct k_timer *timer)
{
	Z_OOPS(Z_SYSCALL_OBJ(timer, K_OBJ_TIMER));
	z_impl_k_timer_stop(timer);
}
#include <syscalls/k_timer_stop_mrsh.c>
#endif

inline uint32_t z_impl_k_timer_status_get(struct k_timer *timer)
{
	uint32_t status = 0;

	K_SPINLOCK(&timer->timeout_api->state->lock)
	{
		status = timer->status;
	}

	return status;
}

#ifdef CONFIG_USERSPACE
static inline uint32_t z_vrfy_k_timer_status_get(struct k_timer *timer)
{
	Z_OOPS(Z_SYSCALL_OBJ(timer, K_OBJ_TIMER));
	return z_impl_k_timer_status_get(timer);
}
#include <syscalls/k_timer_status_get_mrsh.c>
#endif

uint32_t z_impl_k_timer_status_sync(struct k_timer *timer)
{
	struct k_timeout_state *const timeout_state = timer->timeout_api->state;
	k_spinlock_key_t to_key;
	uint32_t result;

	SYS_PORT_TRACING_OBJ_FUNC_ENTER(k_timer, status_sync, timer);

	__ASSERT_NO_MSG(!arch_is_in_isr());

	if (!IS_ENABLED(CONFIG_MULTITHREADING)) {
		do {
			to_key = k_spin_lock(&timeout_state->lock);

			result = timer->status;

			if (result > 0) {
				k_spin_unlock(&timeout_state->lock, to_key);
				break;
			}

			if (z_is_inactive_timeout(&timer->timeout)) {
				k_spin_unlock(&timeout_state->lock, to_key);
				break;
			}

			k_spin_unlock(&timeout_state->lock, to_key);
		} while (true);

		return result;
	}

	/* Locking the timeout state guarantees that the timer status remains
	 * stable as no new expiry can be announced. Must remain locked until
	 * we have pended the thread.
	 */
	to_key = k_spin_lock(&timeout_state->lock);

	result = timer->status;

	if (result == 0U) {
		if (z_is_inactive_timeout(&timer->timeout)) {
			k_spin_unlock(&timeout_state->lock, to_key);

			/* timer already stopped */
			return 0;
		}

		SYS_PORT_TRACING_OBJ_FUNC_BLOCKING(k_timer, status_sync, timer, K_FOREVER);

		/* just started or no expiry since we last checked the status:
		 * wait for timer to expire or stop
		 */
		(void)z_pend_curr(&timeout_state->lock, to_key, &timer->wait_q, K_FOREVER);

		/* get and reset updated timer status */
		result = z_impl_k_timer_status_get(timer);
	} else {
		k_spin_unlock(&timeout_state->lock, to_key);
		/* timer has already expired at least once since we last checked
		 * the status
		 */
	}

	SYS_PORT_TRACING_OBJ_FUNC_EXIT(k_timer, status_sync, timer, result);

	return result;
}

#ifdef CONFIG_USERSPACE
static inline uint32_t z_vrfy_k_timer_status_sync(struct k_timer *timer)
{
	Z_OOPS(Z_SYSCALL_OBJ(timer, K_OBJ_TIMER));
	return z_impl_k_timer_status_sync(timer);
}
#include <syscalls/k_timer_status_sync_mrsh.c>

static inline k_ticks_t z_vrfy_k_timer_remaining_ticks(
						const struct k_timer *timer)
{
	Z_OOPS(Z_SYSCALL_OBJ(timer, K_OBJ_TIMER));
	return z_impl_k_timer_remaining_ticks(timer);
}
#include <syscalls/k_timer_remaining_ticks_mrsh.c>

static inline k_ticks_t z_vrfy_k_timer_expires_ticks(
						const struct k_timer *timer)
{
	Z_OOPS(Z_SYSCALL_OBJ(timer, K_OBJ_TIMER));
	return z_impl_k_timer_expires_ticks(timer);
}
#include <syscalls/k_timer_expires_ticks_mrsh.c>

static inline void *z_vrfy_k_timer_user_data_get(const struct k_timer *timer)
{
	Z_OOPS(Z_SYSCALL_OBJ(timer, K_OBJ_TIMER));
	return z_impl_k_timer_user_data_get(timer);
}
#include <syscalls/k_timer_user_data_get_mrsh.c>

static inline void z_vrfy_k_timer_user_data_set(struct k_timer *timer,
						void *user_data)
{
	Z_OOPS(Z_SYSCALL_OBJ(timer, K_OBJ_TIMER));
	z_impl_k_timer_user_data_set(timer, user_data);
}
#include <syscalls/k_timer_user_data_set_mrsh.c>

#endif
