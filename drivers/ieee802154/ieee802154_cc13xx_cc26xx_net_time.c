/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief Hybrid network uptime counter and reference for TI CC13/26xx SoC implementation
 */

#include <timeout_q.h>
#include <zephyr/net/net_core.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/net_time.h>

#include <kernel/zephyr/dpl/dpl.h>

#include <driverlib/aon_rtc.h>
#include <driverlib/prcm.h>

#include <inc/hw_rfc_pwr.h>
#include <inc/hw_rfc_rat.h>

#include <ti/drivers/dpl/HwiP.h>
#include <ti/drivers/rf/RF.h>

#include "ieee802154_cc13xx_cc26xx_subg.h"

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(ieee802154_cc13xx_cc26xx_net_time, CONFIG_IEEE802154_DRIVER_LOG_LEVEL);

#define SLEEPCOUNTER_INC_PER_TICK   (0x100000000LL / 32768)
#define SLEEPCOUNTER_SUBSECOND_BITS 32
#define MAX_COMMANDS                8

#define HIGHRES_TICKS_PER_SECOND 4000000
#define NSEC_PER_HIGHRES_TICK    (NSEC_PER_SEC / HIGHRES_TICKS_PER_SECOND)

struct ieee802154_cc13xx_cc26xx_net_time_counter {
	const struct net_time_counter_api api;

	struct net_if *iface;

	struct k_timeout_state timeout_state;

	struct k_spinlock lock;

	/* internal state for counter handling */
	uint64_t offset;

	/* The ticks that have been announced already to the timeout driver. */
	uint64_t announced;

	/* The currently running timer timeout relative to announced. */
	uint64_t dticks;

	/* The constant tick value defining "now" while a timer is being set -
	 * atomically activated by switching lock_elapsed.
	 *
	 * NB: The elapsed value is held constant while programming timeouts for
	 * deterministic dtick values.
	 */
	uint64_t elapsed;

	/* switches to the locked value in `elapsed` if true */
	bool lock_elapsed;

	/* a pointer to the RF driver instance */
	RF_Handle rf_handle;

	/* CC13/26xx synchronization offset between sleep and high-res counter,
	 * see CMD_SYNC_STOP_RAT
	 */
	rfc_CMD_SYNC_START_RAT_t *cmd_sync_start_rat;

	/* RAT Compare configuration for overflow handling */
	RF_RatConfigCompare rat_overflow_trigger_config;
#if NET_TIME_DEBUG_PIN
	RF_RatConfigOutput rat_io_config;
#endif
};

static struct ieee802154_cc13xx_cc26xx_net_time_reference {
	const struct net_time_reference_api api;
	struct ieee802154_cc13xx_cc26xx_net_time_counter counter;

	/* Implementation depends on the underlying syntonization algorithm. */
	void *syntonization_data;
} ieee802154_cc13xx_cc26xx_net_time_reference;

static void rf_operation_chain_cb(struct ieee802154_cc13xx_cc26xx_net_time_counter *counter,
				  volatile rfc_radioOp_t *op)
{
	/* The for-loop provides protection against closed loops. */
	for (int cmd = 0; cmd < MAX_COMMANDS && op != NULL; cmd++) {
		if (op->commandNo == CMD_SYNC_START_RAT) {
			counter->cmd_sync_start_rat = (rfc_CMD_SYNC_START_RAT_t *)op;
			counter->offset = 0;
			break;
		}

		op = op->pNextOp;
	}
}

/* global rf callback hook implementation */
void cc13xx_cc26xx_global_rf_callback(RF_Handle rf_handle, RF_GlobalEvent events, void *arg)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		&ieee802154_cc13xx_cc26xx_net_time_reference.counter;

	if (events & RF_GlobalEventRadioSetup) {
		rf_operation_chain_cb(counter, (volatile RF_Op *)arg);
	}
}

/* See TI's RF_ratIsRunning().c. */
static inline bool hirescounter_is_running(void)
{
	/* Assume by default that the RAT is not available. */
	bool status = false;

	/* If the RF core power domain is ON, read the clock of the RAT. */
	if (HWREG(PRCM_BASE + PRCM_O_PDSTAT0) & PRCM_PDSTAT0_RFC_ON) {
		status = (bool)(HWREG(RFC_PWR_BASE + RFC_PWR_O_PWMCLKEN) & RFC_PWR_PWMCLKEN_RAT_M);
	}

	/* Return with the status of RAT. */
	return status;
}

