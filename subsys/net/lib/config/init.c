/* init.c */

/*
 * Copyright (c) 2017 Intel Corporation.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(net_config, CONFIG_NET_CONFIG_LOG_LEVEL);

#include <zephyr/kernel.h>
#include <zephyr/init.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>

#include <zephyr/logging/log_backend.h>
#include <zephyr/logging/log_backend_net.h>
#include <zephyr/net/ethernet.h>
#include <zephyr/net/net_core.h>
#include <zephyr/net/net_ip.h>
#include <zephyr/net/dhcpv4.h>
#include <zephyr/net/dhcpv4_server.h>
#include <zephyr/net/dhcpv6.h>
#include <zephyr/net/net_mgmt.h>
#include <zephyr/net/dns_resolve.h>
#include <zephyr/net/virtual.h>
#include <zephyr/net/ipv4_autoconf.h>
#include <zephyr/net/net_config.h>
#include <zephyr/sys/atomic.h>

#include "init.h"
#include "net_private.h"

extern int net_init_clock_via_sntp(struct net_if *iface, const char *server, int timeout);

static K_SEM_DEFINE(waiter, 0, 1);
static K_SEM_DEFINE(counter, 0, UINT_MAX);

#if defined(CONFIG_NET_NATIVE)
static struct net_mgmt_event_callback mgmt_iface_cb;
#endif

static bool is_initialized;

static struct net_config_iface *get_cfg_by_iface(struct net_if *iface)
{
	struct net_config_iface *cfgs;
	int num_cfg;

	__ASSERT_NO_MSG(is_initialized)

	if (net_config_ifaces(&cfgs, &num_cfg) < 0) {
		return NULL;
	}

	if (!iface) {
		return NULL;
	}

	for (int i = 0; i < num_cfg; i++) {
		if (cfgs[i].iface == iface) {
			return &cfgs[i];
		}
	}

	return NULL;
}

static struct net_config_iface *get_default_cfg(void)
{
	struct net_config_iface *cfgs;
	int num_cfg;

	__ASSERT_NO_MSG(is_initialized)

	if (net_config_ifaces(&cfgs, &num_cfg) < 0) {
		return NULL;
	}

	for (int i = 0; i < num_cfg; i++) {
		if (cfgs[i].is_default) {
			return &cfgs[i];
		}
	}

	return NULL;
}

void net_config_pre_init(void)
{
	if (!is_initialized) {
		struct net_config_iface *cfgs;
		int num_cfg;

		net_config_target_pre_init();

		if (net_config_ifaces(&cfgs, &num_cfg) < 0) {
			return;
		}

		for (int i = 0; i < num_cfg; i++) {
			struct net_config_iface *cfg = &cfgs[i];

			bool default_updated = false;

			NET_ASSERT(cfg->dev,
				   "Net iface config w/o device - device assignment should "
				   "have been validated at build time.");

			cfg->iface = net_if_lookup_by_dev(cfg->dev);

			if (cfg->iface) {
				cfg->ifindex = net_if_get_by_iface(cfg->iface);
			} else {
				NET_WARN("Cannot find net iface: net config for device %p will "
					 "be ignored",
					 cfg->dev);
				continue;
			}

			/* The default interface and interface flags must be configured before
			 * anything else as they can change behavior of configuration itself:
			 *  - the default interface will be configured when no interface is
			 *    specified to net_config_init_one().
			 *  - interfaces that have the NET_IF_NO_AUTO_START flag set or cleared
			 *    in the config will (not) be considered for automatic startup.
			 */

			if (cfg->is_default) {
				if (default_updated) {
					NET_WARN("Multiple default ifaces configured: only the "
						 "first one will be used");
					cfg->is_default = false;
				} else {
					default_updated = true;
					net_if_set_default(cfg->iface);

					NET_DBG("Setting iface %d as default", cfg->ifindex);
				}
			}

			/* Ensure that the default interface's config is synchronized. */
			if (net_if_get_default() == cfg->iface) {
				if (default_updated) {
					struct net_config_iface *previous_default =
						get_default_cfg();
					if (previous_default) {
						/* The initial default was overridden by config. */
						previous_default->is_default = false;
					}
				}

				cfg->is_default = true;
			}

			if (cfg->set_flags) {
				uint32_t mutable_set_flags =
					cfg->set_flags & NET_CONFIG_MUTABLE_FLAGS;

				NET_ASSERT(cfg->iface->if_dev);
				NET_DBG("Setting flags 0x%x for iface %d", mutable_set_flags,
					cfg->ifindex);

				if (cfg->set_flags != mutable_set_flags) {
					NET_WARN("Ignoring immutable flags 0x%x from set-flags for "
						 "iface %d",
						 cfg->set_flags & ~NET_CONFIG_MUTABLE_FLAGS,
						 cfg->ifindex);
				}
				atomic_or(cfg->iface->if_dev->flags, mutable_set_flags);
			}

			if (cfg->clear_flags) {
				uint32_t mutable_clear_flags =
					cfg->clear_flags & NET_CONFIG_MUTABLE_FLAGS;

				NET_DBG("Clearing flags 0x%x for iface %d", mutable_clear_flags,
					cfg->ifindex);

				if (cfg->clear_flags != mutable_clear_flags) {
					NET_WARN("Ignoring immutable flags 0x%x from clear-flags "
						 "for iface "
						 "%d",
						 cfg->clear_flags & ~NET_CONFIG_MUTABLE_FLAGS,
						 cfg->ifindex);
				}
				atomic_and(cfg->iface->if_dev->flags, ~mutable_clear_flags);
			}
		}

		is_initialized = true;
	}
}

