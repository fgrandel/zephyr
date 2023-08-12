/*
 * Copyright (c) 2023 Florian Grandel, Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 Segger SysView Tracing Support Implementation
 */

#include <zephyr/net/ieee802154_tracing.h>
#include <zephyr/net/net_if.h>
#include <zephyr/sys/util.h>
#include <zephyr/toolchain.h>

static void cb_send_module_desc(void);

static SEGGER_SYSVIEW_MODULE sysview_module = {
	.sModule = "M=ZephyrIeee802154",
	.NumEvents = IEEE802154_TRACING_NUM_EVENTS,
	.pfSendModuleDesc = cb_send_module_desc,
};

static void cb_send_module_desc(void)
{
	SEGGER_SYSVIEW_RecordModuleDescription(&sysview_module, "T=IEEE802154");
	SEGGER_SYSVIEW_RecordModuleDescription(&sysview_module, "S='Zephyr IEEE 802.15.4'");
}

void ieee802154_tracing_init(void)
{
	SEGGER_SYSVIEW_RegisterModule(&sysview_module);
}

static inline uint32_t ieee802154_trace_iface(struct net_if *iface)
{
	return iface == NULL ? 0 : net_if_get_by_iface(iface);
}

inline void ieee802154_trace(struct net_if *iface, enum ieee802154_tracing_event_id event_id)
{
	ieee802154_trace_enter(iface, event_id);
}

inline void ieee802154_trace_argx3(struct net_if *iface, enum ieee802154_tracing_event_id event_id,
				   uint32_t arg1, uint32_t arg2, uint32_t arg3)
{
	ieee802154_trace_enter_argx3(iface, event_id, arg1, arg2, arg3);
}

inline void ieee802154_trace_arg(struct net_if *iface, enum ieee802154_tracing_event_id event_id,
				 uint32_t arg)
{
	ieee802154_trace_enter_arg(iface, event_id, arg);
}

inline void ieee802154_trace_enter(struct net_if *iface, enum ieee802154_tracing_event_id event_id)
{
	SEGGER_SYSVIEW_RecordU32(sysview_module.EventOffset + event_id,
				 ieee802154_trace_iface(iface));
}

inline void ieee802154_trace_enter_arg(struct net_if *iface,
				       enum ieee802154_tracing_event_id event_id, uint32_t arg)
{
	SEGGER_SYSVIEW_RecordU32x2(sysview_module.EventOffset + event_id,
				   ieee802154_trace_iface(iface), arg);
}

inline void ieee802154_trace_enter_argx3(struct net_if *iface,
					 enum ieee802154_tracing_event_id event_id, uint32_t arg1,
					 uint32_t arg2, uint32_t arg3)
{
	SEGGER_SYSVIEW_RecordU32x4(sysview_module.EventOffset + event_id,
				   ieee802154_trace_iface(iface), arg1, arg2, arg3);
}

inline void ieee802154_trace_exit(enum ieee802154_tracing_event_id event_id)
{
	SEGGER_SYSVIEW_RecordEndCall(sysview_module.EventOffset + event_id);
}

inline void ieee802154_trace_mark(void)
{
	SEGGER_SYSVIEW_RecordVoid(sysview_module.EventOffset + IEEE802154_TRACING_MARK);
}
