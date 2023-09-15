/*
 * Copyright (c) 2019 Brett Witherspoon
 * Copyright (c) 2020 Friedt Professional Engineering Services, Inc
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef ZEPHYR_DRIVERS_IEEE802154_IEEE802154_CC13XX_CC26XX_SUBG_H_
#define ZEPHYR_DRIVERS_IEEE802154_IEEE802154_CC13XX_CC26XX_SUBG_H_

#include <zephyr/devicetree.h>
#include <zephyr/drivers/pinctrl.h>
#include <zephyr/kernel.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/ieee802154.h>
#include <zephyr/net/ieee802154_radio.h>

#include <ti/drivers/rf/RF.h>

#include <driverlib/rf_common_cmd.h>
#include <driverlib/rf_data_entry.h>
#include <driverlib/rf_ieee_cmd.h>
#include <driverlib/rf_prop_cmd.h>
#include <driverlib/rf_mailbox.h>

#define DT_DRV_COMPAT ti_cc13xx_cc26xx_ieee802154_subghz

#define DT_DRIVER_DEVICE    DEVICE_DT_INST_GET(0)
#define DT_RADIO            DT_PARENT(DT_DRV_INST(0))

#define PINCTRL_STATE_UNDEFINED -1
#define PINCTRL_STATE_OFF       0
#define PINCTRL_STATE_RX        1
#define PINCTRL_STATE_TX        2
#define PINCTRL_STATE_ALL       3

#define PINCTRL_STATE2(idx) PINCTRL_STATE_##idx
#define PINCTRL_STATE(idx)  PINCTRL_STATE2(idx)

#define DT_DEBUG_PIN_DEV_CONFIG PINCTRL_DT_DEV_CONFIG_GET(DT_RADIO)
#define DT_DEBUG_PIN_STATE                                                                         \
	PINCTRL_STATE(DT_STRING_UPPER_TOKEN_OR(DT_RADIO, debug_pins, UNDEFINED))

#define NET_TIME_DEBUG_PIN                                                                         \
	(DT_DEBUG_PIN_STATE != PINCTRL_STATE_UNDEFINED) && (DT_DEBUG_PIN_STATE != PINCTRL_STATE_OFF)

#define CC13XX_CC26XX_NUM_RX_BUF \
	CONFIG_IEEE802154_CC13XX_CC26XX_SUB_GHZ_NUM_RX_BUF

/* TODO: Increase CC13XX_CC26XX_RX_BUF_LEN_SIZE to 2 bytes when implementing SUN
 * PHYs with 2047 bytes payload, see section 11.3, table 11-1, aMaxPhyPacketSize.
 */
#define CC13XX_CC26XX_RX_BUF_LEN_SIZE 1

/* TODO: Support 4-byte CRC when implementing SUN PHYs with 2047 bytes payload. */
#define CC13XX_CC26XX_RX_BUF_CRC_SIZE                                                              \
	(IS_ENABLED(CONFIG_IEEE802154_RAW_MODE) ? IEEE802154_FCS_LENGTH : 0)

#define CC13XX_CC26XX_RX_BUF_RSSI_SIZE 1

#define CC13XX_CC26XX_RX_BUF_TIMESTAMP_SIZE                                                        \
	(IS_ENABLED(CONFIG_NET_PKT_TIMESTAMP) ? sizeof(ratmr_t) : 0)

#define CC13XX_CC26XX_RX_BUF_STATUS_SIZE 1

#define CC13XX_CC26XX_RX_BUF_ADDITIONAL_DATA_SIZE                                                  \
	(CC13XX_CC26XX_RX_BUF_CRC_SIZE + CC13XX_CC26XX_RX_BUF_RSSI_SIZE +                          \
	 CC13XX_CC26XX_RX_BUF_TIMESTAMP_SIZE + CC13XX_CC26XX_RX_BUF_STATUS_SIZE)

#define CC13XX_CC26XX_RX_BUF_SIZE                                                                  \
	(CC13XX_CC26XX_RX_BUF_LEN_SIZE + IEEE802154_MAX_PHY_PACKET_SIZE +                          \
	 CC13XX_CC26XX_RX_BUF_ADDITIONAL_DATA_SIZE)

#define CC13XX_CC26XX_TX_BUF_SIZE (IEEE802154_PHY_SUN_FSK_PHR_LEN + IEEE802154_MAX_PHY_PACKET_SIZE)

#define CC13XX_CC26XX_INVALID_RSSI INT8_MIN

struct ieee802154_cc13xx_cc26xx_subg_data {
	/* protects writable data and serializes access to the API */
	struct k_sem lock;

	RF_Handle rf_handle;
	RF_Object rf_object;

	struct net_if *iface;
	uint8_t mac[8]; /* in big endian */

	struct {
		uint16_t channel;
		net_time_t start;
		net_time_t duration;
	} rx_slot;

	bool is_up;

	dataQueue_t rx_queue;
	rfc_dataEntryPointer_t rx_entry[CC13XX_CC26XX_NUM_RX_BUF];
	uint8_t rx_data[CC13XX_CC26XX_NUM_RX_BUF][CC13XX_CC26XX_RX_BUF_SIZE];
	uint8_t tx_data[CC13XX_CC26XX_TX_BUF_SIZE];

	/* Common Radio Commands */
	volatile rfc_CMD_FS_t cmd_fs;

	/* Sub-GHz Radio Commands */
	volatile rfc_CMD_PROP_RX_ADV_t cmd_prop_rx_adv;
	volatile rfc_CMD_PROP_TX_ADV_t cmd_prop_tx_adv;
	volatile rfc_propRxOutput_t cmd_prop_rx_adv_output;
	volatile rfc_CMD_PROP_CS_t cmd_prop_cs;

	RF_CmdHandle rx_cmd_handle;
};

#endif /* ZEPHYR_DRIVERS_IEEE802154_IEEE802154_CC13XX_CC26XX_SUBG_H_ */