static void setup_ipv4(const struct net_config_iface *cfg)
{
#if defined(CONFIG_NET_IPV4)
	struct net_config_ipv4 *const ipv4 = cfg->ipv4;
	struct net_if_mcast_addr *ifmaddr;
	struct net_if_addr *ifaddr;
	bool ret;

	if (!cfg->iface) {
		return;
	}

	if (!ipv4) {
		NET_DBG("Skipping IPv%c setup for iface %d", '4', cfg->ifindex);
		net_if_flag_clear(cfg->iface, NET_IF_IPV4);
		return;
	}

	/* First set all the static addresses and then enable DHCP */
	for (int i = 0; i < ipv4->num_addrs; i++) {
		const char *ipv4_addr = ipv4->addrs[i];
		struct sockaddr_in sock_addr = {0};
		uint8_t mask_len = 0;

		if (ipv4_addr == NULL || ipv4_addr[0] == '\0') {
			continue;
		}

		ret = net_ipaddr_mask_parse(ipv4_addr, strlen(ipv4_addr),
					    (struct sockaddr *)&sock_addr, &mask_len);
		if (!ret) {
			NET_WARN("Invalid IPv%c %s address \"%s\"", '4', "unicast", ipv4_addr);
			continue;
		}

		if (net_ipv4_is_addr_unspecified(&sock_addr.sin_addr)) {
			continue;
		}

		ifaddr = net_if_ipv4_addr_add(
			cfg->iface, &sock_addr.sin_addr,
			/* If DHCPv4 is enabled, then allow address
			 * to be overridden.
			 */
			ipv4->dhcp_client ? NET_ADDR_OVERRIDABLE : NET_ADDR_MANUAL, 0);

		if (ifaddr == NULL) {
			NET_WARN("Cannot %s %s \"%s\" to iface %d", "add", "address", ipv4_addr,
				 cfg->ifindex);
			continue;
		}

		/* Wait until Address Conflict Detection is ok.
		 * DHCPv4 server startup will fail if the address is not in
		 * preferred state.
		 */
		if (IS_ENABLED(CONFIG_NET_IPV4_ACD) &&
		    (COND_CODE_1(CONFIG_NET_DHCPV4_SERVER, (ipv4->dhcp_server), (false)))) {
			if (WAIT_FOR(ifaddr->addr_state == NET_ADDR_PREFERRED,
				     USEC_PER_MSEC * MSEC_PER_SEC * 2 /* 2 sec */,
				     k_msleep(100)) == false) {
				NET_WARN("Address \"%s\" still is not preferred", ipv4_addr);
			}
		}

		NET_DBG("Added %s \"%s\" to iface %d", "unicast address", ipv4_addr, cfg->ifindex);

		if (mask_len > 0) {
			struct in_addr netmask = {0};

			netmask.s_addr = BIT_MASK(mask_len);

			net_if_ipv4_set_netmask_by_addr(cfg->iface, &sock_addr.sin_addr, &netmask);

			NET_DBG("Added %s \"%s\" to iface %d", "netmask",
				net_sprint_ipv4_addr(&netmask), cfg->ifindex);
		}
	}

	for (int i = 0; i < ipv4->num_mcast_addrs; i++) {
		const char *ipv4_mcast_addr = ipv4->mcast_addrs[i];
		struct sockaddr_in sock_addr = {0};

		if (ipv4_mcast_addr == NULL || ipv4_mcast_addr[0] == '\0') {
			continue;
		}

		ret = net_ipaddr_mask_parse(ipv4_mcast_addr, strlen(ipv4_mcast_addr),
					    (struct sockaddr *)&sock_addr, NULL);
		if (!ret) {
			NET_WARN("Invalid IPv%c %s address \"%s\"", '4', "multicast",
				 ipv4_mcast_addr);
			continue;
		}

		if (net_ipv4_is_addr_unspecified(&sock_addr.sin_addr)) {
			continue;
		}

		ifmaddr = net_if_ipv4_maddr_add(cfg->iface, &sock_addr.sin_addr);
		if (ifmaddr == NULL) {
			NET_WARN("Cannot %s %s \"%s\" to iface %d", "add", "address",
				 ipv4_mcast_addr, cfg->ifindex);
			continue;
		}

		NET_DBG("Added %s \"%s\" to iface %d", "multicast address", ipv4_mcast_addr,
			cfg->ifindex);
	}

	if (ipv4->ttl > 0) {
		net_if_ipv4_set_ttl(cfg->iface, ipv4->ttl);
	}

	if (ipv4->mcast_ttl > 0) {
		net_if_ipv4_set_mcast_ttl(cfg->iface, ipv4->mcast_ttl);
	}

	if (ipv4->gateway != NULL && ipv4->gateway[0] != '\0') {
		struct sockaddr_in sock_addr = {0};

		ret = net_ipaddr_mask_parse(ipv4->gateway, strlen(ipv4->gateway),
					    (struct sockaddr *)&sock_addr, NULL);
		if (!ret) {
			NET_WARN("Invalid IPv%c %s address \"%s\"", '4', "geteway", ipv4->gateway);
		} else {
			if (!net_ipv4_is_addr_unspecified(&sock_addr.sin_addr)) {
				net_if_ipv4_set_gw(cfg->iface, &sock_addr.sin_addr);

				NET_DBG("Added %s \"%s\" to iface %d", "gateway address",
					net_sprint_ipv4_addr(&sock_addr.sin_addr), cfg->ifindex);
			}
		}
	}

	if (IS_ENABLED(CONFIG_NET_DHCPV4) && ipv4->dhcp_client) {
		NET_DBG("DHCPv4 client started");
		net_dhcpv4_start(cfg->iface);
	} else {
		/* TODO: Check whether either a static address or DHCP4 is configured. */
	}

	if (COND_CODE_1(CONFIG_NET_DHCPV4_SERVER, (ipv4->dhcp_server_base_addr != NULL), (false))) {
		struct sockaddr_in sock_addr = {0};

		ret = net_ipaddr_mask_parse(ipv4->dhcp_server_base_addr,
					    strlen(ipv4->dhcp_server_base_addr),
					    (struct sockaddr *)&sock_addr, NULL);
		if (!ret) {
			NET_WARN("Invalid IPv%c %s address \"%s\"", '4', "DHCPv4 base",
				 ipv4->dhcp_server_base_addr);
		} else {
			int retval;

			retval = net_dhcpv4_server_start(cfg->iface,
							 COND_CODE_1(CONFIG_NET_DHCPV4_SERVER,
								     (&sock_addr.sin_addr),
								     (&((struct in_addr){0}))));
			if (retval < 0) {
				NET_WARN("DHCPv4 server start failed (%d)", retval);
			} else {
				NET_DBG("DHCPv4 server started");
			}
		}
	}

	if (IS_ENABLED(CONFIG_NET_IPV4_AUTO) && ipv4->autoconf) {
		NET_DBG("IPv4 autoconf started");
		net_ipv4_autoconf_start(cfg->iface);
	}
#endif /* CONFIG_NET_IPV4 */
}

