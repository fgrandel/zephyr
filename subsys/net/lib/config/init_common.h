/* L3+ network settings header */

/*
 * Copyright (c) 2024 The Zephyr Project.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef __NET_LIB_CONFIG_COMMON_H__
#define __NET_LIB_CONFIG_COMMON_H__

#include <stdbool.h>

#include <zephyr/devicetree.h>
#include <zephyr/device.h>

#define NET_CONFIG_NET_DEVICE(iface_node_id) DT_PARENT(iface_node_id)
#define NET_CONFIG_NET_DEVICE_ORD_NAME(iface_node_id)                                              \
	DEVICE_DT_NAME_GET(NET_CONFIG_NET_DEVICE(iface_node_id))
#define NET_CONFIG_NET_DEVICE_NAME(iface_node_id)                                                  \
	DEVICE_DT_NAME(NET_CONFIG_NET_DEVICE(iface_node_id))

#define NET_CONFIG_SUBNODE(_node_id, _subnode) DT_CHILD(_node_id, _subnode)

#define NET_CONFIG_SUBNODE2(_node_id, _subnode, _subnode2)                                         \
	DT_CHILD(DT_CHILD(_node_id, _subnode), _subnode2)

#define NET_CONFIG_IF_SUBSTATE(_node_id, _subnode, _bracketed_then)                                \
	IF_ENABLED(DT_NODE_HAS_STATUS(NET_CONFIG_SUBNODE(_node_id, _subnode), okay),               \
		   _bracketed_then)

#define NET_CONFIG_COND_SUBSTATE(_node_id, _subnode, _bracketed_then, _bracketed_else)             \
	COND_CODE_1(DT_NODE_HAS_STATUS(NET_CONFIG_SUBNODE(_node_id, _subnode), okay),              \
		    _bracketed_then, _bracketed_else)

#define NET_CONFIG_SUBSTATE_NAME(_iface_node_id, _subnode)                                         \
	CONCAT(NET_CONFIG_NET_DEVICE_ORD_NAME(_iface_node_id), _net_config_, _subnode)

#define NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, _subnode, _subnode2)                             \
	CONCAT(NET_CONFIG_SUBSTATE_NAME(_iface_node_id, _subnode), _, _subnode2)

#define NET_CONFIG_SUBSTATE_PTR_OR_NULL(_iface_node_id, _subnode)                                  \
	NET_CONFIG_COND_SUBSTATE(_iface_node_id, _subnode,                                         \
				 (&NET_CONFIG_SUBSTATE_NAME(_iface_node_id, _subnode)), (NULL))

#define NET_CONFIG_SUBSTATE2_PTR_OR_NULL(_iface_node_id, _subnode, _subnode2)                      \
	NET_CONFIG_COND_SUBSTATE(                                                                  \
		NET_CONFIG_SUBNODE(_iface_node_id, _subnode), _subnode2,                           \
		(&NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, _subnode, _subnode2)), (NULL))

#define NET_CONFIG_SUBSTATE2_OR_NULL(_iface_node_id, _subnode, _subnode2)                          \
	NET_CONFIG_COND_SUBSTATE(NET_CONFIG_SUBNODE(_iface_node_id, _subnode), _subnode2,          \
				 (NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, _subnode, _subnode2)), \
				 (NULL))

#define NET_CONFIG_SUBSTATE_STR_ARRAY(_iface_node_id, _subnode, _str_array_field)                  \
	static const char *NET_CONFIG_SUBSTATE2_NAME(_iface_node_id, _subnode,                     \
						     _str_array_field)[] =                         \
		DT_PROP(NET_CONFIG_SUBNODE(_iface_node_id, _subnode), _str_array_field)

#endif /* __NET_LIB_CONFIG_COMMON_H__ */