/* requires lock to be held, see TI's RF_ratGetValue() */
static inline uint32_t hirescounter_get_value(void)
{
	return HWREG(RFC_RAT_BASE + RFC_RAT_O_RATCNT);
}

static inline uint64_t sleepcounter_ticks_to_rat_ticks(uint64_t sleepcounter_ticks, uint32_t rat0)
{
	/* Convert and offset sleep counter to high resolution counter. */
	return
		/* Scale bits 31:0 (sub-second part). */
		(((sleepcounter_ticks & UINT32_MAX) * HIGHRES_TICKS_PER_SECOND) >>
		 SLEEPCOUNTER_SUBSECOND_BITS)
		/* Scale bits 63:32 (seconds). */
		+ (sleepcounter_ticks >> SLEEPCOUNTER_SUBSECOND_BITS) * HIGHRES_TICKS_PER_SECOND
		/* Add RAT offset. */
		+ rat0;
}

static inline uint64_t rat_ticks_to_sleepcounter_ticks(uint64_t rat_ticks, uint32_t rat0)
{
	rat_ticks -= rat0;

	return ((rat_ticks / HIGHRES_TICKS_PER_SECOND) << SLEEPCOUNTER_SUBSECOND_BITS) +
	       DIV_ROUND_UP(((rat_ticks % HIGHRES_TICKS_PER_SECOND) << SLEEPCOUNTER_SUBSECOND_BITS),
			    HIGHRES_TICKS_PER_SECOND);
}

/* requires lock to be held */
static uint64_t get_sleepcounter_ticks(struct ieee802154_cc13xx_cc26xx_net_time_counter *counter)
{
	if (!counter->cmd_sync_start_rat) {
		return 0;
	}

	/* Conservatively assume that we are just about to increment the
	 * sleep counter.
	 */
	return sleepcounter_ticks_to_rat_ticks(AONRTCCurrent64BitValueGet() +
						       SLEEPCOUNTER_INC_PER_TICK,
					       counter->cmd_sync_start_rat->rat0);
}

/* requires lock to be held */
static int get_current_tick(struct ieee802154_cc13xx_cc26xx_net_time_counter *counter,
			    uint64_t *tick)
{
	static uint64_t prev_tick;
	int ret = 0;

	/* See RF_getCurrentTime() - we're implementing our own version so that
	 * we can ensure monotonic and overflow-protected counter values.
	 */

	if (likely(hirescounter_is_running())) {
		*tick = counter->offset + hirescounter_get_value();
	} else {
		/* If the high resolution counter is inactive, read the
		 * sleep counter instead.
		 */
		*tick = get_sleepcounter_ticks(counter);
		ret = -EIO;
	}

	/* Ensure monotonicity. Counting backwards may happen when switching
	 * between high-res and low-res counter.
	 */
	if (prev_tick > *tick) {
		*tick = prev_tick;
	}

	prev_tick = *tick;

	return ret;
}

static void on_rat_triggered(RF_Handle h, RF_RatHandle rh, RF_EventMask e,
			     uint32_t compare_capture_time)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		&ieee802154_cc13xx_cc26xx_net_time_reference.counter;
	uint64_t announce = 0;

	K_SPINLOCK(&counter->lock)
	{
		counter->announced += counter->dticks;
		announce = counter->dticks;
		counter->dticks = 0;
		counter->elapsed = 0;
		counter->lock_elapsed = true;
	}

	/* Announce the current timeout and re-program the counter to the next
	 * timeout.
	 */
	z_timeout_q_timeout_announce(&counter->api.timeout_api, announce);

	K_SPINLOCK(&counter->lock)
	{
		counter->lock_elapsed = false;
	}

	/* The first overflow trigger will always be "too late" as the counter
	 * will be at zero when we init the trigger.
	 */
	if (e & RF_EventError) {
		NET_DBG("RAT overflow captured too late.");
	}

	NET_DBG("RAT overflow captured at CC %" PRIu32 " / Ann %" PRIu64 ".", compare_capture_time,
		announce);
}

static uint64_t ieee802154_cc13xx_cc26xx_net_time_counter_elapsed(void)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		&ieee802154_cc13xx_cc26xx_net_time_reference.counter;
	uint64_t elapsed = 0;

	K_SPINLOCK(&counter->lock)
	{
		if (counter->lock_elapsed) {
			elapsed = counter->elapsed;
		} else {
			uint64_t tick;

			get_current_tick(counter, &tick);
			elapsed = tick - counter->announced;
		}
	}

	return elapsed;
}