static void setup_ipv6(const struct net_config_iface *cfg)
{
#if defined(CONFIG_NET_IPV6)
	struct net_config_ipv6 *const ipv6 = cfg->ipv6;
	struct net_if_mcast_addr *ifmaddr;
	struct net_if_addr *ifaddr;
	bool ret;

	if (!cfg->iface) {
		return;
	}

	if (!ipv6) {
		NET_DBG("Skipping IPv%c setup for iface %d", '6', cfg->ifindex);
		net_if_flag_clear(cfg->iface, NET_IF_IPV6);
		return;
	}

	/* First set all the static addresses and then enable DHCP */
	for (int i = 0; i < ipv6->num_addrs; i++) {
		const char *ipv6_addr = ipv6->addrs[i];
		struct sockaddr_in6 sock_addr = {0};
		uint8_t prefix_len = 0;

		if (ipv6_addr == NULL || ipv6_addr[0] == '\0') {
			continue;
		}

		ret = net_ipaddr_mask_parse(ipv6_addr, strlen(ipv6_addr),
					    (struct sockaddr *)&sock_addr, &prefix_len);
		if (!ret) {
			NET_WARN("Invalid IPv%c %s address \"%s\"", '6', "unicast", ipv6_addr);
			continue;
		}

		if (net_ipv6_is_addr_unspecified(&sock_addr.sin6_addr)) {
			continue;
		}

		ifaddr = net_if_ipv6_addr_add(
			cfg->iface, &sock_addr.sin6_addr,
			/* If DHCPv6 is enabled, then allow address
			 * to be overridden.
			 */
			ipv6->dhcp_client ? NET_ADDR_OVERRIDABLE : NET_ADDR_MANUAL, 0);
		if (ifaddr == NULL) {
			NET_WARN("Cannot %s %s \"%s\" to iface %d", "add", "address", ipv6_addr,
				 cfg->ifindex);
			continue;
		}

		NET_DBG("Added %s \"%s\" to iface %d", "unicast address", ipv6_addr, cfg->ifindex);
	}

	for (int i = 0; i < ipv6->num_mcast_addrs; i++) {
		const char *ipv6_mcast_addr = ipv6->mcast_addrs[i];
		struct sockaddr_in6 sock_addr = {0};

		if (ipv6_mcast_addr == NULL || ipv6_mcast_addr[0] == '\0') {
			continue;
		}

		ret = net_ipaddr_mask_parse(ipv6_mcast_addr, strlen(ipv6_mcast_addr),
					    (struct sockaddr *)&sock_addr, NULL);
		if (!ret) {
			NET_WARN("Invalid IPv%c %s address \"%s\"", '6', "multicast",
				 ipv6_mcast_addr);
			continue;
		}

		if (net_ipv6_is_addr_unspecified(&sock_addr.sin6_addr)) {
			continue;
		}

		ifmaddr = net_if_ipv6_maddr_add(cfg->iface, &sock_addr.sin6_addr);
		if (ifmaddr == NULL) {
			NET_WARN("Cannot %s %s \"%s\" to iface %d", "add", "address",
				 ipv6_mcast_addr, cfg->ifindex);
			continue;
		}

		NET_DBG("Added %s \"%s\" to iface %d", "multicast address", ipv6_mcast_addr,
			cfg->ifindex);
	}

	for (int i = 0; i < ipv6->num_prefixes; i++) {
		struct net_config_ipv6_prefix *ipv6_prefix = &ipv6->prefixes[i];
		struct net_if_ipv6_prefix *prefix;
		struct sockaddr_in6 addr = {0};

		if (ipv6_prefix->addr == NULL || ipv6_prefix->addr[0] == '\0') {
			continue;
		}

		ret = net_ipaddr_mask_parse(ipv6_prefix->addr, strlen(ipv6_prefix->addr),
					    (struct sockaddr *)&addr, NULL);
		if (!ret) {
			NET_WARN("Invalid IPv%c %s address \"%s\"", '6', "prefix",
				 ipv6_prefix->addr);
			continue;
		}

		if (net_ipv6_is_addr_unspecified(&addr.sin6_addr)) {
			continue;
		}

		prefix = net_if_ipv6_prefix_add(cfg->iface, &addr.sin6_addr, ipv6_prefix->len,
						ipv6_prefix->lifetime);
		if (prefix == NULL) {
			NET_WARN("Cannot %s %s \"%s\" to iface %d", "add", "prefix",
				 ipv6_prefix->addr, cfg->ifindex);
			continue;
		}

		NET_DBG("Added %s \"%s\" to iface %d", "prefix", ipv6_prefix->addr, cfg->ifindex);
	}

	if (ipv6->hop_limit > 0) {
		net_if_ipv6_set_hop_limit(cfg->iface, ipv6->hop_limit);
	}

	if (ipv6->mcast_hop_limit > 0) {
		net_if_ipv6_set_mcast_hop_limit(cfg->iface, ipv6->mcast_hop_limit);
	}

	if (COND_CODE_1(CONFIG_NET_DHCPV6, (ipv6->dhcp_client), (false))) {
		struct net_dhcpv6_params params = {
			.request_addr = COND_CODE_1(CONFIG_NET_DHCPV6,
						    (ipv6->dhcp_client->req_addr), (false)),
			.request_prefix = COND_CODE_1(CONFIG_NET_DHCPV6,
						      (ipv6->dhcp_client->req_prefix), (false)),
		};

		net_dhcpv6_start(cfg->iface, &params);
	}
#endif /* CONFIG_NET_IPV6 */
}

