/*
 * Copyright (c) 2023, Florian Grandel, Zephyr Project
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 L2 channel hopping specific structures
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 *
 * All values are in CPU byte order unless otherwise noted.
 */

#ifndef ZEPHYR_INCLUDE_NET_IEEE802154_CHANNEL_HOPPING_H_
#define ZEPHYR_INCLUDE_NET_IEEE802154_CHANNEL_HOPPING_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @addtogroup ieee802154
 * @{
 */

/* Default IEEE 802.15.4e hopping sequences,
 * obtained from https://gist.github.com/twatteyne/2e22ee3c1a802b685695
 * via Contiki-NG.
 */

/* 16 channels, sequence length 16, channel page zero, 2.4 GHz band */
#define IEEE802154_CHANNEL_HOPPING_SEQUENCE_2_4_GHZ_16_16                                          \
	{ 16, 17, 23, 18, 26, 15, 25, 22, 19, 11, 12, 13, 24, 14, 20, 21 }

/* 4 channels, sequence length 16, channel page zero, 2.4 GHz band */
#define IEEE802154_CHANNEL_HOPPING_SEQUENCE_2_4_GHZ_4_16                                           \
	{ 20, 26, 25, 26, 15, 15, 25, 20, 26, 15, 26, 25, 20, 15, 20, 25 }

/* 4 channels, sequence length 4, channel page zero, 2.4 GHz band */
#define IEEE802154_CHANNEL_HOPPING_SEQUENCE_2_4_GHZ_4_4                                            \
	{ 15, 25, 26, 20 }

/* 2 channels, sequence length 2, channel page zero, 2.4 GHz band */
#define IEEE802154_CHANNEL_HOPPING_SEQUENCE_2_4_GHZ_2_2                                            \
	{ 20, 25 }

/* 1 channel, sequence length 1, channel page zero, 2.4 GHz band */
#define IEEE802154_CHANNEL_HOPPING_SEQUENCE_2_4_GHZ_1_1                                            \
	{ 20 }

/* 1 channel, sequence length 1, channel page zero, 863 MHz band */
#define IEEE802154_CHANNEL_HOPPING_SEQUENCE_SUB_GHZ_1_1                                            \
	{ 0 }

/* 10 channels, sequence length 10, channel page zero, 915 MHz band */
#define IEEE802154_CHANNEL_HOPPING_SEQUENCE_SUB_GHZ_10_10                                          \
	{ 6, 2, 9, 3, 7, 4, 10, 8, 5, 1 }

/* see section 8.4.3.4, table 8-100 */
struct ieee802154_hopping_sequence {
	/* The hopping sequence list must contain channels valid within the
	 * context of the currently selected channel page.
	 *
	 * As this implies the number of channels available in the list and the
	 * channels selected for this list, we do not keep separate
	 * representations of macChannelPage, macNumberOfChannels,
	 * macPhyConfiguration and macExtendedBitmap.  It is the application's
	 * responsibility to update the hopping sequence if it switches to a
	 * different channel page.
	 *
	 * As we do not support unslotted hopping modes, macHopDwellTime may also
	 * not be configured.
	 *
	 * Currently we only support a single hopping sequence, therefore the
	 * macHoppingSequenceId is assumed to be 0.
	 */
	uint16_t *list;  /* macHoppingSequenceListLength */
	uint16_t length; /* macHoppingSequenceList */
};

/**
 * @}
 */

#endif /* ZEPHYR_INCLUDE_NET_IEEE802154_CHANNEL_HOPPING_H_ */
