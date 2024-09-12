/* macro-target specific config header for init.c */

/*
 * Copyright (c) 2024 The Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef __NET_LIB_CONFIG_INIT_MACRO_H__
#define __NET_LIB_CONFIG_INIT_MACRO_H__

#include "macro_common.h"
#include "init.h"

#define NET_CONFIG_SNTP_SERVER_STATE(_sntp_server_node_id)                                         \
	{                                                                                          \
		.server_name = DT_PROP(_sntp_server_node_id, server_name),                         \
		.timeout = DT_PROP(_sntp_server_node_id, timeout),                                 \
	},

#define NET_CONFIG_SNTP_SERVERS(_sntp_servers_node_id)                                             \
	static struct net_config_sntp_server sntp_servers[] = {DT_FOREACH_CHILD_STATUS_OKAY(       \
		_sntp_servers_node_id, NET_CONFIG_SNTP_SERVER_STATE)};

#define NET_CONFIG_SNTP_SERVER_PTR_OR_NULL(_iface_node_id)                                         \
	COND_CODE_1(                                                                               \
		DT_NODE_HAS_PROP(_iface_node_id, sntp_server),                                     \
		(&sntp_servers[DT_REG_ADDR_BY_IDX(DT_PHANDLE(_iface_node_id, sntp_server), 0)]),   \
		(NULL))

#define NET_CONFIG_IPV6_PREFIX_STATE(_ipv6_prefix_node_id)                                         \
	{                                                                                          \
		.addr = DT_PROP(_ipv6_prefix_node_id, addr),                                       \
		.len = DT_PROP(_ipv6_prefix_node_id, len),                                         \
		.lifetime = DT_PROP(_ipv6_prefix_node_id, lifetime),                               \
	},

#define NET_CONFIG_IPV6_PREFIXES_STATE(_iface_node_id, _ipv6_prefixes_node_id,                     \
				       _ipv6_prefixes_state_name)                                  \
	(static struct net_config_ipv6_prefix NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, ipv6,      \
									prefixes)[] = {            \
		 DT_FOREACH_CHILD_STATUS_OKAY(_ipv6_prefixes_node_id,                              \
					      NET_CONFIG_IPV6_PREFIX_STATE)};)

#define NET_CONFIG_IPV6_PREFIXES(_iface_node_id)                                                   \
	NET_CONFIG_IF_SUBSTATE(NET_CONFIG_SUBNODE(_iface_node_id, ipv6), prefixes,                 \
			       NET_CONFIG_IPV6_PREFIXES_STATE(                                     \
				       _iface_node_id,                                             \
				       NET_CONFIG_SUBNODE2(_iface_node_id, ipv6, prefixes),        \
				       NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, ipv6, prefixes)))

#define NET_CONFIG_NUM_IPV6_PREFIXES(_iface_node_id)                                               \
	NET_CONFIG_COND_SUBSTATE(                                                                  \
		NET_CONFIG_SUBNODE(_iface_node_id, ipv6), prefixes,                                \
		(DT_CHILD_NUM_STATUS_OKAY(NET_CONFIG_SUBNODE2(_iface_node_id, ipv6, prefixes))),   \
		(0))

#define NET_CONFIG_IPV6_DHCP_CLIENT_STATE(_ipv6_dhcp_client_node_id, _ipv6_dhcp_client_state_name) \
	(static struct net_config_ipv6_dhcp_client _ipv6_dhcp_client_state_name = {                \
		 .req_addr = DT_PROP(_ipv6_dhcp_client_node_id, req_addr),                         \
		 .req_prefix = DT_PROP(_ipv6_dhcp_client_node_id, req_prefix),                     \
	 };)

#define NET_CONFIG_IPV6_DHCP_CLIENT(_iface_node_id)                                                \
	NET_CONFIG_IF_SUBSTATE(                                                                    \
		NET_CONFIG_SUBNODE(_iface_node_id, ipv6), dhcp_client,                             \
		NET_CONFIG_IPV6_DHCP_CLIENT_STATE(                                                 \
			NET_CONFIG_SUBNODE2(_iface_node_id, ipv6, dhcp_client),                    \
			NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, ipv6, dhcp_client)))

#define NET_CONFIG_IPV6_STATE(_iface_node_id, _ipv6_node_id, _ipv6_state_name)                     \
                                                                                                   \
	(NET_CONFIG_SUBSTATE_STR_ARRAY(_iface_node_id, ipv6, addrs);                               \
	NET_CONFIG_SUBSTATE_STR_ARRAY(_iface_node_id, ipv6, mcast_addrs);                          \
                                                                                                   \
	static struct net_config_ipv6 _ipv6_state_name = {                                         \
		 .addrs = NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, ipv6, addrs),                  \
		 .mcast_addrs = NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, ipv6, mcast_addrs),      \
		 .dhcp_client =                                                                    \
			 NET_CONFIG_SUBSTATE2_PTR_OR_NULL(_iface_node_id, ipv6, dhcp_client),      \
		 .prefixes = NET_CONFIG_SUBSTATE2_OR_NULL(_iface_node_id, ipv6, prefixes),         \
		 .hop_limit = DT_PROP(_ipv6_node_id, hop_limit),                                   \
		 .mcast_hop_limit = DT_PROP(_ipv6_node_id, mcast_hop_limit),                       \
		 .num_addrs = DT_PROP_LEN(_ipv6_node_id, addrs),                                   \
		 .num_mcast_addrs = DT_PROP_LEN(_ipv6_node_id, mcast_addrs),                       \
		 .num_prefixes = NET_CONFIG_NUM_IPV6_PREFIXES(_iface_node_id),                     \
	};)

#define NET_CONFIG_IPV4_STATE(_iface_node_id, _ipv4_node_id, _ipv4_state_name)                     \
                                                                                                   \
	(NET_CONFIG_SUBSTATE_STR_ARRAY(_iface_node_id, ipv4, addrs);                               \
	NET_CONFIG_SUBSTATE_STR_ARRAY(_iface_node_id, ipv4, mcast_addrs);                          \
                                                                                                   \
	static struct net_config_ipv4 _ipv4_state_name = {                                         \
		 .addrs = NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, ipv4, addrs),                  \
		 .mcast_addrs = NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, ipv4, mcast_addrs),      \
		 .gateway = DT_PROP(_ipv4_node_id, gateway),                                       \
		 .ttl = DT_PROP(_ipv4_node_id, ttl),                                               \
		 .mcast_ttl = DT_PROP(_ipv4_node_id, mcast_ttl),                                   \
		 .dhcp_client = NET_CONFIG_COND_SUBSTATE(_ipv4_node_id, dhcp_client, (1), (0)),    \
		 .dhcp_server_base_addr = DT_PROP_OR(                                              \
			 NET_CONFIG_SUBNODE(_ipv4_node_id, dhcp_server), base_addr, NULL),         \
		 .autoconf = DT_PROP(_ipv4_node_id, autoconf),                                     \
		 .num_addrs = DT_PROP_LEN(_ipv4_node_id, addrs),                                   \
		 .num_mcast_addrs = DT_PROP_LEN(_ipv4_node_id, mcast_addrs),                       \
	};)

#define NET_CONFIG_VLAN_STATE(_iface_node_id, _vlan_node_id, _vlan_state_name)                     \
	(static struct net_config_vlan _vlan_state_name = {                                        \
		 .tag = DT_PROP(_vlan_node_id, tag),                                               \
	 };)

#define NET_CONFIG_IPV6(_iface_node_id)                                                            \
	NET_CONFIG_IF_SUBSTATE(                                                                    \
		_iface_node_id, ipv6,                                                              \
		NET_CONFIG_IPV6_STATE(_iface_node_id, NET_CONFIG_SUBNODE(_iface_node_id, ipv6),    \
				      NET_CONFIG_SUBSTATE_NAME(_iface_node_id, ipv6)))

#define NET_CONFIG_IPV4(_iface_node_id)                                                            \
	NET_CONFIG_IF_SUBSTATE(                                                                    \
		_iface_node_id, ipv4,                                                              \
		NET_CONFIG_IPV4_STATE(_iface_node_id, NET_CONFIG_SUBNODE(_iface_node_id, ipv4),    \
				      NET_CONFIG_SUBSTATE_NAME(_iface_node_id, ipv4)))

/* Structures not present in the config will consume no resources. */
#define NET_CONFIG_VLAN(_iface_node_id)                                                            \
	NET_CONFIG_IF_SUBSTATE(                                                                    \
		_iface_node_id, vlan,                                                              \
		NET_CONFIG_VLAN_STATE(_iface_node_id, NET_CONFIG_SUBNODE(_iface_node_id, vlan),    \
				      NET_CONFIG_SUBSTATE_NAME(_iface_node_id, vlan)))

