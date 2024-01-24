/** @file
 * @brief Routines for network subsystem initialization.
 */

/*
 * Copyright (c) 2017 Intel Corporation
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef ZEPHYR_INCLUDE_NET_NET_CONFIG_H_
#define ZEPHYR_INCLUDE_NET_NET_CONFIG_H_

#include <zephyr/types.h>
#include <zephyr/device.h>
#include <zephyr/net/net_if.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifdef CONFIG_NET_CONFIG_SETTINGS

/**
 * @brief Network configuration library
 * @defgroup net_config Network Configuration Library
 * @since 1.8
 * @version 0.8.0
 * @ingroup networking
 * @{
 */

/* Flags that tell what kind of functionality is needed by the client. */
/**
 * @deprecated no longer required - ignored
 *
 * @brief Application needs routers to be set so that connectivity to remote
 * network is possible. For IPv6 networks, this means that the device should
 * receive IPv6 router advertisement message before continuing.
 */
#define NET_CONFIG_NEED_ROUTER BIT(0)

/**
 * @deprecated no longer required - ignored
 *
 * @brief Application needs IPv6 subsystem configured and initialized.
 * Typically this means that the device has IPv6 address set.
 */
#define NET_CONFIG_NEED_IPV6 BIT(1)

/**
 * @deprecated no longer required - ignored
 *
 * @brief Application needs IPv4 subsystem configured and initialized.
 * Typically this means that the device has IPv4 address set.
 */
#define NET_CONFIG_NEED_IPV4 BIT(2)

/**
 * @deprecated Use `net_config_init_all()` or `net_config_init_one()` instead.
 *
 * @brief Initialize and configure the default network interface and the
 * services it depends upon (IP4/6, DHCP, SNTP, IEEE 802.15.4, if enabled).
 *
 * @details This will `call net_config_init_one()` without an interface.
 *
 * @param app_info String describing this application.
 * @param services no longer required (ignored)
 * @param timeout How long to wait for interfaces to be UP before canceling the
 * setup. If the timeout is set to a negative number, then the default timeout
 * is used, see `CONFIG_NET_CONFIG_INIT_TIMEOUT`. If the timeout is set to 0, it
 * is assumed that all interfaces are already up and not check will be performed
 * at all.
 *
 * @return 0 if ok, <0 if error.
 */
int net_config_init(const char *app_info, uint32_t services, int32_t timeout);

/**
 * @deprecated Use `net_config_init_one()` instead.
 *
 * @brief Initialize and configure a specific network interface and the services
 * it depends upon (IP4/6, DHCP, SNTP, IEEE 802.15.4, if enabled).
 *
 * @details If the network interface is set to NULL, then the default one is
 * used in the configuration.
 *
 * @param iface Initialize networking using this network interface.
 * @param app_info String describing this application.
 * @param services no longer required (ignored)
 * @param timeout How long to wait for interfaces to be UP before canceling the
 * setup. If the timeout is set to a negative number, then the default timeout
 * is used, see `CONFIG_NET_CONFIG_INIT_TIMEOUT`. If the timeout is set to 0, it
 * is assumed that all interfaces are already up and not check will be performed
 * at all.
 *
 * @return 0 if ok, <0 if error.
 */
int net_config_init_by_iface(struct net_if *iface, const char *app_info, uint32_t services,
			     int32_t timeout);

/**
 * @deprecated Use `net_config_init_all()` or `net_config_init_one()` instead.
 *
 * @brief Initialize and configure a specific network device and the services it
 * depends upon (IP4/6, DHCP, SNTP, IEEE 802.15.4, if enabled).
 *
 * @details If the network device is set to NULL, then the default one is used
 * in the configuration.
 *
 * @param dev Network device to use. The function will figure out what network
 * interface to use based on the device. If the device is NULL, then default
 * network interface is used by the function.
 * @param app_info String describing this application.
 *
 * @return 0 if ok, <0 if error.
 */
int net_config_init_app(const struct device *dev, const char *app_info);

/**
 * @brief Initialize and configure all network interfaces and services for which
 * a configuration is found (IP4/6, DHCP, SNTP, IEEE 802.15.4, if enabled).
 *
 * @details If CONFIG_NET_CONFIG_AUTO_INIT is set, then this function is called
 * automatically when the device boots. If that is not desired, unset the config
 * option and call the function manually when the application starts.
 *
 * @param app_info String describing this application.
 * @param timeout How long to wait per interface for the interface to be UP
 * before canceling the setup. If the timeout is set to a negative number, then
 * the default timeout is used, see `CONFIG_NET_CONFIG_INIT_TIMEOUT`. If the
 * timeout is set to 0, it is assumed that all interfaces are already up and not
 * check will be performed at all.
 *
 * @return 0 if ok, <0 if error.
 */
int net_config_init_all(const char *app_info, int32_t timeout);

/**
 * @brief Initialize and configure a specific network interface and the services it
 * depends upon (IP4/6, DHCP, SNTP, IEEE 802.15.4, if enabled).
 *
 * @details If the network interface is set to NULL, then the default one is
 * configured.
 *
 * @param iface Initialize networking using this network interface.
 * @param app_info String describing this application.
 * @param timeout How long to wait for interfaces to be UP before canceling the
 * setup. If the timeout is set to a negative number, then the default timeout
 * is used, see `CONFIG_NET_CONFIG_INIT_TIMEOUT`. If the timeout is set to 0, it
 * is assumed that all interfaces are already up and not check will be performed
 * at all.
 *
 * @return 0 if ok, <0 if error.
 */
int net_config_init_one(struct net_if *iface, const char *app_info, int32_t timeout);

#if defined(CONFIG_NET_L2_IEEE802154)
/**
 * @brief Configure the IEEE 802.15.4 radio driver of an interface.
 *
 * @details If CONFIG_NET_CONFIG_AUTO_INIT is set or any of the
 * net_config_init_*() functions is called, then this function is called
 * automatically. If you want to re-configure the radio driver separately after
 * a configuration change, then call the function manually.
 *
 * @note This function must be called while the interface is down.
 *
 * @param iface The network interface to configure. If set to NULL, then the
 * devicetree will be checked for a chosen-property "zephyr,ieee802154". If that
 * is not found, then the default network interface is configured.
 * @param start If set to true, then the network interface is started after the
 * configuration is done.
 *
 * @return 0 if ok, <0 if error.
 */
int net_config_init_ieee802154(struct net_if *iface, bool start);
#endif /* CONFIG_NET_L2_IEEE802154 */

/**
 * @brief Early initialization of the network configuration library.
 *
 * @details This function must be called before calling any of the other network
 * configuration functions. Calling it multiple times is safe.
 *
 * @note This function is automatically called when the network stack is being
 * initialized. Applications will usually not have to call this function.
 */
void net_config_pre_init(void);

#else /* CONFIG_NET_CONFIG_SETTINGS */
#define net_config_pre_init(...)
#endif /* CONFIG_NET_CONFIG_SETTINGS */

/**
 * @}
 */

#ifdef __cplusplus
}
#endif

#endif /* ZEPHYR_INCLUDE_NET_NET_CONFIG_H_ */