static void setup_ieee802154(struct net_if *iface, bool start)
{
#if defined(CONFIG_NET_L2_IEEE802154)
	int ret = net_config_init_ieee802154(iface, start);

	if (ret < 0) {
		NET_WARN("Cannot setup IEEE 802.15.4 iface (%d)", ret);
	}
#endif /* CONFIG_NET_L2_IEEE802154 */
}

static void setup_vlan(const struct net_config_iface *cfg)
{
#if defined(CONFIG_NET_VLAN)
	const struct net_config_vlan *vlan = cfg->vlan;
	int ret;

	if (!(cfg->iface && vlan)) {
		return;
	}

	ret = net_eth_vlan_enable(cfg->iface, vlan->tag);
	if (ret < 0) {
		NET_WARN("Cannot add VLAN tag %d to iface %d (%d)", vlan->tag, cfg->ifindex, ret);
		return;
	}

	NET_DBG("Added %s %d to iface %d", "VLAN tag", vlan->tag, cfg->ifindex);
#endif /* CONFIG_NET_VLAN */
}

static void setup_virtual_l2(const struct net_config_iface *cfg)
{
#if defined(CONFIG_NET_L2_VIRTUAL)
	const struct virtual_interface_api *api = net_if_get_device(cfg->iface)->api;
	struct net_if *physical_iface = NULL;
	int ret;

	if (!cfg->iface || net_if_l2(cfg->iface) != &NET_L2_GET_NAME(VIRTUAL)) {
		return;
	}

	/* VLAN interfaces are handled separately */
	if (api->get_capabilities(cfg->iface) & VIRTUAL_INTERFACE_VLAN) {
		return;
	}

	/* TODO: identify physical interface. */

	ret = net_virtual_interface_attach(cfg->iface, physical_iface);
	if (ret < 0) {
		if (ret != -EALREADY) {
			NET_WARN("Cannot %s %s %s to iface %d (%d)", "attach", "virtual", "iface",
				 net_if_get_by_iface(cfg->iface), ret);
		}

		return;
	}

	NET_DBG("Added %s %d to iface %d", "virtual iface", cfg->ifindex,
		net_if_get_by_iface(cfg->iface));
#endif /* CONFIG_NET_L2_VIRTUAL */
}