#define NET_CONFIG_IFACE_STATE(_iface_node_id)                                                     \
	{                                                                                          \
		.dev = DEVICE_DT_GET(NET_CONFIG_NET_DEVICE(_iface_node_id)),                       \
		.set_iface_name = DT_PROP_OR(_iface_node_id, set_iface_name, NULL),                \
		.ipv6 = NET_CONFIG_SUBSTATE_PTR_OR_NULL(_iface_node_id, ipv6),                     \
		.ipv4 = NET_CONFIG_SUBSTATE_PTR_OR_NULL(_iface_node_id, ipv4),                     \
		.vlan = NET_CONFIG_SUBSTATE_PTR_OR_NULL(_iface_node_id, vlan),                     \
		.sntp_server = NET_CONFIG_SNTP_SERVER_PTR_OR_NULL(_iface_node_id),                 \
		.set_flags = DT_PROP(_iface_node_id, set_flags),                                   \
		.clear_flags = DT_PROP(_iface_node_id, clear_flags),                               \
		.is_default = DT_PROP(_iface_node_id, is_default),                                 \
	},

BUILD_ASSERT(DT_NUM_INST_STATUS_OKAY(zephyr_net_lib_sntp_servers) <= 1,
	     "Only one SNTP servers configuration with status 'okay' is supported.");

#define NET_CONFIG_MACRO_DEFINE_STATE(_net_config_ifaces)                                          \
	DT_FOREACH_STATUS_OKAY(zephyr_net_lib_sntp_servers, NET_CONFIG_SNTP_SERVERS)               \
	DT_FOREACH_STATUS_OKAY(zephyr_net_iface, NET_CONFIG_IPV6_PREFIXES)                         \
	DT_FOREACH_STATUS_OKAY(zephyr_net_iface, NET_CONFIG_IPV6_DHCP_CLIENT)                      \
	DT_FOREACH_STATUS_OKAY(zephyr_net_iface, NET_CONFIG_IPV6)                                  \
	DT_FOREACH_STATUS_OKAY(zephyr_net_iface, NET_CONFIG_IPV4)                                  \
	DT_FOREACH_STATUS_OKAY(zephyr_net_iface, NET_CONFIG_VLAN)                                  \
	static struct net_config_iface _net_config_ifaces[] = {                                    \
		DT_FOREACH_STATUS_OKAY(zephyr_net_iface, NET_CONFIG_IFACE_STATE)}

#endif /* __NET_LIB_CONFIG_INIT_MACRO_H__ */