#if NET_TIME_DEBUG_PIN
#define NET_TIME_DEBUG_PIN_CONFIG(counter) &counter->rat_io_config
#else
#define NET_TIME_DEBUG_PIN_CONFIG(counter) NULL
#endif

/* ticks are relative to "now", i.e. announced + elapsed */
static void ieee802154_cc13xx_cc26xx_net_time_counter_set_timeout(int64_t ticks, bool idle)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		&ieee802154_cc13xx_cc26xx_net_time_reference.counter;
	RF_RatHandle rat_handle;

	if (ticks == INT_MAX || ticks == K_TICKS_FOREVER) {
		/* TODO: Handle overflow when ticks is INT_MAX or when ticks wrap. */
		return;
	}

	K_SPINLOCK(&counter->lock)
	{
		__ASSERT_NO_MSG(counter->lock_elapsed);

		/* Remember the timeout relative to the previously announced tick count. */
		counter->dticks = counter->elapsed + ticks;

		/* Program the timeout in terms of the currently running counter epoch. */
		uint64_t timeout = counter->announced + counter->dticks - counter->offset;

		__ASSERT_NO_MSG(timeout <= UINT32_MAX);
		counter->rat_overflow_trigger_config.timeout = timeout;
	}

	rat_handle = RF_ratCompare(counter->rf_handle, &counter->rat_overflow_trigger_config,
				   NET_TIME_DEBUG_PIN_CONFIG(counter));
	if (rat_handle == RF_ALLOC_ERROR) {
		NET_ERR("Could not allocate RAT channel for overflow trigger.");
	}

	NET_DBG("RAT overflow was reprogrammed to %" PRId64 ".", ticks);
}

static int ieee802154_cc13xx_cc26xx_net_time_counter_init(const struct net_time_counter_api *api,
							  struct net_if *iface)
{
	/* TODO: Create a common basis struct for SUN FSK and OQPSK PHYs. */
	struct ieee802154_cc13xx_cc26xx_subg_data *drv_data = net_if_get_device(iface)->data;
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		(struct ieee802154_cc13xx_cc26xx_net_time_counter *)api;
	RF_RatConfigCompare *rat_config = &counter->rat_overflow_trigger_config;

	/* No need for locking as this function will not experience concurrency. */

	counter->iface = iface;

	__ASSERT_NO_MSG(drv_data->rf_handle);
	counter->rf_handle = drv_data->rf_handle;

	RF_RatConfigCompare_init(rat_config);
	rat_config->callback = &on_rat_triggered;
	rat_config->channel = RF_RatChannelAny;

#if NET_TIME_DEBUG_PIN
	counter->rat_io_config = (RF_RatConfigOutput) {
		.mode = RF_RatOutputModePulse,
		.select = RF_RatOutputSelectRatGpo3,
	};
#endif

	return 0;
}

static int ieee802154_cc13xx_cc26xx_net_time_counter_get_current_timepoint(
	const struct net_time_counter_api *api, k_timepoint_t *timepoint)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		(struct ieee802154_cc13xx_cc26xx_net_time_counter *)api;
	int ret = 0;

	K_SPINLOCK(&counter->lock)
	{
		if (counter->lock_elapsed) {
			timepoint->tick = counter->announced + counter->elapsed;
		} else {
			ret = get_current_tick(counter, &timepoint->tick);
		}
	}

	return ret;
}

static void ieee802154_cc13xx_cc26xx_net_time_counter_get_tick_from_timepoint(
	const struct net_time_counter_api *api, k_timepoint_t timepoint, void *tick)
{
	*(ratmr_t *)tick = timepoint.tick;
}

static void ieee802154_cc13xx_cc26xx_net_time_counter_get_timepoint_from_tick(
	const struct net_time_counter_api *api, void *tick, k_timepoint_t *timepoint)
{
	(*timepoint).tick = *(ratmr_t *)tick;
}

static void
ieee802154_cc13xx_cc26xx_net_time_counter_timer_start(const struct net_time_counter_api *api,
						      struct k_timer *timer, k_timeout_t duration,
						      k_timeout_t period)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		(struct ieee802154_cc13xx_cc26xx_net_time_counter *)api;
	uint64_t elapsed = ieee802154_cc13xx_cc26xx_net_time_counter_elapsed();

	timer->timeout_api = &counter->api.timeout_api;

	K_SPINLOCK(&counter->lock)
	{
		counter->elapsed = elapsed;
		counter->lock_elapsed = true;
	}

	k_timer_start(timer, duration, period);

	K_SPINLOCK(&counter->lock)
	{
		counter->lock_elapsed = false;
	}
}

