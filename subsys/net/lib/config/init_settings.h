/* macro-target specific config header for init.c */

/*
 * Copyright (c) 2024 The Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef __NET_LIB_CONFIG_INIT_SETTINGS_H__
#define __NET_LIB_CONFIG_INIT_SETTINGS_H__

#include "init.h"

void net_config_target_pre_init(void);

/* TODO: Convert or map to Kconfig vars. */
#define NET_CONFIG_SETTINGS_NUM_IFACES              1
#define NET_CONFIG_SETTINGS_NUM_PREFIXES_PER_IFACES 1
#define NET_CONFIG_SETTINGS_NUM_SNTP_SERVERS        1

#endif /* __NET_LIB_CONFIG_INIT_SETTINGS_H__ */
