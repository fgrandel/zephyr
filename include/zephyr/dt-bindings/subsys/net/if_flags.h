/*
 * Copyright (c) 2024 The Zephyr Project
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef __DT_SUBSYS_NET_IF_FLAGS_H
#define __DT_SUBSYS_NET_IF_FLAGS_H

/* see `net_if_flag` in net_if.h */
#define NET_IF_POINTOPOINT        0x00000001 /* BIT(0) */
#define NET_IF_PROMISC            0x00000002 /* BIT(1) */
#define NET_IF_NO_AUTO_START      0x00000004 /* BIT(2) */
#define NET_IF_FORWARD_MULTICASTS 0x00000020 /* BIT(5) */
#define NET_IF_IPV6_NO_ND         0x00000800 /* BIT(11) */
#define NET_IF_IPV6_NO_MLD        0x00001000 /* BIT(12) */

#endif /* __DT_SUBSYS_NET_IF_FLAGS_H */
