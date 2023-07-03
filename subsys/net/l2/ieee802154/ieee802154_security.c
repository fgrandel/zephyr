/*
 * Copyright (c) 2017 Intel Corporation.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief 802.15.4 6LoWPAN authentication and encryption implementation
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 */

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(net_ieee802154_security, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

#include "ieee802154_frame.h"
#include "ieee802154_security.h"

#include <zephyr/crypto/crypto.h>
#include <zephyr/net/net_core.h>

extern const uint8_t level_2_authtag_len[4];

int ieee802154_security_setup_session(struct ieee802154_security_ctx *sec_ctx, uint8_t level,
				      uint8_t key_mode, uint8_t *key, uint8_t key_len)
{
	uint8_t authtag_len;
	int ret;

	if (level > IEEE802154_SECURITY_LEVEL_ENC_MIC_128 ||
	    key_mode > IEEE802154_KEY_ID_MODE_SRC_8_INDEX) {
		return -EINVAL;
	}

	/* TODO: supporting other key modes */
	if (level > IEEE802154_SECURITY_LEVEL_NONE &&
	    (key_len > IEEE802154_KEY_MAX_LEN || !key ||
	     key_mode != IEEE802154_KEY_ID_MODE_IMPLICIT)) {
		return -EINVAL;
	}

	sec_ctx->level = level;

	if (level == IEEE802154_SECURITY_LEVEL_NONE) {
		return 0;
	}


	if (level > IEEE802154_SECURITY_LEVEL_ENC) {
		authtag_len = level_2_authtag_len[level - 4];
	} else if (level < IEEE802154_SECURITY_LEVEL_ENC) {
		authtag_len = level_2_authtag_len[level];
	} else {
		/* Encryption-only security is no longer supported since IEEE 802.15.4-2020. */
		return -EINVAL;
	}
	sec_ctx->enc.mode_params.ccm_info.tag_len = authtag_len;
	sec_ctx->dec.mode_params.ccm_info.tag_len = authtag_len;

	memcpy(sec_ctx->key, key, key_len);
	sec_ctx->key_len = key_len;
	sec_ctx->key_mode = key_mode;

	sec_ctx->enc.key.bit_stream = sec_ctx->key;
	sec_ctx->enc.keylen = sec_ctx->key_len;

	sec_ctx->dec.key.bit_stream = sec_ctx->key;
	sec_ctx->dec.keylen = sec_ctx->key_len;

	ret = cipher_begin_session(sec_ctx->enc.device, &sec_ctx->enc, CRYPTO_CIPHER_ALGO_AES,
				   CRYPTO_CIPHER_MODE_CCM, CRYPTO_CIPHER_OP_ENCRYPT);
	if (ret) {
		NET_ERR("Could not setup encryption context");

		return ret;
	}

	ret = cipher_begin_session(sec_ctx->dec.device, &sec_ctx->dec, CRYPTO_CIPHER_ALGO_AES,
				   CRYPTO_CIPHER_MODE_CCM, CRYPTO_CIPHER_OP_DECRYPT);
	if (ret) {
		NET_ERR("Could not setup decryption context");
		cipher_free_session(sec_ctx->enc.device, &sec_ctx->enc);

		return ret;
	}

	return 0;
}

void ieee802154_security_teardown_session(struct ieee802154_security_ctx *sec_ctx)
{
	if (sec_ctx->level == IEEE802154_SECURITY_LEVEL_NONE) {
		return;
	}

	cipher_free_session(sec_ctx->enc.device, &sec_ctx->enc);
	cipher_free_session(sec_ctx->dec.device, &sec_ctx->dec);
	sec_ctx->level = IEEE802154_SECURITY_LEVEL_NONE;
}

static bool prepare_aead(int frame_type, uint8_t *frame, uint8_t level, uint8_t ll_hdr_len,
			 uint8_t payload_len, uint8_t authtag_len, uint16_t pan_id,
			 struct ieee802154_address *src_addr, int src_addr_mode,
			 uint64_t frame_counter_or_asn, struct cipher_aead_pkt *apkt,
			 struct cipher_pkt *pkt, uint8_t nonce[13], bool tsch_mode)
{
	bool is_encrypted;
	uint8_t out_buf_offset;

	__ASSERT_NO_MSG(level != IEEE802154_SECURITY_LEVEL_ENC &&
			level != IEEE802154_SECURITY_LEVEL_NONE);

	if (tsch_mode) {
		/* Enhanced Beacon frames in TSCH mode shall not be encrypted,
		 * but may be authenticated, see section 6.3.6.
		 */
		if (frame_type == IEEE802154_FRAME_TYPE_BEACON &&
		    level > IEEE802154_SECURITY_LEVEL_ENC) {
			level -= 4U;
		}

		/* See section 9.3.3.2 */
		if (src_addr_mode == IEEE802154_ADDR_MODE_SHORT) {
			/* IEEE 802.15 CID */
			nonce[0] = 0xba;
			nonce[1] = 0x55;
			nonce[2] = 0xec;

			nonce[3] = 0x00;

			sys_put_be16(pan_id, &nonce[4]);
			sys_put_be16(sys_le16_to_cpu(src_addr->short_addr), &nonce[6]);
		} else if (src_addr_mode == IEEE802154_ADDR_MODE_EXTENDED) {
			memcpy(nonce, src_addr, IEEE802154_EXT_ADDR_LENGTH);
		} else {
			return false;
		}
		nonce[8] = frame_counter_or_asn >> 32;
		sys_put_be32(frame_counter_or_asn, &nonce[9]);
	} else {
		/* See section 9.3.3.1 */
		if (src_addr_mode != IEEE802154_ADDR_MODE_EXTENDED) {
			/* TODO: Handle src short address.
			 * This will require to look up in nbr cache with short addr
			 * in order to get the extended address related to it.
			 */
			return false;
		}

		memcpy(nonce, src_addr, IEEE802154_EXT_ADDR_LENGTH);
		sys_put_be32(frame_counter_or_asn, &nonce[8]);
		nonce[12] = level;
	}

	is_encrypted = level > IEEE802154_SECURITY_LEVEL_ENC;

	/* See section 9.3.5.3 */
	pkt->in_buf = is_encrypted && payload_len ? frame + ll_hdr_len : NULL;
	pkt->in_len = is_encrypted ? payload_len : 0;

	/* See section 9.3.5.4 */
	out_buf_offset = is_encrypted ? ll_hdr_len : ll_hdr_len + payload_len;

	pkt->out_buf = frame + out_buf_offset;
	pkt->out_buf_max = (is_encrypted ? payload_len : 0) + authtag_len;

	apkt->ad = frame;
	apkt->ad_len = out_buf_offset;
	apkt->tag = frame + ll_hdr_len + payload_len;
	apkt->pkt = pkt;

	return true;
}

bool ieee802154_decrypt_auth(struct ieee802154_context *ctx, int frame_type, uint8_t *frame,
			     uint8_t ll_hdr_len, uint8_t payload_len, uint8_t authtag_len,
			     struct ieee802154_address *src_addr, int src_addr_mode,
			     uint64_t frame_counter_or_asn)
{
	struct ieee802154_security_ctx *sec_ctx = &ctx->sec_ctx;
	struct cipher_aead_pkt apkt;
	struct cipher_pkt pkt;
	uint8_t nonce[13];
	int ret;

	if (!prepare_aead(frame_type, frame, sec_ctx->level, ll_hdr_len, payload_len, authtag_len,
			  ctx->pan_id, src_addr, src_addr_mode, frame_counter_or_asn, &apkt, &pkt,
			  nonce, IEEE802154_TSCH_MODE_ON(ctx))) {
		return false;
	}

	ret = cipher_ccm_op(&sec_ctx->dec, &apkt, nonce);
	if (ret) {
		NET_DBG("Cannot decrypt/auth (%i): %p %u/%u - fc/asn %" PRIu64, ret, frame,
			ll_hdr_len, payload_len, frame_counter_or_asn);
		return false;
	}

	return true;
}

bool ieee802154_encrypt_auth(struct ieee802154_context *ctx, int frame_type, uint8_t *frame,
			     uint8_t ll_hdr_len, uint8_t payload_len, uint8_t authtag_len,
			     struct ieee802154_address *src_addr, int src_addr_mode,
			     uint64_t frame_counter_or_asn)
{
	struct ieee802154_security_ctx *sec_ctx = &ctx->sec_ctx;
	struct cipher_aead_pkt apkt;
	struct cipher_pkt pkt;
	uint8_t nonce[13];
	int ret;

	if (!prepare_aead(frame_type, frame, sec_ctx->level, ll_hdr_len, payload_len, authtag_len,
			  ctx->pan_id, src_addr, src_addr_mode, frame_counter_or_asn, &apkt, &pkt,
			  nonce, IEEE802154_TSCH_MODE_ON(ctx))) {
		return false;
	}

	ret = cipher_ccm_op(&sec_ctx->enc, &apkt, nonce);
	if (ret) {
		NET_DBG("Cannot encrypt/auth (%i): frame %p - len %u - fc/asn %" PRIu64, ret, frame,
			payload_len, frame_counter_or_asn);
		return false;
	}

	return true;
}

int ieee802154_security_init(struct ieee802154_security_ctx *sec_ctx)
{
	const struct device *dev;

	(void)memset(&sec_ctx->enc, 0, sizeof(struct cipher_ctx));
	(void)memset(&sec_ctx->dec, 0, sizeof(struct cipher_ctx));

	dev = device_get_binding(CONFIG_NET_L2_IEEE802154_SECURITY_CRYPTO_DEV_NAME);
	if (!dev) {
		return -ENODEV;
	}

	sec_ctx->enc.flags = crypto_query_hwcaps(dev);
	sec_ctx->dec.flags = crypto_query_hwcaps(dev);

	sec_ctx->enc.mode_params.ccm_info.nonce_len = 13U;
	sec_ctx->dec.mode_params.ccm_info.nonce_len = 13U;

	sec_ctx->enc.device = dev;
	sec_ctx->dec.device = dev;

	return 0;
}
