/*
 * Copyright (c) 2021 2021 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/types.h>
#include <stddef.h>
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/iso.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>

#include <hal/nrf_rtc.h>

#define LATENCY_MS      20U                   /* 20.0 ms, FT=2 with CIS reliability policy on, otherwise ignored */
#define SDU_INTERVAL_US (10U * USEC_PER_MSEC) /* 10.0 ms */
#define NUM_RETRIES     1U                    /* number of times to retry sending the same SDU, influences the ISO interval length */

#define ISO_INT_UNIT_US   1250U

#define HAL_TICKER_CNTR_CLK_UNIT_FSEC 30517578125UL
#define HAL_TICKER_FSEC_PER_USEC      1000000000UL

#define HAL_TICKER_TICKS_TO_US(x)                                                                  \
	(((uint32_t)(((uint64_t)(x) * HAL_TICKER_CNTR_CLK_UNIT_FSEC) / HAL_TICKER_FSEC_PER_USEC)))

#define HAL_TICKER_CNTR_MASK       0x00FFFFFF
#define ISO_TIME_WRAPPING_POINT_US (HAL_TICKER_TICKS_TO_US(HAL_TICKER_CNTR_MASK))
#define ISO_TIME_SPAN_FULL_US      (ISO_TIME_WRAPPING_POINT_US + 1)

#define hal_ticker_now_ticks() nrf_rtc_counter_get(NRF_RTC0)
#define hal_ticker_now_usec()  HAL_TICKER_TICKS_TO_US(hal_ticker_now_ticks())

#define GUARD_TIME_USEC 3000U

uint32_t get_time_diff_us(uint32_t minuend, uint32_t subtrahend)
{
	__ASSERT_NO_MSG(minuend <= ISO_TIME_WRAPPING_POINT_US);

	uint32_t result = ((uint64_t)minuend + ISO_TIME_SPAN_FULL_US - subtrahend) %
			  ((uint64_t)ISO_TIME_SPAN_FULL_US);

	return result;
}

static void start_scan(void);

static struct bt_conn *default_conn;
static struct bt_iso_chan iso_chan;
static uint32_t seq_num;
NET_BUF_POOL_FIXED_DEFINE(tx_pool, 2, BT_ISO_SDU_BUF_SIZE(CONFIG_BT_ISO_TX_MTU),
			  CONFIG_BT_CONN_TX_USER_DATA_SIZE, NULL);

/**
 * @brief Calculate the SDU interval
 * 
 * Calculates the SDU interval based on current channel information.
 * 
 * This even works on a peripheral where the central's original configuration is
 * not known.
 * 
 * @param chan Pointer to the ISO channel
 * 
 * @return SDU interval in microseconds, -EINVAL if failed to obtain ISO info.
 */
static int32_t calculate_sdu_interval_us(struct bt_iso_chan *chan)
{
	uint32_t transport_latency_us, cig_sync_delay_us, flush_timeout_us;
	struct bt_iso_unicast_tx_info *iso_unicast_central_info;
	struct bt_iso_unicast_info *iso_unicast_info;
	struct bt_iso_info iso_info;
	int32_t sdu_interval_us;
	int err;

	err = bt_iso_chan_get_info(chan, &iso_info);
	if (err) {
		printk("Failed obtaining ISO info\n");
		return -EINVAL;
	}

	iso_unicast_info = &iso_info.unicast;
	iso_unicast_central_info = &iso_unicast_info->central;

	transport_latency_us = iso_unicast_central_info->latency;
	cig_sync_delay_us = iso_unicast_info->cig_sync_delay;
	flush_timeout_us = iso_unicast_central_info->flush_timeout * ISO_INT_UNIT_US;

	/* Transport_Latency = CIG_Sync_Delay + FT x ISO_Interval - SDU_Interval */
	sdu_interval_us = cig_sync_delay_us + flush_timeout_us - transport_latency_us;
	__ASSERT_NO_MSG(sdu_interval_us > 0);

	return sdu_interval_us;
}

static void iso_sdu_interval_work_handler(struct k_work *work);

static K_WORK_DEFINE(iso_sdu_interval_work, iso_sdu_interval_work_handler);

static void iso_timer_timeout(struct k_timer *timer);

static K_TIMER_DEFINE(iso_timer, iso_timer_timeout, NULL);