static void
ieee802154_cc13xx_cc26xx_net_time_counter_timer_stop(const struct net_time_counter_api *api,
						     struct k_timer *timer)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		(struct ieee802154_cc13xx_cc26xx_net_time_counter *)api;

	__ASSERT_NO_MSG(timer->timeout_api == &counter->api.timeout_api);

	k_timer_stop(timer);
}

static int ieee802154_cc13xx_cc26xx_net_time_counter_wake_up(const struct net_time_counter_api *api)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		(struct ieee802154_cc13xx_cc26xx_net_time_counter *)api;

	/* We start/stop the RAT once when initializing the interface, so this
	 * should never happen.
	 */
	if (unlikely(!counter->cmd_sync_start_rat)) {
		return -EBUSY;
	}

	if (RF_runCmd(counter->rf_handle, (RF_Op *)counter->cmd_sync_start_rat, RF_PriorityNormal,
		      NULL, 0) != RF_EventLastCmdDone) {
		return -EBUSY;
	}

	return 0;
}

static int
ieee802154_cc13xx_cc26xx_net_time_counter_may_sleep(const struct net_time_counter_api *api)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		(struct ieee802154_cc13xx_cc26xx_net_time_counter *)api;

	RF_yield(counter->rf_handle);

	return 0;
}

int ieee802154_cc13xx_cc26xx_net_time_reference_get_time(const struct net_time_reference_api *api,
							 net_time_t *uptime)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		&((struct ieee802154_cc13xx_cc26xx_net_time_reference *)api)->counter;
	k_timepoint_t timepoint;
	int ret;

	/* TODO: Use k_timepoint instead of uint64_t. */
	ret = counter->api.get_current_timepoint(&counter->api, &timepoint);
	if (ret) {
		return ret;
	}

	return api->get_time_from_timepoint(api, timepoint, uptime);
}

int ieee802154_cc13xx_cc26xx_net_time_reference_get_time_from_timepoint(
	const struct net_time_reference_api *api, k_timepoint_t timepoint, net_time_t *net_time)
{
	/* TODO: Caclulate syntonized time. */
	*net_time = timepoint.tick * NSEC_PER_HIGHRES_TICK;
	return 0;
}

#define net_time_to_ticks_near64(t)                                                                \
	z_tmcvt_64(t, Z_HZ_ns, HIGHRES_TICKS_PER_SECOND, Z_CCYC, false, true);

#define net_time_to_ticks_floor64(t)                                                               \
	z_tmcvt_64(t, Z_HZ_ns, HIGHRES_TICKS_PER_SECOND, Z_CCYC, false, false);

#define net_time_to_ticks_ceil64(t)                                                                \
	z_tmcvt_64(t, Z_HZ_ns, HIGHRES_TICKS_PER_SECOND, Z_CCYC, true, false);

int ieee802154_cc13xx_cc26xx_net_time_reference_get_timepoint_from_time(
	const struct net_time_reference_api *api, net_time_t net_time,
	enum net_time_rounding rounding, k_timepoint_t *timepoint)
{
	__ASSERT_NO_MSG(net_time >= 0);

	switch (rounding) {
	case NET_TIME_ROUNDING_NEAREST_TIMEPOINT:
		timepoint->tick = net_time_to_ticks_near64(net_time);
		break;
	case NET_TIME_ROUNDING_NEXT_TIMEPOINT:
		timepoint->tick = net_time_to_ticks_ceil64(net_time);
		break;
	case NET_TIME_ROUNDING_PREVIOUS_TIMEPOINT:
		timepoint->tick = net_time_to_ticks_floor64(net_time);
		break;
	}

	return 0;
}

int ieee802154_cc13xx_cc26xx_net_time_reference_timer_start(
	const struct net_time_reference_api *api, struct net_time_timer *net_time_timer,
	net_time_t expire_at, net_time_t period, enum net_time_rounding rounding,
	net_time_t *programmed_expiry)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		&((struct ieee802154_cc13xx_cc26xx_net_time_reference *)api)->counter;
	k_timepoint_t expire_at_tp;

	if (period < 0) {
		return -EINVAL;
	}

	net_time_timer->time_reference_api = api;
	net_time_timer->current_expiry_ns = expire_at;
	net_time_timer->period_ns = period;
	net_time_timer->rounding = rounding;

	api->get_timepoint_from_time(api, expire_at, rounding, &expire_at_tp);
	counter->api.timer_start(&counter->api, &net_time_timer->timer,
				 K_TIMEOUT_ABS_TICKS(expire_at_tp.tick), K_NO_WAIT);

	if (programmed_expiry) {
		api->get_time_from_timepoint(api, expire_at_tp, programmed_expiry);
	}

	return 0;
}