static void setup_sntp(const struct net_config_iface *cfg)
{
#if defined(CONFIG_SNTP)
	int ret;

	/* TODO: Deprecate and ignore CONFIG_NET_CONFIG_CLOCK_SNTP_INIT and only
	 * check whether a config is available.
	 */
	/* TODO: We do not yet have interface-specific clocks, therefore only use the
	 * first SNTP config found and warn if more are available.
	 */
	if (IS_ENABLED(CONFIG_NET_CONFIG_CLOCK_SNTP_INIT) && cfg->iface &&
	    cfg->sntp_server->server_name) {
		ret = net_init_clock_via_sntp(cfg->iface, cfg->sntp_server->server_name,
					      cfg->sntp_server->timeout);
		if (ret < 0) {
			NET_WARN("Cannot init SNTP iface %d (%d)", net_if_get_by_iface(cfg->iface),
				 ret);
		} else {
			NET_DBG("Initialized SNTP to use iface %d",
				net_if_get_by_iface(cfg->iface));
		}
	}
#endif /* CONFIG_SNTP */
}

static void activate_log_backend(void)
{
	if (IS_ENABLED(CONFIG_LOG_BACKEND_NET) && IS_ENABLED(CONFIG_LOG_BACKEND_NET_AUTOSTART)) {
		const struct log_backend *backend = log_backend_net_get();

		if (!log_backend_is_active(backend)) {
			if (backend->api->init != NULL) {
				backend->api->init(backend);
			}

			log_backend_activate(backend, NULL);
		}
	}
}