static struct bt_iso_tx_info last_iso_tx_info;

static void iso_timer_timeout(struct k_timer *timer)
{
	int32_t sdu_interval_us, timer_offset;
	uint32_t now_us, elapsed_us, next_ts;
	bool is_first_sdu_pending = (last_iso_tx_info.seq_num == 0);
	int skipped_sdu_intervals;

	ARG_UNUSED(timer);

	k_work_submit(&iso_sdu_interval_work);

	sdu_interval_us = calculate_sdu_interval_us(&iso_chan);
	if (sdu_interval_us < 0) {
		printk("Failed calculating the SDU interval\n");
		return;
	}

	if (is_first_sdu_pending) {
		k_timer_start(&iso_timer, K_USEC(sdu_interval_us), K_FOREVER);
		return;
	}

	/* Offset should be zero for unframed PDUs */
	__ASSERT_NO_MSG(last_iso_tx_info.offset == 0);

	now_us = hal_ticker_now_usec();
	if (now_us + GUARD_TIME_USEC <= last_iso_tx_info.ts) {
		next_ts = last_iso_tx_info.ts;
	} else {
		elapsed_us = get_time_diff_us(now_us + GUARD_TIME_USEC, last_iso_tx_info.ts);
		skipped_sdu_intervals = DIV_ROUND_UP(elapsed_us + GUARD_TIME_USEC, sdu_interval_us);

		/* Re-synchronize the timer. */
		next_ts = (last_iso_tx_info.ts + skipped_sdu_intervals * sdu_interval_us) %
			  ISO_TIME_SPAN_FULL_US;
	}

	timer_offset = get_time_diff_us(next_ts, (hal_ticker_now_usec() + GUARD_TIME_USEC) %
							   ISO_TIME_SPAN_FULL_US);
	k_timer_start(&iso_timer, K_USEC(timer_offset), K_FOREVER);
}

/**
 * @brief Send ISO data SDU
 *
 * This will send an increasing amount of ISO data, starting from 1 octet.
 *
 * First iteration : 0x00
 * Second iteration: 0x00 0x01
 * Third iteration : 0x00 0x01 0x02
 *
 * And so on, until it wraps around the configured ISO TX MTU (CONFIG_BT_ISO_TX_MTU)
 *
 * @param work Pointer to the work structure
 */
static void iso_sdu_interval_work_handler(struct k_work *work)
{
	static uint8_t buf_data[CONFIG_BT_ISO_TX_MTU];
	static bool is_first_sdu = true;
	static size_t len_to_send = 1;
	static bool data_initialized;

	bool is_first_sdu_pending = (last_iso_tx_info.seq_num == 0);
	struct net_buf *buf;
	int ret;

	ARG_UNUSED(work);

	if (!data_initialized) {
		for (int i = 0; i < ARRAY_SIZE(buf_data); i++) {
			buf_data[i] = (uint8_t)i;
		}

		data_initialized = true;
	}

	if (is_first_sdu || !is_first_sdu_pending) {
		is_first_sdu = false;

		buf = net_buf_alloc(&tx_pool, K_FOREVER);
		if (!buf) {
			printk("Failed to allocate buffer\n");
			return;
		}

		net_buf_reserve(buf, BT_ISO_CHAN_SEND_RESERVE);

		net_buf_add_mem(buf, buf_data, len_to_send);

		ret = bt_iso_chan_send(&iso_chan, buf, seq_num);
		if (ret < 0) {
			printk("Failed to send ISO data (%d)\n", ret);
			net_buf_unref(buf);
		}

		SEGGER_SYSVIEW_RecordU32x2(SEGGER_SYSVIEW_BLE_PRODUCE_PKT, seq_num,
					   len_to_send);

		seq_num++;

		len_to_send++;
		if (len_to_send > ARRAY_SIZE(buf_data)) {
			len_to_send = 1;
		}
	}

	ret = bt_iso_chan_get_tx_sync(&iso_chan, &last_iso_tx_info);
	if (ret && ret != -EACCES) {
		printk("Failed obtaining timestamp\n");
	}
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	char addr_str[BT_ADDR_LE_STR_LEN];
	int err;

	if (default_conn) {
		/* Already connected */
		return;
	}

	/* We're only interested in connectable events */
	if (type != BT_GAP_ADV_TYPE_ADV_IND && type != BT_GAP_ADV_TYPE_ADV_DIRECT_IND) {
		return;
	}

	bt_addr_le_to_str(addr, addr_str, sizeof(addr_str));
	printk("Device found: %s (RSSI %d)\n", addr_str, rssi);

	/* connect only to devices in close proximity */
	if (rssi < -50) {
		return;
	}

	if (bt_le_scan_stop()) {
		return;
	}

	err = bt_conn_le_create(addr, BT_CONN_LE_CREATE_CONN, BT_LE_CONN_PARAM_DEFAULT,
				&default_conn);
	if (err) {
		printk("Create conn to %s failed (%u)\n", addr_str, err);
		start_scan();
	}
}

