/* config_internal.h */

/*
 * Copyright (c) The Zephyr Project
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef __NET_LIB_CONFIG_INIT_H__
#define __NET_LIB_CONFIG_INIT_H__

#include <stdbool.h>
#include <stdint.h>

#include <zephyr/net/net_if.h>

struct net_config_ipv6_prefix {
	const char *addr;
	uint32_t lifetime;
	uint8_t len;
};

struct net_config_ipv6_dhcp_client {
	bool req_addr;
	bool req_prefix;
};

struct net_config_ipv6 {
	const char **addrs;
	const char **mcast_addrs;
	struct net_config_ipv6_prefix *prefixes;
	struct net_config_ipv6_dhcp_client *dhcp_client;
	uint8_t hop_limit;
	uint8_t mcast_hop_limit;
	uint8_t num_addrs;
	uint8_t num_mcast_addrs;
	uint8_t num_prefixes;
};

struct net_config_ipv4 {
	const char **addrs;
	const char **mcast_addrs;
	const char *gateway;
	uint8_t ttl;
	uint8_t mcast_ttl;
	bool dhcp_client; /* Config sources and targets are structurally decoupled. */
	bool autoconf;
	const char *dhcp_server_base_addr;
	uint8_t num_addrs;
	uint8_t num_mcast_addrs;
};

struct net_config_vlan {
	int tag;
};

struct net_config_sntp_server {
	const char *server_name;
	uint16_t timeout;
};

/**
 * These structures can be documented and optimized (padding, holes).
 */
struct net_config_iface {
	const struct device *const dev;
	struct net_if *iface;       /* set at runtime, null if the device is not an interface */
	int ifindex;                /* set at runtime */
	const char *set_iface_name; /* null if not configured */
	struct net_config_ipv6 *const ipv6;               /* null if not configured */
	struct net_config_ipv4 *const ipv4;               /* null if not configured */
	struct net_config_vlan *const vlan;               /* null if not configured */
	struct net_config_sntp_server *const sntp_server; /* null if not configured */
	uint32_t set_flags; /* binary flags are supported via config includes */
	uint32_t clear_flags;
	bool is_default;
};

#define NET_CONFIG_MUTABLE_FLAGS                                                                   \
	(NET_IF_POINTOPOINT | NET_IF_PROMISC | NET_IF_NO_AUTO_START | NET_IF_FORWARD_MULTICASTS |  \
	 NET_IF_IPV6_NO_ND | NET_IF_IPV6_NO_MLD)

void net_config_target_pre_init(void);
int net_config_ifaces(struct net_config_iface **ifaces, int *num_ifaces);

#endif /* __NET_LIB_CONFIG_INIT_H__ */