#if defined(CONFIG_NET_NATIVE)
static void iface_up_handler(struct net_mgmt_event_callback *cb, uint32_t mgmt_event,
			     struct net_if *iface)
{
	if (mgmt_event == NET_EVENT_IF_UP) {
		NET_INFO("Iface %d (%p) coming up", net_if_get_by_iface(iface), iface);

		k_sem_reset(&counter);
		k_sem_give(&waiter);
	}
}

static bool check_interface(struct net_if *iface)
{
	if (net_if_is_up(iface)) {
		k_sem_reset(&counter);
		k_sem_give(&waiter);
		return true;
	}

	NET_INFO("Waiting for iface %d (%p) to be up...", net_if_get_by_iface(iface), iface);

	net_mgmt_init_event_callback(&mgmt_iface_cb, iface_up_handler, NET_EVENT_IF_UP);
	net_mgmt_add_event_callback(&mgmt_iface_cb);

	return false;
}
#else
static bool check_interface(struct net_if *iface)
{
	k_sem_reset(&counter);
	k_sem_give(&waiter);

	return true;
}
#endif

int net_config_init_one(struct net_if *iface, const char *app_info, int32_t timeout)
{
#define LOOP_DIVIDER 10
	struct net_config_iface *cfg;
	int loop, loop_count;
	int ret, ifindex;

	if (app_info) {
		NET_INFO("%s", app_info);
	}

	if (iface == NULL) {
		iface = net_if_get_default();
		NET_DBG("No iface given: using default iface");
	}

	if (!iface) {
		NET_WARN("No configurable iface found: ignoring config.");
		return -ENOENT;
	}

	cfg = get_cfg_by_iface(iface);

	/* Must run first because IEEE 802.15.4 interfaces must be configured while
	 * down. The driver therefore sets the no-auto-start flag by default. Also
	 * note that a low-level IEEE 802.15.4 config might be given w/o a net config
	 * as IEEE 802.15.4 star topologies do not necessarily require L3+ layers.
	 */
	setup_ieee802154(iface, cfg ? !((cfg->set_flags & ~cfg->clear_flags) & NET_IF_NO_AUTO_START)
				    : true);

	ifindex = net_if_get_by_iface(iface);

	if (!net_if_is_up(iface) && net_if_flag_is_set(iface, NET_IF_NO_AUTO_START)) {
		NET_WARN("Iface is not up: cannot configure iface %d", ifindex);
		return -ENETDOWN;
	}

	NET_DBG("Configuring iface %d (%p)", ifindex, iface);

	loop = (timeout < 0 ? CONFIG_NET_CONFIG_INIT_TIMEOUT * MSEC_PER_SEC : timeout) /
	       LOOP_DIVIDER;

	if (timeout == 0) {
		loop_count = 0;
	} else {
		loop_count = LOOP_DIVIDER;
	}

	/* First make sure that the interface is up */
	if (check_interface(iface) == false) {
		k_sem_init(&counter, 1, K_SEM_MAX_LIMIT);

		while (loop_count-- > 0) {
			if (!k_sem_count_get(&counter)) {
				break;
			}

			if (k_sem_take(&waiter, K_MSEC(loop))) {
				if (!k_sem_count_get(&counter)) {
					break;
				}
			}
		}

#if defined(CONFIG_NET_NATIVE)
		net_mgmt_del_event_callback(&mgmt_iface_cb);
#endif
	}

	/* Network interface did not come up. */
	if (timeout > 0 && loop_count < 0) {
		NET_WARN("Timeout while waiting for network iface %d", ifindex);
		return -ENETDOWN;
	}

	if (cfg) {
		/* Do we need to change the interface name */
		if (cfg->set_iface_name != NULL) {
			ret = net_if_set_name(iface, cfg->set_iface_name);
			if (ret < 0) {
				NET_WARN("Cannot %s %s %d to \"%s\" (%d)", "rename",
					 "network iface", ifindex, cfg->set_iface_name, ret);
			} else {
				NET_DBG("Changed name of %s %d to \"%s\"", "network iface", ifindex,
					cfg->set_iface_name);
			}
		}

		setup_vlan(cfg);
		setup_ipv4(cfg);
		setup_ipv6(cfg);
		setup_virtual_l2(cfg);
		setup_sntp(cfg);
	} else {
		NET_WARN("No configuration found for iface %d", ifindex);
	}

	/* This is activated late as it requires the network stack to be up and
	 * running before syslog messages can be sent to network. It is safe to call
	 * this for every interface as the function is idempotent.
	 */
	activate_log_backend();

	return 0;
}