int ieee802154_cc13xx_cc26xx_net_time_reference_timer_stop(const struct net_time_reference_api *api,
							   struct net_time_timer *net_time_timer)
{
	struct ieee802154_cc13xx_cc26xx_net_time_counter *counter =
		&((struct ieee802154_cc13xx_cc26xx_net_time_reference *)api)->counter;

	counter->api.timer_stop(&counter->api, &net_time_timer->timer);

	return 0;
}

int ieee802154_cc13xx_cc26xx_net_time_reference_syntonize(const struct net_time_reference_api *api,
							  net_time_t net_time,
							  k_timepoint_t timepoint)
{
	/* TODO: implement */
	return 0;
}

int ieee802154_cc13xx_cc26xx_net_time_reference_get_uncertainty(
	const struct net_time_reference_api *api, net_time_t target_time, net_time_t *uncertainty)
{
	/* TODO: implement */
	return 0;
}

int ieee802154_cc13xx_cc26xx_net_time_reference_init(const struct net_time_reference_api *api,
						     struct net_if *iface)
{
	/* currently a no-op */
	return 0;
}

static struct ieee802154_cc13xx_cc26xx_net_time_reference
		ieee802154_cc13xx_cc26xx_net_time_reference = {

	.api = {
		.get_time = ieee802154_cc13xx_cc26xx_net_time_reference_get_time,
		.get_time_from_timepoint =
			ieee802154_cc13xx_cc26xx_net_time_reference_get_time_from_timepoint,
		.get_timepoint_from_time =
			ieee802154_cc13xx_cc26xx_net_time_reference_get_timepoint_from_time,
		.timer_start = ieee802154_cc13xx_cc26xx_net_time_reference_timer_start,
		.timer_stop = ieee802154_cc13xx_cc26xx_net_time_reference_timer_stop,
		.syntonize = ieee802154_cc13xx_cc26xx_net_time_reference_syntonize,
		.get_uncertainty =
			ieee802154_cc13xx_cc26xx_net_time_reference_get_uncertainty,
		.init = ieee802154_cc13xx_cc26xx_net_time_reference_init,
		.counter_api = &ieee802154_cc13xx_cc26xx_net_time_reference.counter.api,
	},

	.counter = {
		.api = {
			.init = ieee802154_cc13xx_cc26xx_net_time_counter_init,
			.get_current_timepoint =
				ieee802154_cc13xx_cc26xx_net_time_counter_get_current_timepoint,
			.get_tick_from_timepoint =
				ieee802154_cc13xx_cc26xx_net_time_counter_get_tick_from_timepoint,
			.get_timepoint_from_tick =
				ieee802154_cc13xx_cc26xx_net_time_counter_get_timepoint_from_tick,
			.timer_start =
				ieee802154_cc13xx_cc26xx_net_time_counter_timer_start,
			.timer_stop =
				ieee802154_cc13xx_cc26xx_net_time_counter_timer_stop,
			.wake_up =
				ieee802154_cc13xx_cc26xx_net_time_counter_wake_up,
			.may_sleep =
				ieee802154_cc13xx_cc26xx_net_time_counter_may_sleep,
			.timeout_api = {
				.elapsed =
					ieee802154_cc13xx_cc26xx_net_time_counter_elapsed,
				.set_timeout =
					ieee802154_cc13xx_cc26xx_net_time_counter_set_timeout,
				.state = &ieee802154_cc13xx_cc26xx_net_time_reference
							.counter.timeout_state,
			},
			.frequency = HIGHRES_TICKS_PER_SECOND,
		},
		.timeout_state = {
			.list = SYS_DLIST_STATIC_INIT(
				&ieee802154_cc13xx_cc26xx_net_time_reference.counter
						.timeout_state.list),
		},
	},
};

const struct net_time_reference_api *ieee802154_cc13xx_cc26xx_net_time_reference_api_get(void)
{
	return &ieee802154_cc13xx_cc26xx_net_time_reference.api;
}