static void start_scan(void)
{
	int err;

	/* This demo doesn't require active scan */
	err = bt_le_scan_start(BT_LE_SCAN_PASSIVE, device_found);
	if (err) {
		printk("Scanning failed to start (err %d)\n", err);
		return;
	}

	printk("Scanning successfully started\n");
}

static void iso_connected(struct bt_iso_chan *chan)
{
	printk("ISO Channel %p connected\n", chan);

	seq_num = 0U;

	/* start send timer */
	k_timer_start(&iso_timer, K_NO_WAIT, K_FOREVER);
}

static void iso_disconnected(struct bt_iso_chan *chan, uint8_t reason)
{
	printk("ISO Channel %p disconnected (reason 0x%02x)\n", chan, reason);
	k_timer_stop(&iso_timer);
}

static struct bt_iso_chan_ops iso_ops = {
	.connected = iso_connected,
	.disconnected = iso_disconnected,
};

static struct bt_iso_chan_io_qos iso_tx = {
	.sdu = CONFIG_BT_ISO_TX_MTU,
	.phy = BT_GAP_LE_PHY_2M,
	.rtn = NUM_RETRIES,
	.path = NULL,
};

static struct bt_iso_chan_qos iso_qos = {
	.tx = &iso_tx,
	.rx = NULL,
};

static void connected(struct bt_conn *conn, uint8_t err)
{
	struct bt_iso_connect_param connect_param;
	char addr[BT_ADDR_LE_STR_LEN];
	int iso_err;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	if (err) {
		printk("Failed to connect to %s (%u)\n", addr, err);

		bt_conn_unref(default_conn);
		default_conn = NULL;

		start_scan();
		return;
	}

	if (conn != default_conn) {
		return;
	}

	printk("Connected: %s\n", addr);

	connect_param.acl = conn;
	connect_param.iso_chan = &iso_chan;

	iso_err = bt_iso_chan_connect(&connect_param, 1);

	if (iso_err) {
		printk("Failed to connect iso (%d)\n", iso_err);
		return;
	}
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	if (conn != default_conn) {
		return;
	}

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	printk("Disconnected: %s (reason 0x%02x)\n", addr, reason);

	bt_conn_unref(default_conn);
	default_conn = NULL;

	start_scan();
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

int main(void)
{
	struct bt_iso_chan *channels[1];
	struct bt_iso_cig_param param;
	struct bt_iso_cig *cig;
	int err;

	err = bt_enable(NULL);
	if (err) {
		printk("Bluetooth init failed (err %d)\n", err);
		return 0;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}


	printk("Bluetooth initialized\n");

	tracing_sysview_ble_start();

	iso_chan.ops = &iso_ops;
	iso_chan.qos = &iso_qos;
#if defined(CONFIG_BT_SMP)
	iso_chan.required_sec_level = BT_SECURITY_L2,
#endif /* CONFIG_BT_SMP */

	channels[0] = &iso_chan;
	param.cis_channels = channels;
	param.num_cis = ARRAY_SIZE(channels);
	param.sca = BT_GAP_SCA_UNKNOWN;
	param.packing = BT_ISO_PACKING_SEQUENTIAL;
	param.framing = BT_ISO_FRAMING_UNFRAMED;
	param.c_to_p_latency = LATENCY_MS;
	param.p_to_c_latency = LATENCY_MS;
	param.c_to_p_interval = SDU_INTERVAL_US;
	param.p_to_c_interval = SDU_INTERVAL_US;

	err = bt_iso_cig_create(&param, &cig);

	if (err != 0) {
		printk("Failed to create CIG (%d)\n", err);
		return 0;
	}

	start_scan();

	return 0;
}
