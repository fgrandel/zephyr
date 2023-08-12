/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 Segger SysView Tracing Support
 *
 * This is not to be included by the application.
 */

#ifndef __IEEE802154_TRACING_H__
#define __IEEE802154_TRACING_H__

enum ieee802154_tracing_event_id {
	IEEE802154_TRACING_MARK,

	IEEE802154_TRACING_SET_CHANNEL,

	IEEE802154_TRACING_NET_COUNTER_WAKEUP,
	IEEE802154_TRACING_NET_COUNTER_SUSPEND,
	IEEE802154_TRACING_NET_COUNTER_PROGRAM,
	IEEE802154_TRACING_NET_COUNTER_CAPTURE,

#ifdef CONFIG_NET_L2_IEEE802154_TSCH
	IEEE802154_TRACING_TSCH_SLOT_START,
	IEEE802154_TRACING_TSCH_SLOT_END,
#endif

	IEEE802154_TRACING_NUM_EVENTS,
};

#ifdef CONFIG_SEGGER_SYSTEMVIEW

#include <zephyr/net/net_if.h>

#include <SEGGER_SYSVIEW.h>

/**
 * @brief Initialize the IEEE 802.15.4 tracing module.
 */
void ieee802154_tracing_init(void);

/**
 * @brief Trace an IEEE 802.15.4 API event.
 *
 * @param[in] iface pointer to the interface on which the event occured.
 * @param[in] event_id event to be traced
 */
void ieee802154_trace(struct net_if *iface, enum ieee802154_tracing_event_id event_id);

/**
 * @brief Trace an IEEE 802.15.4 API event with an argument.
 *
 * @param[in] iface pointer to the interface on which the event occured.
 * @param[in] event_id event to be traced
 * @param[in] arg argument to be traced
 */
void ieee802154_trace_arg(struct net_if *iface, enum ieee802154_tracing_event_id event_id,
			  uint32_t arg);

/**
 * @brief Trace an IEEE 802.15.4 API event with an argument.
 *
 * @param[in] iface pointer to the interface on which the event occured.
 * @param[in] event_id event to be traced
 * @param[in] arg1 first argument to be traced
 * @param[in] arg2 second argument to be traced
 * @param[in] arg3 third argument to be traced
 */
void ieee802154_trace_argx3(struct net_if *iface, enum ieee802154_tracing_event_id event_id,
			    uint32_t arg1, uint32_t arg2, uint32_t arg3);

/**
 * @brief Trace an IEEE 802.15.4 API call or start event.
 *
 * @param[in] iface pointer to the interface on which the event occured.
 * @param[in] event_id event to be traced
 */
void ieee802154_trace_enter(struct net_if *iface, enum ieee802154_tracing_event_id event_id);

/**
 * @brief Trace an IEEE 802.15.4 API call or start event with an argument.
 *
 * @param[in] iface pointer to the interface on which the event occured.
 * @param[in] event_id event to be traced
 * @param[in] arg argument to be traced
 */
void ieee802154_trace_enter_arg(struct net_if *iface, enum ieee802154_tracing_event_id event_id,
				uint32_t arg);

/**
 * @brief Trace an IEEE 802.15.4 API call or start event with an argument.
 *
 * @param[in] iface pointer to the interface on which the event occured.
 * @param[in] event_id event to be traced
 * @param[in] arg1 first argument to be traced
 * @param[in] arg2 second argument to be traced
 * @param[in] arg3 third argument to be traced
 */
void ieee802154_trace_enter_argx3(struct net_if *iface, enum ieee802154_tracing_event_id event_id,
				  uint32_t arg1, uint32_t arg2, uint32_t arg3);

/**
 * @brief Trace an IEEE 802.15.4 API call return or end event.
 *
 * @param[in] event_id event to be traced
 */
void ieee802154_trace_exit(enum ieee802154_tracing_event_id event_id);

/**
 * @brief Set a custom trace marker for performance measurements.
 */
void ieee802154_trace_mark(void);

#else
#define ieee802154_tracing_init()
#define ieee802154_trace(iface, event_id)
#define ieee802154_trace_arg(iface, event_id, arg)
#define ieee802154_trace_argx3(iface, event_id, arg1, arg2, arg3)
#define ieee802154_trace_enter(iface, event_id)
#define ieee802154_trace_enter_arg(iface, event_id, arg)
#define ieee802154_trace_enter_argx3(iface, event_id, arg1, arg2, arg3)
#define ieee802154_trace_exit(event_id)
#define ieee802154_trace_mark()
#endif

#endif /* __IEEE802154_TRACING_H__ */
