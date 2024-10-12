/*
 * SPDX-License-Identifier: Apache-2.0
 * Copyright (c) 2024 The Linux Project
 */

/**
 * @file
 * @brief Configuration macro target main header
 *
 * API for accessing the current application's configuration tree macros.
 */

#ifndef CONFIGURATION_H
#define CONFIGURATION_H

#include <zephyr/config_generated.h>

#include <zephyr/sys/util.h>

/**
 * @brief configuration.h API
 * @defgroup configuration Configuration Subsystem
 * @since 4.0
 * @version 0.0.1
 * @{
 * @}
 */

#define CFG_ROOT CFG_N

#define CFG_CAT(a1, a2)                  a1##a2
#define CFG_CAT3(a1, a2, a3)             a1##a2##a3
#define CFG_CAT4(a1, a2, a3, a4)         a1##a2##a3##a4
#define CFG_CAT6(a1, a2, a3, a4, a5, a6) a1##a2##a3##a4##a5##a6

#define CFG_DASH_PREFIX(name) _##name
#define CFG_DASH(...)         MACRO_MAP_CAT(CFG_DASH_PREFIX, __VA_ARGS__)
#define CFG_S_PREFIX(name)    _S_##name

#define CFG_NODELABEL(label) DT_CAT(CFG_N_NODELABEL_, label)

#define CFG_PARENT(node_id)       CFG_CAT(node_id, _PARENT)
#define CFG_CHILD(node_id, child) UTIL_CAT(node_id, CFG_S_PREFIX(child))

#define CFG_NODE_HAS_PROP(node_id, prop) IS_ENABLED(CFG_CAT4(node_id, _P_, prop, _EXISTS))
#define CFG_PROP(node_id, prop)          CFG_CAT3(node_id, _P_, prop)
#define CFG_PROP_OR(node_id, prop, default_value)                                                  \
	COND_CODE_1(CFG_NODE_HAS_PROP(node_id, prop), \
		    (CFG_PROP(node_id, prop)), (default_value))
#define CFG_PROP_LEN(node_id, prop) CFG_CAT4(node_id, _P_, prop, _LEN)

#define CFG_POINTER_BY_IDX(node_id, prop, idx) CFG_CAT6(node_id, _P_, prop, _IDX_, idx, _PH)
#define CFG_POINTER(node_id, prop)             CFG_POINTER_BY_IDX(node_id, prop, 0)

#define CFG_NODE_IS_ENABLED_INTERNAL(node_id) IS_ENABLED(CFG_CAT(node_id, _ENABLED))
#define CFG_NODE_IS_ENABLED(node_id)          CFG_NODE_IS_ENABLED_INTERNAL(node_id)

#define CFG_HAS_SCHEMA_ENABLED(schema) IS_ENABLED(CFG_CAT(CFG_SCHEMA_HAS_ENABLED_, schema))

#define CFG_FOREACH_ENABLED(schema, fn)                                                            \
	COND_CODE_1(CFG_HAS_SCHEMA_ENABLED(schema),                                                \
		    (UTIL_CAT(CFG_FOREACH_ENABLED_, schema)(fn)),                                  \
		    ())

#define CFG_CHILD_NUM_ENABLED(node_id)         CFG_CAT(node_id, _CHILD_NUM_ENABLED)
#define CFG_FOREACH_CHILD_ENABLED(node_id, fn) CFG_CAT(node_id, _FOREACH_CHILD_ENABLED)(fn)

#define CFG_NUM_INST_ENABLED(schema)                                                               \
	UTIL_AND(CFG_HAS_SCHEMA_ENABLED(schema),                                                   \
		 UTIL_CAT(CFG_N_INST, CFG_DASH(schema, NUM_ENABLED)))

#endif /* CONFIGURATION_H */
