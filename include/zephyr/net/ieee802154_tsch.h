/*
 * Copyright (c) 2023, Florian Grandel, Zephyr Project
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 L2 TSCH specific structures
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 *
 * All values are in CPU byte order unless otherwise noted.
 */

#ifndef ZEPHYR_INCLUDE_NET_IEEE802154_TSCH_H_
#define ZEPHYR_INCLUDE_NET_IEEE802154_TSCH_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief IEEE 802.15.4 TSCH (time slotted channel hopping) library
 * @defgroup ieee802154_tsch IEEE 802.15.4 TSCH Library
 * @ingroup networking
 * @{
 */

/* see section 8.4.3.3.3, table 8-98 */
struct ieee802154_tsch_link {
	sys_sfnode_t sfnode; /* must be first member */
	struct net_linkaddr node_addr;
	uint16_t handle;
	uint16_t timeslot;
	uint16_t channel_offset;
	uint8_t slotframe_handle;
	uint8_t tx: 1;          /* 1 = TX link */
	uint8_t rx: 1;          /* 1 = RX link */
	uint8_t shared: 1;      /* 1 = shared link */
	uint8_t timekeeping: 1; /* 1 = timekeeping link */
	uint8_t priority: 1;    /* 1 = link for high priority traffic */
	uint8_t advertising: 1; /* 0: NORMAL, 1: ADVERTISING */
	uint8_t advertise: 1;   /* 1 = advertised in beacons */
};

/* see section 8.4.3.3.2, table 8-97 */
struct ieee802154_tsch_slotframe {
	sys_sfnode_t sfnode;     /* must be first member */
	sys_sflist_t link_table; /* see section 8.4.3.3.3, protected by the ctx lock */
	uint16_t size;           /* number of timeslots in the slotframe */
	uint8_t handle;          /* identifier of the slotframe */
	uint8_t advertise: 1;    /* true = advertised in beacons */
};

/* see section 8.4.3.3.4, table 8-99 */
struct ieee802154_tsch_timeslot_template {
	uint16_t cca_offset; /* The time between the beginning of timeslot and start of CCA
			      * operation, in us.
			      */
	uint16_t cca;	     /* Duration of CCA, in us. */
	uint16_t tx_offset; /* The time between the beginning of the timeslot and the start of frame
			     * transmission, in us.
			     */
	uint16_t rx_offset; /* Beginning of the timeslot to when the receiver shall be listening, in
			     * us.
			     */
	uint16_t rx_ack_delay; /* End of frame to when the transmitter shall listen for
				* acknowledgment, in us.
				*/
	uint16_t tx_ack_delay; /* End of frame to start of acknowledgment, in us. */
	uint16_t rx_wait;      /* The time to wait for start of frame, in us. */
	uint16_t rx_tx;	       /* Transmit to Receive turnaround, in us. */
	uint16_t max_ack;      /* Transmission time to send an acknowledgment, in us. */
	uint16_t ack_wait;     /* Minimum time to wait for the start of an acknowledgment in us. */
	uint32_t max_tx : 20;  /* Transmission time to send the maximum length frame, in us. */
	uint32_t _unused_1 : 12;
	uint32_t length : 20; /* The total length of the timeslot including any unused time after
			       * frame transmission and acknowledgment, in us.
			       */
	uint32_t _unused_2 : 12;
};

/**
 * @}
 */

#endif /* ZEPHYR_INCLUDE_NET_IEEE802154_TSCH_H_ */