/* deprecated - for backwards compat only */
int net_config_init_by_iface(struct net_if *iface, const char *app_info, uint32_t services,
			     int32_t timeout)
{
	ARG_UNUSED(services);

	return net_config_init_one(iface, app_info, timeout);
}

/* deprecated - for backwards compat only */
int net_config_init(const char *app_info, uint32_t services, int32_t timeout)
{
	ARG_UNUSED(services);
	return net_config_init_one(NULL, app_info, timeout);
}

/* deprecated - for backwards compat only */
int net_config_init_app(const struct device *dev, const char *app_info)
{
	struct net_if *iface = NULL;
	int ret;

	if (dev) {
		iface = net_if_lookup_by_dev(dev);
		if (iface == NULL) {
			NET_WARN("No iface for device %p, using default", dev);
		}
	} else {
		NET_WARN("No device given, using default");
	}

	ret = net_config_init_one(iface, app_info, CONFIG_NET_CONFIG_INIT_TIMEOUT * MSEC_PER_SEC);
	if (ret < 0) {
		NET_ERR("Initialization of network device %p failed (%d)", dev, ret);
	}

	return ret;
}

int net_config_init_all(const char *app_info, int32_t timeout)
{
	STRUCT_SECTION_FOREACH(net_if, iface) {
		/* Cannot be implemented with net_if_foreach() as it doesn't allow to
		 * break the loop on error.
		 */
		int ret = net_config_init_one(iface, app_info, timeout);

		if (ret < 0) {
			NET_ERR("Initialization of network iface %d failed (%d)",
				net_if_get_by_iface(iface), ret);
			return ret;
		}
	}

	return 0;
}

#if defined(CONFIG_NET_CONFIG_AUTO_INIT)
static int init_app(void)
{
#if defined(CONFIG_NET_DHCPV4_SERVER)
	/* If we are starting DHCPv4 server, then the socket service needs to be started before
	 * this config lib as the server will need to use the socket service.
	 */
	BUILD_ASSERT(CONFIG_NET_SOCKETS_SERVICE_THREAD_PRIO < CONFIG_NET_CONFIG_INIT_PRIO);
#endif

	(void)net_config_init_all("Initializing network",
				  CONFIG_NET_CONFIG_INIT_TIMEOUT * MSEC_PER_SEC);

	return 0;
}

SYS_INIT(init_app, APPLICATION, CONFIG_NET_CONFIG_INIT_PRIO);
#endif /* CONFIG_NET_CONFIG_AUTO_INIT */
