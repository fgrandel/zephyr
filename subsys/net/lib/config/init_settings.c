/* settings.c - exemplifies the settings config target */

/*
 * Copyright (c) 2024 The Zephyr Project
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>

#include "init_settings.h"

#if defined(CONFIG_NET_IPV4)
static struct net_config_ipv6_prefixes
	ipv6_prefix[NET_CONFIG_SETTINGS_NUM_IFACES * NET_CONFIG_SETTINGS_NUM_PREFIXES_PER_IFACES];
static struct net_config_ipv6_dhcp_clients ipv6_dhcp_client[NET_CONFIG_SETTINGS_NUM_IFACES];
static struct net_config_ipv6 ipv6[NET_CONFIG_SETTINGS_NUM_IFACES];
#endif

#if defined(CONFIG_NET_IPV4)
static struct net_config_ipv4 ipv4[NET_CONFIG_SETTINGS_NUM_IFACES];
#endif

#if defined(CONFIG_NET_VLAN)
static struct net_config_vlan vlan[NET_CONFIG_SETTINGS_NUM_IFACES];
#endif

#if defined(CONFIG_SNTP)
static struct net_config_sntp_server sntp_server[NET_CONFIG_SETTINGS_NUM_SNTP_SERVERS];
#endif

struct k_spinlock lock; /* Enforces atomic updates of ifaces and num_ifaces. */
static struct net_config_iface net_config_settings_ifaces[NET_CONFIG_SETTINGS_NUM_IFACES];
static int net_config_settings_num_ifaces;

void net_config_target_pre_init(void)
{
	settings_subsys_init();
	settings_load_subtree("subsys/net");
}

int net_config_ifaces(struct net_config_iface **ifaces, int *num_ifaces)
{
	K_SPINLOCK(&lock) {
		*ifaces = net_config_settings_ifaces;
		*num_ifaces = net_config_settings_num_ifaces;
	}
	return 0;
}

#if defined(CONFIG_SETTINGS_RUNTIME)
static int config_settings_get(const char *name, char *val, int val_len_max)
{
	return 0;
}
#else
#define config_settings_get NULL
#endif

static int config_settings_set(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg)
{
	return 0;
}

static int config_settings_commit(void)
{
	return 0;
}

static int config_settings_export(int (*export_func)(const char *name, const void *val,
						     size_t val_len))
{
	return 0;
}

/* static subtree handler */
SETTINGS_STATIC_HANDLER_DEFINE(net_config_settings, "subsys/net", config_settings_get,
			       config_settings_set, config_settings_commit, config_settings_export);
