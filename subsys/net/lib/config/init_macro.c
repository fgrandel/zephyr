/* settings.c - exemplifies the settings config target */

/*
 * Copyright (c) 2024 The Zephyr Project
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include "init_macro.h"

NET_CONFIG_MACRO_DEFINE_STATE(net_config_macro_ifaces);

/* The macro target does not require runtime initialization. */
void net_config_target_pre_init(void)
{
}

int net_config_ifaces(struct net_config_iface **ifaces, int *num_ifaces)
{
	*ifaces = net_config_macro_ifaces;
	*num_ifaces = ARRAY_SIZE(net_config_macro_ifaces);
	return 0;
}
