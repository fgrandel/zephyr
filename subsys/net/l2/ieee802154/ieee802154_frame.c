/*
 * Copyright (c) 2016 Intel Corporation.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief IEEE 802.15.4 MAC frame related functions implementation
 *
 * All references to the spec refer to IEEE 802.15.4-2020.
 */

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(net_ieee802154_frame, CONFIG_NET_L2_IEEE802154_LOG_LEVEL);

#include "ieee802154_frame.h"
#include "ieee802154_mgmt_priv.h"
#include "ieee802154_security.h"

#include <zephyr/net/net_core.h>
#include <zephyr/net/net_if.h>

#include <ipv6.h>
#include <nbr.h>

#define dbg_print_fcf(fcf)                                                                         \
	NET_DBG("fcf(1): %u/%u/%u/%u/%u/%u", fcf->frame_type, fcf->security_enabled,               \
		fcf->frame_pending, fcf->ar, fcf->pan_id_comp, fcf->reserved);                     \
	NET_DBG("fcf(2): %u/%u/%u/%u/%u", fcf->seq_num_suppr, fcf->ie_present, fcf->dst_addr_mode, \
		fcf->frame_version, fcf->src_addr_mode)

#define BUF_TIMEOUT K_MSEC(50)

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
const uint8_t level_2_authtag_len[4] = {0, IEEE802154_AUTH_TAG_LENGTH_32,
					IEEE802154_AUTH_TAG_LENGTH_64,
					IEEE802154_AUTH_TAG_LENGTH_128};
#endif

static inline bool get_pan_id_comp(int dst_addr_mode, int src_addr_mode, uint16_t dst_pan_id,
				   uint16_t src_pan_id, bool *pan_id_comp)
{
	/* see section 7.2.2.6 */
	bool has_dst_addr = dst_addr_mode != IEEE802154_ADDR_MODE_NONE;
	bool has_src_addr = src_addr_mode != IEEE802154_ADDR_MODE_NONE;
	bool both_present = has_src_addr && has_dst_addr;

	*pan_id_comp = both_present && dst_pan_id == src_pan_id;

	return true;
}

/* see section 7.2.2.6 */
static inline bool verify_and_get_has_dst_pan_id(int dst_addr_mode, int src_addr_mode,
						 bool pan_id_comp, bool *has_dst_pan_id)
{
	bool has_dst_addr = dst_addr_mode != IEEE802154_ADDR_MODE_NONE;
	bool has_src_addr = src_addr_mode != IEEE802154_ADDR_MODE_NONE;
	bool both_present = has_src_addr && has_dst_addr;

	if (!both_present && pan_id_comp != 0) {
		return false;
	}

	*has_dst_pan_id = both_present ? true : has_dst_addr;

	return true;
}

/* see section 7.2.2.6 */
static inline bool verify_and_get_has_src_pan_id(int dst_addr_mode, int src_addr_mode,
						 bool pan_id_comp, bool *has_src_pan_id)
{
	bool has_dst_addr = dst_addr_mode != IEEE802154_ADDR_MODE_NONE;
	bool has_src_addr = src_addr_mode != IEEE802154_ADDR_MODE_NONE;
	bool both_present = has_src_addr && has_dst_addr;

	if (!both_present && pan_id_comp != 0) {
		return false;
	}

	*has_src_pan_id = both_present ? !pan_id_comp : has_src_addr;

	return true;
}

static inline bool advance_cursor(int progress, uint8_t **cursor, uint8_t *remaining_length)
{
	if (progress < 0 || progress > *remaining_length) {
		NET_DBG("Error while parsing frame: %d", progress);
		return false;
	}

	*cursor += progress;
	*remaining_length -= progress;
	return true;
}

static inline int parse_fcf_seq(uint8_t *start, struct ieee802154_mhr *mhr)
{
	struct ieee802154_fcf *fcf = (struct ieee802154_fcf *)start;
	bool has_dst_pan_id = false, has_src_pan_id = false;
	uint8_t *cursor = start;

	dbg_print_fcf(fcf);

	/* Check basic value ranges and reject unsupported frame types
	 * see section 6.7.2 a) and b).
	 */
	if (fcf->frame_type >= IEEE802154_FRAME_TYPE_RESERVED ||
	    fcf->frame_version == IEEE802154_VERSION_RESERVED ||
	    fcf->dst_addr_mode == IEEE802154_ADDR_MODE_RESERVED ||
	    fcf->src_addr_mode == IEEE802154_ADDR_MODE_RESERVED) {
		return -EINVAL;
	}

	if (fcf->frame_type == IEEE802154_FRAME_TYPE_DATA &&
	    fcf->frame_version != IEEE802154_VERSION_802154 &&
	    fcf->dst_addr_mode == IEEE802154_ADDR_MODE_NONE &&
	    fcf->src_addr_mode == IEEE802154_ADDR_MODE_NONE) {
		/* see sections 7.2.2.9 and 7.2.2.11 */
		return -EINVAL;
	} else if (fcf->frame_type == IEEE802154_FRAME_TYPE_BEACON &&
		   fcf->frame_version != IEEE802154_VERSION_802154 &&
		   (fcf->dst_addr_mode != IEEE802154_ADDR_MODE_NONE ||
		    fcf->src_addr_mode == IEEE802154_ADDR_MODE_NONE || fcf->pan_id_comp)) {
		/* see sections 7.2.2.9, 7.2.2.11 and 7.3.1.2 */
		return -EINVAL;
	} else if (fcf->frame_type == IEEE802154_FRAME_TYPE_MAC_COMMAND && fcf->frame_pending) {
		/* see section 7.2.2.4, we repair the bit if set as the
		 * spec says that it should be ignored on reception if wrong
		 * so we should not reject the frame but we also don't want
		 * application logic having to deal with validation issues.
		 * This works as we're pointing directly into the data
		 * buffer.
		 */
		fcf->frame_pending = 0;
	}

#ifndef CONFIG_NET_L2_IEEE802154_SECURITY
	if (fcf->security_enabled) {
		return -EINVAL;
	}
#endif

	/* Verify PAN ID compression bit, see section 7.2.2.6 */
	if (!verify_and_get_has_dst_pan_id(fcf->dst_addr_mode, fcf->src_addr_mode, fcf->pan_id_comp,
					   &has_dst_pan_id)) {
		return -EINVAL;
	}
	if (!verify_and_get_has_src_pan_id(fcf->dst_addr_mode, fcf->src_addr_mode, fcf->pan_id_comp,
					   &has_src_pan_id)) {
		return -EINVAL;
	}

	/* Verify sequence number suppression and IE present fields,
	 * see sections 7.2.2.7 and 7.2.2.8.
	 */
	if ((fcf->seq_num_suppr || fcf->ie_present) &&
	    fcf->frame_version != IEEE802154_VERSION_802154) {
		return -EINVAL;
	}

	cursor += IEEE802154_FCF_LENGTH;

	mhr->frame_control = (struct ieee802154_frame_control){
		.frame_type = fcf->frame_type,
		.frame_version = fcf->frame_version,
		.has_dst_pan = has_dst_pan_id,
		.dst_addr_mode = fcf->dst_addr_mode,
		.has_src_pan = has_src_pan_id,
		.src_addr_mode = fcf->src_addr_mode,
		.security_enabled = fcf->security_enabled,
		.frame_pending = fcf->frame_pending,
		.ack_requested = fcf->ar,
		.has_seq_number = !fcf->seq_num_suppr,
		.ie_present = fcf->ie_present,
	};

	if (mhr->frame_control.has_seq_number) {
		mhr->sequence = *cursor;
		cursor += IEEE802154_SEQ_LENGTH;
	}

	return cursor - start;
}

static inline int parse_addr(uint8_t *start, uint8_t remaining_length,
			     enum ieee802154_addressing_mode mode, bool has_pan_id,
			     struct ieee802154_address_field **addr)
{
	int len = 0;
	*addr = NULL;

	NET_DBG("Buf %p - mode %d - pan id comp %d", (void *)start, mode, !has_pan_id);

	if (mode == IEEE802154_ADDR_MODE_NONE) {
		return 0;
	}

	if (has_pan_id) {
		len = IEEE802154_PAN_ID_LENGTH;
	}

	len += (mode == IEEE802154_ADDR_MODE_SHORT) ? IEEE802154_SHORT_ADDR_LENGTH
						    : IEEE802154_EXT_ADDR_LENGTH;
	if (len > remaining_length) {
		return -EFAULT;
	}

	*addr = (struct ieee802154_address_field *)start;

	return len;
}

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
static int ieee802154_parse_aux_security_hdr(uint8_t *start, uint8_t remaining_length,
					     struct ieee802154_aux_security_hdr **aux_hdr)
{
	struct ieee802154_aux_security_hdr *ash = (struct ieee802154_aux_security_hdr *)start;
	int len;

	len = IEEE802154_SECURITY_CF_LENGTH + IEEE802154_SECURITY_FRAME_COUNTER_LENGTH;

	/* At least the asf is sized of: control field + (optionally) frame counter */
	if (len > remaining_length) {
		return -EFAULT;
	}

	/* Only implicit key mode is supported for now */
	if (ash->control.key_id_mode != IEEE802154_KEY_ID_MODE_IMPLICIT) {
		return -EPROTONOSUPPORT;
	}

	/* Explicit key must have a key index != 0x00, see section 9.4.2.3 */
	switch (ash->control.key_id_mode) {
	case IEEE802154_KEY_ID_MODE_IMPLICIT:
		break;
	case IEEE802154_KEY_ID_MODE_INDEX:
		len += IEEE802154_KEY_ID_FIELD_INDEX_LENGTH;

		if (!ash->kif.mode_1.key_index) {
			return -EINVAL;
		}

		break;
	case IEEE802154_KEY_ID_MODE_SRC_4_INDEX:
		len += IEEE802154_KEY_ID_FIELD_SRC_4_INDEX_LENGTH;

		if (!ash->kif.mode_2.key_index) {
			return -EINVAL;
		}

		break;
	case IEEE802154_KEY_ID_MODE_SRC_8_INDEX:
		len += IEEE802154_KEY_ID_FIELD_SRC_8_INDEX_LENGTH;

		if (!ash->kif.mode_3.key_index) {
			return -EINVAL;
		}

		break;
	}

	if (len > remaining_length) {
		return -EFAULT;
	}

	*aux_hdr = (struct ieee802154_aux_security_hdr *)start;

	return len;
}
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */

static inline int parse_beacon(uint8_t *start, uint8_t remaining_length,
			       struct ieee802154_mpdu *mpdu)
{
	struct ieee802154_beacon *beacon = (struct ieee802154_beacon *)start;
	struct ieee802154_pas_spec *pas;
	uint8_t progress;

	__ASSERT_NO_MSG(mpdu->mhr.frame_control.frame_version < IEEE802154_VERSION_802154);

	progress = IEEE802154_BEACON_SF_SIZE + IEEE802154_BEACON_GTS_SPEC_SIZE;
	if (remaining_length < progress) {
		return -EFAULT;
	}

	if (beacon->gts.desc_count) {
		progress += IEEE802154_BEACON_GTS_DIR_SIZE +
			    beacon->gts.desc_count * IEEE802154_BEACON_GTS_SIZE;
	}

	if (remaining_length < progress) {
		return -EFAULT;
	}

	pas = (struct ieee802154_pas_spec *)start + progress;

	progress += IEEE802154_BEACON_PAS_SPEC_SIZE;
	if (remaining_length < progress) {
		return -EFAULT;
	}

	if (pas->nb_sap || pas->nb_eap) {
		progress += (pas->nb_sap * IEEE802154_SHORT_ADDR_LENGTH) +
			    (pas->nb_eap * IEEE802154_EXT_ADDR_LENGTH);
	}

	if (remaining_length < progress) {
		return -EFAULT;
	}

	mpdu->beacon = beacon;

	return progress;
}

static inline bool verify_mac_command_cfi_mhr(struct ieee802154_mhr *mhr, bool ack_requested,
					      bool has_src_pan, bool has_dst_pan, uint8_t src_bf,
					      bool src_pan_brdcst_chk, uint8_t dst_bf,
					      bool dst_brdcst_chk)
{
	if (mhr->frame_control.ack_requested != ack_requested ||
	    mhr->frame_control.has_src_pan != has_src_pan ||
	    mhr->frame_control.has_dst_pan != has_dst_pan ||
	    !(BIT(mhr->frame_control.src_addr_mode) & src_bf) ||
	    !(BIT(mhr->frame_control.dst_addr_mode) & dst_bf)) {
		return false;
	}

	/* broadcast address is symmetric so no need to swap byte order */
	if (src_pan_brdcst_chk) {
		if (!has_src_pan || mhr->src_addr->plain.pan_id != IEEE802154_BROADCAST_PAN_ID) {
			return false;
		}
	}

	if (dst_brdcst_chk) {
		if (mhr->dst_addr->plain.addr.short_addr != IEEE802154_BROADCAST_ADDRESS) {
			return false;
		}
	}

	return true;
}

static inline int parse_mac_command(uint8_t *start, uint8_t remaining_length,
				    struct ieee802154_mpdu *mpdu)
{
	struct ieee802154_command *command = (struct ieee802154_command *)start;
	uint8_t progress = IEEE802154_CMD_CFI_LENGTH;
	bool src_pan_brdcst_chk = false;
	uint8_t src_bf = 0, dst_bf = 0;
	bool dst_brdcst_chk = false;
	bool ack_requested = false;
	bool has_src_pan = true;
	bool has_dst_pan = true;

	if (remaining_length < progress) {
		return -EFAULT;
	}

	switch (command->cfi) {
	case IEEE802154_CFI_UNKNOWN:
		return -EOPNOTSUPP;

	case IEEE802154_CFI_ASSOCIATION_REQUEST:
		progress += IEEE802154_CMD_ASSOC_REQ_LENGTH;
		ack_requested = true;
		src_bf = BIT(IEEE802154_ADDR_MODE_EXTENDED);
		src_pan_brdcst_chk = true;
		dst_bf = BIT(IEEE802154_ADDR_MODE_SHORT) | BIT(IEEE802154_ADDR_MODE_EXTENDED);
		break;

	case IEEE802154_CFI_ASSOCIATION_RESPONSE:
		progress += IEEE802154_CMD_ASSOC_RES_LENGTH;
		__fallthrough;
	case IEEE802154_CFI_DISASSOCIATION_NOTIFICATION:
		if (command->cfi == IEEE802154_CFI_DISASSOCIATION_NOTIFICATION) {
			progress += IEEE802154_CMD_DISASSOC_NOTE_LENGTH;
			dst_bf = BIT(IEEE802154_ADDR_MODE_SHORT);
		}
		__fallthrough;
	case IEEE802154_CFI_PAN_ID_CONFLICT_NOTIFICATION:
		ack_requested = true;
		has_src_pan = false;
		src_bf = BIT(IEEE802154_ADDR_MODE_EXTENDED);
		dst_bf |= BIT(IEEE802154_ADDR_MODE_EXTENDED);
		break;

	case IEEE802154_CFI_DATA_REQUEST:
		ack_requested = true;
		src_bf = BIT(IEEE802154_ADDR_MODE_SHORT) | BIT(IEEE802154_ADDR_MODE_EXTENDED);

		if (mpdu->mhr.frame_control.dst_addr_mode == IEEE802154_ADDR_MODE_NONE) {
			has_dst_pan = false;
			dst_bf = BIT(IEEE802154_ADDR_MODE_NONE);
		} else {
			has_src_pan = false;
			dst_bf = BIT(IEEE802154_ADDR_MODE_SHORT) |
				 BIT(IEEE802154_ADDR_MODE_EXTENDED);
		}

		break;

	case IEEE802154_CFI_ORPHAN_NOTIFICATION:
		has_src_pan = false;
		src_bf = BIT(IEEE802154_ADDR_MODE_EXTENDED);
		dst_bf = BIT(IEEE802154_ADDR_MODE_SHORT);
		break;

	case IEEE802154_CFI_BEACON_REQUEST:
		has_src_pan = false;
		src_bf = BIT(IEEE802154_ADDR_MODE_NONE);
		dst_bf = BIT(IEEE802154_ADDR_MODE_SHORT);
		dst_brdcst_chk = true;
		break;

	case IEEE802154_CFI_COORDINATOR_REALIGNEMENT:
		progress += IEEE802154_CMD_COORD_REALIGN_LENGTH;
		src_bf = BIT(IEEE802154_ADDR_MODE_EXTENDED);

		if (mpdu->mhr.frame_control.dst_addr_mode == IEEE802154_ADDR_MODE_SHORT) {
			dst_bf = BIT(IEEE802154_ADDR_MODE_SHORT);
			dst_brdcst_chk = true;
		} else {
			dst_bf = BIT(IEEE802154_ADDR_MODE_EXTENDED);
		}

		break;

	case IEEE802154_CFI_GTS_REQUEST:
		progress += IEEE802154_GTS_REQUEST_LENGTH;
		ack_requested = true;
		src_bf = BIT(IEEE802154_ADDR_MODE_SHORT);
		dst_bf = BIT(IEEE802154_ADDR_MODE_NONE);
		break;

	default:
		return -EOPNOTSUPP;
	}

	if (remaining_length < progress) {
		return -EFAULT;
	}

	if (!verify_mac_command_cfi_mhr(&mpdu->mhr, ack_requested, has_src_pan, has_dst_pan, src_bf,
					src_pan_brdcst_chk, dst_bf, dst_brdcst_chk)) {
		return -EFAULT;
	}

	mpdu->command = command;

	return progress;
}

bool ieee802154_parse_mac_payload(struct ieee802154_mpdu *mpdu)
{
	int frame_version = mpdu->mhr.frame_control.frame_version;
	int frame_type = mpdu->mhr.frame_control.frame_type;
	uint8_t remaining_length = mpdu->mac_payload_length;
	uint8_t *cursor = mpdu->mac_payload;
	int progress = 0;

	if (frame_type == IEEE802154_FRAME_TYPE_MAC_COMMAND) {
		progress = parse_mac_command(cursor, remaining_length, mpdu);
		if (!advance_cursor(progress, &cursor, &remaining_length)) {
			return false;
		}

		if (frame_version < IEEE802154_VERSION_802154 && remaining_length) {
			return false;
		}
	} else if (frame_type == IEEE802154_FRAME_TYPE_DATA ||
		   frame_version == IEEE802154_VERSION_802154) {
		/* A data frame always embeds a payload, other generic
		 * enhanced frames may or may not embed a payload.
		 */
		if (frame_type == IEEE802154_FRAME_TYPE_DATA && !remaining_length) {
			return false;
		}
	} else if (frame_type == IEEE802154_FRAME_TYPE_BEACON) {
		progress = parse_beacon(cursor, remaining_length, mpdu);
		if (!advance_cursor(progress, &cursor, &remaining_length)) {
			return false;
		}
	} else if (frame_type == IEEE802154_FRAME_TYPE_ACK) {
		/** An Imm-ACK frame has no payload */
		if (remaining_length) {
			return false;
		}
	} else {
		return false;
	}

	mpdu->frame_payload_length = remaining_length;

	if (remaining_length) {
		mpdu->frame_payload = cursor;
	} else {
		mpdu->frame_payload = NULL;
	}

	return true;
}

bool ieee802154_parse_mhr(struct net_pkt *pkt, struct ieee802154_mpdu *mpdu)
{
	struct ieee802154_frame_control *frame_control;
	struct ieee802154_mhr *mhr = &mpdu->mhr;
	uint8_t remaining_length;
	uint8_t *cursor;
	uint8_t *start;
	int progress;

	remaining_length = net_pkt_get_len(pkt);
	if (remaining_length > IEEE802154_MTU ||
	    remaining_length < IEEE802154_MIN_LENGTH) {
		NET_DBG("Wrong packet length: %u", remaining_length);
		return false;
	}

	start = net_pkt_data(pkt);
	cursor = start;

	progress = parse_fcf_seq(cursor, mhr);
	if (!advance_cursor(progress, &cursor, &remaining_length)) {
		return false;
	}

	frame_control = &mhr->frame_control;

	progress = parse_addr(cursor, remaining_length, frame_control->dst_addr_mode,
			      frame_control->has_dst_pan, &mhr->dst_addr);
	if (!advance_cursor(progress, &cursor, &remaining_length)) {
		return false;
	}

	progress = parse_addr(cursor, remaining_length, frame_control->src_addr_mode,
			      frame_control->has_src_pan, &mhr->src_addr);
	if (!advance_cursor(progress, &cursor, &remaining_length)) {
		return false;
	}

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
	if (frame_control->security_enabled) {
		progress =
			ieee802154_parse_aux_security_hdr(cursor, remaining_length, &mhr->aux_sec);
		if (!advance_cursor(progress, &cursor, &remaining_length)) {
			return false;
		}
	}
#endif

	if (mhr->frame_control.ie_present) {
		return false;
	}

	mpdu->mac_payload_length = remaining_length;
	mpdu->mac_payload = remaining_length > 0 ? cursor : NULL;

	NET_DBG("Header size: %u, MAC payload size (including payload IEs) %u",
		(size_t)(cursor - start), remaining_length);

	return true;
}

/* context must be locked */
static int ieee802154_get_src_addr_mode(struct net_linkaddr *src, struct ieee802154_context *ctx)
{
	uint8_t ext_addr_le[IEEE802154_EXT_ADDR_LENGTH];
	uint16_t short_addr;

	if (ctx->pan_id == IEEE802154_PAN_ID_NOT_ASSOCIATED ||
	    ctx->short_addr == IEEE802154_SHORT_ADDRESS_NOT_ASSOCIATED) {
		return -EPERM;
	}

	if (!src->addr) {
		return ctx->short_addr == IEEE802154_NO_SHORT_ADDRESS_ASSIGNED
			       ? IEEE802154_ADDR_MODE_EXTENDED
			       : IEEE802154_ADDR_MODE_SHORT;
	}

	/* Just ensure that any given source address
	 * corresponds to the interface's address
	 * which will be used in the frame.
	 */
	if (src->len == IEEE802154_SHORT_ADDR_LENGTH) {
		short_addr = ntohs(*(uint16_t *)(src->addr));

		if (ctx->short_addr != short_addr) {
			return -EINVAL;
		}

		return IEEE802154_ADDR_MODE_SHORT;
	}

	if (src->len != IEEE802154_EXT_ADDR_LENGTH) {
		return -EINVAL;
	}

	sys_memcpy_swap(ext_addr_le, src->addr, IEEE802154_EXT_ADDR_LENGTH);
	if (memcmp(ctx->ext_addr, ext_addr_le, src->len)) {
		return -EINVAL;
	}

	return IEEE802154_ADDR_MODE_EXTENDED;
}

/* context must be locked */
static uint8_t ieee802154_compute_header_size(struct ieee802154_context *ctx,
					      struct ieee802154_frame_params *params,
					      bool is_encrypted)
{
	bool has_dst_pan_id = false, has_src_pan_id = false;
	int dst_addr_mode, src_addr_mode;
	uint8_t ll_hdr_len = 0;
	bool pan_id_comp;

	ll_hdr_len += IEEE802154_FCF_LENGTH + IEEE802154_SEQ_LENGTH;

	__ASSERT_NO_MSG(params->len != 0);
	dst_addr_mode = params->dst.len == IEEE802154_SHORT_ADDR_LENGTH
				? IEEE802154_ADDR_MODE_SHORT
				: IEEE802154_ADDR_MODE_EXTENDED;

	__ASSERT_NO_MSG(params->dst.len != 0);
	src_addr_mode = params->len == IEEE802154_SHORT_ADDR_LENGTH ? IEEE802154_ADDR_MODE_SHORT
								    : IEEE802154_ADDR_MODE_EXTENDED;

	if (!get_pan_id_comp(dst_addr_mode, src_addr_mode, params->dst.pan_id, params->pan_id,
			     &pan_id_comp)) {
		return -EINVAL;
	}

	if (!verify_and_get_has_dst_pan_id(dst_addr_mode, src_addr_mode, pan_id_comp,
					   &has_dst_pan_id)) {
		return -EINVAL;
	}

	if (!verify_and_get_has_src_pan_id(dst_addr_mode, src_addr_mode, pan_id_comp,
					   &has_src_pan_id)) {
		return -EINVAL;
	}

	ll_hdr_len += (has_dst_pan_id + has_src_pan_id) * IEEE802154_PAN_ID_LENGTH +
		      params->dst.len + params->len;

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
	struct ieee802154_security_ctx *sec_ctx = &ctx->sec_ctx;

	if (!is_encrypted) {
		goto done;
	}

	__ASSERT_NO_MSG(sec_ctx->level != IEEE802154_SECURITY_LEVEL_NONE);

	/* Compute aux-sec hdr size and add it to ll_hdr_len */
	ll_hdr_len += IEEE802154_SECURITY_CF_LENGTH + IEEE802154_SECURITY_FRAME_COUNTER_LENGTH;

	switch (sec_ctx->key_mode) {
	case IEEE802154_KEY_ID_MODE_IMPLICIT:
		/* The only mode supported for now,
		 * generate_aux_securiy_hdr() will fail on other modes
		 */
		break;
	case IEEE802154_KEY_ID_MODE_INDEX:
		ll_hdr_len += IEEE802154_KEY_ID_FIELD_INDEX_LENGTH;
		break;
	case IEEE802154_KEY_ID_MODE_SRC_4_INDEX:
		ll_hdr_len += IEEE802154_KEY_ID_FIELD_SRC_4_INDEX_LENGTH;
		break;
	case IEEE802154_KEY_ID_MODE_SRC_8_INDEX:
		ll_hdr_len += IEEE802154_KEY_ID_FIELD_SRC_8_INDEX_LENGTH;
	}

done:
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */

	NET_DBG("Computed header size: %u", ll_hdr_len);

	return ll_hdr_len;
}

/* context must be locked */
static uint8_t ieee802154_compute_authtag_len(struct ieee802154_context *ctx, bool is_encrypted)
{
	uint8_t authtag_len = 0;

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
	struct ieee802154_security_ctx *sec_ctx = &ctx->sec_ctx;

	if (!is_encrypted) {
		goto done;
	}

	__ASSERT_NO_MSG(sec_ctx->level != IEEE802154_SECURITY_LEVEL_NONE);

	if (sec_ctx->level < IEEE802154_SECURITY_LEVEL_ENC) {
		authtag_len += level_2_authtag_len[sec_ctx->level];
	} else {
		authtag_len += level_2_authtag_len[sec_ctx->level - 4U];
	}

done:
	NET_DBG("Computed authtag length: %u", authtag_len);
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */

	return authtag_len;
}

int ieee802154_get_data_frame_params(struct ieee802154_context *ctx, struct net_linkaddr *dst,
				     struct net_linkaddr *src,
				     struct ieee802154_frame_params *params, uint8_t *ll_hdr_len,
				     uint8_t *authtag_len)
{
	bool is_encrypted = false;
	int ll_hdr_len_or_error;
	int src_addr_mode;
	int res = 0;

	k_sem_take(&ctx->ctx_lock, K_FOREVER);

	src_addr_mode = ieee802154_get_src_addr_mode(src, ctx);
	if (src_addr_mode < 0) {
		NET_ERR("Cannot determine source address");
		res = src_addr_mode;
		goto release_ctx;
	}

	params->len = src_addr_mode == IEEE802154_ADDR_MODE_SHORT ? IEEE802154_SHORT_ADDR_LENGTH
								  : IEEE802154_EXT_ADDR_LENGTH;

	params->pan_id = ctx->pan_id;
	params->dst.pan_id = ctx->pan_id;

	if (!dst->addr) {
		NET_DBG("No destination address - assuming broadcast.");
		params->dst.len = IEEE802154_SHORT_ADDR_LENGTH;
		params->dst.short_addr = IEEE802154_BROADCAST_ADDRESS;
	} else {
		if (dst->len == IEEE802154_SHORT_ADDR_LENGTH) {
			params->dst.len = IEEE802154_SHORT_ADDR_LENGTH;
			params->dst.short_addr = ntohs(*(uint16_t *)(dst->addr));
		} else if (dst->len == IEEE802154_EXT_ADDR_LENGTH) {
			params->dst.len = IEEE802154_EXT_ADDR_LENGTH;
			memcpy(params->dst.ext_addr, dst->addr, sizeof(params->dst.ext_addr));
		} else {
			res = -EINVAL;
			goto release_ctx;
		}
	}

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
	is_encrypted = ctx->sec_ctx.level != IEEE802154_SECURITY_LEVEL_NONE;
#endif

	ll_hdr_len_or_error = ieee802154_compute_header_size(ctx, params, is_encrypted);
	if (ll_hdr_len_or_error < 0) {
		res = ll_hdr_len_or_error;
		goto release_ctx;
	}

	*ll_hdr_len = ll_hdr_len_or_error;
	*authtag_len = ieee802154_compute_authtag_len(ctx, is_encrypted);

release_ctx:
	k_sem_give(&ctx->ctx_lock);
	return res;
}

/* context must be locked, requires addressing mode to already have been written */
static inline int write_fcf_and_seq(uint8_t *start, int frame_type, uint8_t *seq,
				    struct ieee802154_frame_params *params)
{
	struct ieee802154_fcf *fcf = (struct ieee802154_fcf *)start;
	uint16_t dst_pan_id = params ? params->dst.pan_id : 0U;
	uint16_t src_pan_id = params ? params->pan_id : 0U;
	uint8_t *cursor = start;
	bool pan_id_comp;

	fcf->frame_type = frame_type;
	fcf->security_enabled = false;
	fcf->frame_pending = false;
	fcf->reserved = 0U;
	fcf->seq_num_suppr = 0U;
	fcf->ie_present = 0U;

	if (!get_pan_id_comp(fcf->dst_addr_mode, fcf->src_addr_mode, dst_pan_id, src_pan_id,
			     &pan_id_comp)) {
		return -EINVAL;
	}

	fcf->pan_id_comp = pan_id_comp;

	cursor += IEEE802154_FCF_LENGTH;

	*cursor = *seq;
	cursor += IEEE802154_SEQ_LENGTH;

	if (frame_type != IEEE802154_FRAME_TYPE_ACK) {
		(*seq)++;
	}

	return cursor - start;
}

/* context must be locked */
static inline void initialize_generic_frame_fcf(struct ieee802154_context *ctx, int frame_type,
						struct ieee802154_frame_params *params,
						struct ieee802154_fcf *fcf)
{
	bool is_broadcast;

	/* We support version 2006 only for now */
	fcf->frame_version = IEEE802154_VERSION_802154_2006;

	is_broadcast = params->dst.len == IEEE802154_SHORT_ADDR_LENGTH &&
		       params->dst.short_addr == IEEE802154_BROADCAST_ADDRESS;

	/* see section 6.7.4.1 */
	fcf->ar = !is_broadcast && ctx->ack_requested;

	if (params->dst.len == IEEE802154_SHORT_ADDR_LENGTH) {
		fcf->dst_addr_mode = IEEE802154_ADDR_MODE_SHORT;
	} else {
		__ASSERT_NO_MSG(params->dst.len == IEEE802154_EXT_ADDR_LENGTH);
		fcf->dst_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;
	}

	if (params->len == IEEE802154_SHORT_ADDR_LENGTH) {
		fcf->src_addr_mode = IEEE802154_ADDR_MODE_SHORT;
	} else {
		__ASSERT_NO_MSG(params->len == IEEE802154_EXT_ADDR_LENGTH);
		fcf->src_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;
	}
}

/* context must be locked */
static int write_addressing_fields(uint8_t *start, struct ieee802154_frame_params *params,
				   struct ieee802154_context *ctx, struct ieee802154_fcf *fcf,
				   struct ieee802154_address **p_src_addr)
{
	struct ieee802154_address_field *address_field;
	struct ieee802154_address *addr;
	uint8_t *cursor = start;
	bool has_pan_id;

	/* destination address */
	if (fcf->dst_addr_mode != IEEE802154_ADDR_MODE_NONE) {
		address_field = (struct ieee802154_address_field *)cursor;

		if (!verify_and_get_has_dst_pan_id(fcf->dst_addr_mode, fcf->src_addr_mode,
						   fcf->pan_id_comp, &has_pan_id)) {
			return -EINVAL;
		}

		if (has_pan_id) {
			address_field->plain.pan_id = sys_cpu_to_le16(params->dst.pan_id);
			addr = &address_field->plain.addr;
			cursor += IEEE802154_PAN_ID_LENGTH;
		} else {
			addr = &address_field->comp.addr;
		}

		if (fcf->dst_addr_mode == IEEE802154_ADDR_MODE_SHORT) {
			__ASSERT_NO_MSG(params->dst.len == IEEE802154_SHORT_ADDR_LENGTH);
			addr->short_addr = sys_cpu_to_le16(params->dst.short_addr);
			cursor += IEEE802154_SHORT_ADDR_LENGTH;
		} else {
			__ASSERT_NO_MSG(params->dst.len == IEEE802154_EXT_ADDR_LENGTH);
			sys_memcpy_swap(addr->ext_addr, params->dst.ext_addr,
					IEEE802154_EXT_ADDR_LENGTH);
			cursor += IEEE802154_EXT_ADDR_LENGTH;
		}
	}

	/* source address */
	if (fcf->src_addr_mode == IEEE802154_ADDR_MODE_NONE) {
		goto out;
	}

	address_field = (struct ieee802154_address_field *)cursor;

	if (!verify_and_get_has_src_pan_id(fcf->dst_addr_mode, fcf->src_addr_mode, fcf->pan_id_comp,
					   &has_pan_id)) {
		return -EINVAL;
	}

	if (has_pan_id) {
		address_field->plain.pan_id = sys_cpu_to_le16(params->pan_id);
		addr = &address_field->plain.addr;
		cursor += IEEE802154_PAN_ID_LENGTH;
	} else {
		addr = &address_field->comp.addr;
	}

	if (p_src_addr) {
		*p_src_addr = addr;
	}

	if (fcf->src_addr_mode == IEEE802154_ADDR_MODE_SHORT) {
		__ASSERT_NO_MSG(params->len == IEEE802154_SHORT_ADDR_LENGTH);
		addr->short_addr = sys_cpu_to_le16(ctx->short_addr);
		cursor += IEEE802154_SHORT_ADDR_LENGTH;
	} else {
		__ASSERT_NO_MSG(params->len == IEEE802154_EXT_ADDR_LENGTH);
		memcpy(addr->ext_addr, ctx->ext_addr, IEEE802154_EXT_ADDR_LENGTH);
		cursor += IEEE802154_EXT_ADDR_LENGTH;
	}

out:
	return cursor - start;
}

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
/* context must be locked */
static int write_aux_security_hdr(uint8_t *start, struct ieee802154_security_ctx *sec_ctx)
{
	struct ieee802154_aux_security_hdr *aux_sec;
	int progress = 0;

	__ASSERT_NO_MSG(sec_ctx->level != IEEE802154_SECURITY_LEVEL_NONE &&
			sec_ctx->level != IEEE802154_SECURITY_LEVEL_RESERVED);
	__ASSERT_NO_MSG(sec_ctx->frame_counter != 0xffffffff);

	if (sec_ctx->key_mode != IEEE802154_KEY_ID_MODE_IMPLICIT) {
		/* TODO: Support other key ID modes. */
		return -ENOTSUP;
	}

	aux_sec = (struct ieee802154_aux_security_hdr *)start;

	aux_sec->control.security_level = sec_ctx->level;
	aux_sec->control.key_id_mode = sec_ctx->key_mode;
	aux_sec->control.reserved = 0U;
	progress += IEEE802154_SECURITY_CF_LENGTH;

	aux_sec->frame_counter = sys_cpu_to_le32(sec_ctx->frame_counter);
	progress += IEEE802154_SECURITY_FRAME_COUNTER_LENGTH;

	return progress;
}

/* context must be locked */
static int outgoing_security_procedure(uint8_t *cursor, struct ieee802154_security_ctx *sec_ctx,
				       int frame_type, uint8_t *frame, struct ieee802154_fcf *fcf,
				       uint8_t payload_len, uint8_t authtag_len, uint16_t pan_id,
				       struct ieee802154_address *src_addr, uint32_t frame_counter)
{
	uint8_t ll_hdr_len = cursor - frame;
	uint8_t level = sec_ctx->level;
	int progress = 0;

	/* Section 9.2.2: Outgoing frame security procedure
	 *
	 * a) Is security needed? If the SecurityLevel parameter is zero, the procedure shall set
	 *    the secured frame to be the frame to be secured and return with a Status of SUCCESS.
	 */
	if (!authtag_len) {
		return progress;
	}

	/* b) Is security enabled? If macSecurityEnabled is set to FALSE, the procedure shall return
	 * with a Status of UNSUPPORTED_SECURITY.
	 *
	 * TODO: c) - implement. Currently we have a single frame counter and a single key. The
	 * security feature MUST NOT be marked STABLE unless step c) is properly implemented.
	 */
	if (level == IEEE802154_SECURITY_LEVEL_NONE) {
		NET_DBG("Outgoing security procedure failed: Unsupported security.");
		return -EPERM;
	}

	if (level == IEEE802154_SECURITY_LEVEL_RESERVED) {
		NET_DBG("Encryption-only security is deprecated since IEEE 802.15.4-2015.");
		return -ENOTSUP;
	}

	fcf->security_enabled = 1U;

	/* d) Check frame counter value.
	 *    1) TODO: - implement. Currently we do not have key specific frame counters.
	 *    2) If the secKeyFrameCounter [...] is set to 0xffffffff, the procedure shall
	 *       return with a Status of COUNTER_ERROR.
	 */
	if (frame_counter == 0xffffffff) {
		NET_DBG("Outgoing security procedure failed: Counter error.");
		return -EINVAL;
	}

	/* e) Insert Auxiliary Security Header field. */
	progress = write_aux_security_hdr(cursor, sec_ctx);
	if (progress < 0) {
		NET_DBG("Unsupported key mode.");
		return progress;
	}
	ll_hdr_len += progress;

	/* f) Secure the frame.
	 *
	 * TODO: Support distinction between private and open payload field.
	 */
	if (!ieee802154_encrypt_auth(sec_ctx, frame_type, frame, ll_hdr_len, payload_len,
				     authtag_len, pan_id, src_addr, fcf->src_addr_mode,
				     frame_counter)) {
		NET_DBG("Outgoing security procedure failed: Security error.");
		return -EFAULT;
	}

	/* g) Store frame counter */
	sec_ctx->frame_counter++;

	return progress;
}
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */

bool ieee802154_write_mhr_and_security(struct ieee802154_context *ctx, int frame_type,
				       struct ieee802154_frame_params *params, struct net_buf *buf,
				       uint8_t ll_hdr_len, uint8_t authtag_len)
{
	struct ieee802154_address *src_addr;
	struct ieee802154_fcf *fcf;
	uint8_t remaining_length;
	uint8_t payload_len;
	bool ret = false;
	uint8_t *cursor;
	uint8_t *start;
	uint8_t *seq;
	int progress;

	start = buf->data;
	cursor = start;

	__ASSERT_NO_MSG(buf->len <= IEEE802154_MTU);
	remaining_length = buf->len;

	__ASSERT_NO_MSG(buf->len >= ll_hdr_len + authtag_len);
	payload_len = remaining_length - ll_hdr_len - authtag_len;

	k_sem_take(&ctx->ctx_lock, K_FOREVER);

	seq = &ctx->sequence;

	fcf = (struct ieee802154_fcf *)cursor;
	initialize_generic_frame_fcf(ctx, frame_type, params, fcf);

	progress = write_fcf_and_seq(cursor, frame_type, seq, params);
	if (!advance_cursor(progress, &cursor, &remaining_length)) {
		goto release_ctx;
	}

	progress = write_addressing_fields(cursor, params, ctx, fcf, &src_addr);
	if (!advance_cursor(progress, &cursor, &remaining_length)) {
		goto release_ctx;
	}

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
	uint32_t frame_counter = ctx->sec_ctx.frame_counter;
	progress = outgoing_security_procedure(cursor, &ctx->sec_ctx, frame_type, start, fcf,
					       payload_len, authtag_len, ctx->pan_id, src_addr,
					       frame_counter);
	if (!advance_cursor(progress, &cursor, &remaining_length)) {
		goto release_ctx;
	}
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */

	if ((cursor - start) != ll_hdr_len) {
		/* ll_hdr_len was too small? We probably overwrote payload bytes. */
		NET_ERR("Could not generate data frame header, header length mismatch %zu vs %u",
			(cursor - start), ll_hdr_len);
		goto release_ctx;
	}

	progress = payload_len + authtag_len;
	if (!advance_cursor(progress, &cursor, &remaining_length) || remaining_length != 0) {
		NET_ERR("Could not finalize data frame payload, frame length mismatch %u",
			remaining_length);
		goto release_ctx;
	}

	dbg_print_fcf(fcf);

	ret = true;

release_ctx:
	k_sem_give(&ctx->ctx_lock);
	return ret;
}

#ifdef CONFIG_NET_L2_IEEE802154_MGMT
/* context must be locked */
static inline bool initialize_cmd_frame_fcf(struct ieee802154_context *ctx, enum ieee802154_cfi cfi,
					    struct ieee802154_frame_params *params,
					    struct ieee802154_fcf *fcf)
{
	*fcf = (struct ieee802154_fcf){0};
	fcf->frame_version = IEEE802154_VERSION_802154_2006;

	switch (cfi) {
	case IEEE802154_CFI_DISASSOCIATION_NOTIFICATION:
		/* See section 7.5.4:
		 *
		 * The Frame Pending field shall be set to zero and ignored upon
		 * reception, and the AR field shall be set to one.
		 */
		fcf->ar = 1U;

		/* The Source Addressing Mode field shall be set to indicate
		 * extended addressing.
		 */
		fcf->src_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;

		/* The Source Address field shall contain the value of macExtendedAddress. */
		params->len = IEEE802154_EXT_ADDR_LENGTH;

		/* The Source PAN ID field shall be omitted. */
		__ASSERT_NO_MSG(params->pan_id == 0);
		fcf->pan_id_comp = 1U;

		/* The Destination PAN ID field shall contain the value of macPanId. */
		params->dst.pan_id = ctx->pan_id;

		if (ctx->device_role == IEEE802154_DEVICE_ROLE_ENDDEVICE) {
			/* If an associated device is disassociating from the
			 * PAN, then the Destination Address field shall contain
			 * the value of either macCoordShortAddress, if the
			 * Destination Addressing Mode field is set to indicated
			 * short addressing, or macCoordExtendedAddress, if the
			 * Destination Addressing Mode field is set to indicated
			 * extended addressing.
			 */
			if (ctx->coord_short_addr != IEEE802154_SHORT_ADDRESS_NOT_ASSOCIATED &&
			    ctx->coord_short_addr != IEEE802154_NO_SHORT_ADDRESS_ASSIGNED) {
				fcf->dst_addr_mode = IEEE802154_ADDR_MODE_SHORT;
				params->dst.len = IEEE802154_SHORT_ADDR_LENGTH;
				params->dst.short_addr = ctx->coord_short_addr;
			} else {
				fcf->dst_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;
				params->dst.len = IEEE802154_EXT_ADDR_LENGTH;
				sys_memcpy_swap(params->dst.ext_addr, ctx->coord_ext_addr,
						sizeof(params->dst.ext_addr));
			}
		} else {
			/* If the coordinator is disassociating a device from the
			 * PAN, then the Destination Address field shall contain
			 * the address of the device being removed from the PAN.
			 */
			if (params->dst.len == IEEE802154_SHORT_ADDR_LENGTH) {
				fcf->dst_addr_mode = IEEE802154_ADDR_MODE_SHORT;
			} else {
				__ASSERT_NO_MSG(params->dst.len == IEEE802154_EXT_ADDR_LENGTH);
				fcf->dst_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;
			}
		}

		break;
	case IEEE802154_CFI_ASSOCIATION_REQUEST:
		/* The Frame Pending field shall be set to zero and ignored upon
		 * reception, and the AR field shall be set to one.
		 */
		fcf->ar = 1U;

		/* The Source Addressing Mode field shall be set to indicate
		 * extended addressing.
		 */
		fcf->src_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;

		/* The Source Address field shall contain the value of macExtendedAddress.*/
		params->len = IEEE802154_EXT_ADDR_LENGTH;

		/* The Destination Address field shall contain the address from
		 * the Beacon frame that was transmitted by the coordinator to
		 * which the Association Request command is being sent.
		 *
		 * The Destination Addressing Mode field shall be set to the same
		 * mode as indicated in the Beacon frame to which the Association
		 * Request command refers.
		 */
		if (params->dst.len == IEEE802154_SHORT_ADDR_LENGTH) {
			fcf->dst_addr_mode = IEEE802154_ADDR_MODE_SHORT;
		} else {
			__ASSERT_NO_MSG(params->dst.len == IEEE802154_EXT_ADDR_LENGTH);
			fcf->dst_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;
		}

		/* If the Version field is set to 0b10, the Source PAN ID field is
		 * omitted. Otherwise, the Source PAN ID field shall contain the
		 * broadcast PAN ID.
		 */
		params->pan_id = IEEE802154_BROADCAST_PAN_ID;

		/* The Destination PAN ID field shall contain the identifier of
		 * the PAN to which to associate.
		 */
		__ASSERT_NO_MSG(params->dst.pan_id != IEEE802154_PAN_ID_NOT_ASSOCIATED);

		break;
	case IEEE802154_CFI_ASSOCIATION_RESPONSE:
	case IEEE802154_CFI_PAN_ID_CONFLICT_NOTIFICATION:
		/* See sections 7.5.4 and 7.5.6:
		 *
		 * The Frame Pending field shall be set to zero and ignored upon
		 * reception, and the AR field shall be set to one.
		 */
		fcf->ar = 1U;

		/* The Destination Addressing Mode and Source Addressing Mode
		 * fields shall each be set to indicate extended addressing.
		 */
		fcf->src_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;
		fcf->dst_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;

		/* The Source Address field shall contain the value of macExtendedAddress. */
		params->len = IEEE802154_EXT_ADDR_LENGTH;

		/* The Destination PAN ID field shall contain the value of
		 * macPanId, while the Source PAN ID field shall be omitted.
		 */
		params->dst.pan_id = ctx->pan_id;
		fcf->pan_id_comp = 1U;

		/* The Destination Address field shall contain the extended
		 * address of the device requesting association (assoc response)
		 * or macCoordExtendedAddress (conflict notification)
		 * respectively.
		 */
		if (cfi == IEEE802154_CFI_ASSOCIATION_RESPONSE) {
			__ASSERT_NO_MSG(params->dst.len == IEEE802154_EXT_ADDR_LENGTH);
		} else {
			__ASSERT_NO_MSG(params->dst.len == 0);
			params->dst.len = IEEE802154_EXT_ADDR_LENGTH;
			sys_memcpy_swap(params->dst.ext_addr, ctx->coord_ext_addr,
					sizeof(params->dst.ext_addr));
		}

		break;
	case IEEE802154_CFI_DATA_REQUEST:
		fcf->ar = 1U;
		/* TODO: src/dst addr mode and params: see section 7.5.5 */

		break;
	case IEEE802154_CFI_ORPHAN_NOTIFICATION:
		fcf->pan_id_comp = 1U;
		fcf->src_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;
		fcf->dst_addr_mode = IEEE802154_ADDR_MODE_SHORT;
		/* TODO: params */

		break;
	case IEEE802154_CFI_BEACON_REQUEST:
		fcf->src_addr_mode = IEEE802154_ADDR_MODE_NONE;
		fcf->dst_addr_mode = IEEE802154_ADDR_MODE_SHORT;

		__ASSERT_NO_MSG(params->dst.len == 0);
		params->dst.len = IEEE802154_SHORT_ADDR_LENGTH;
		params->dst.short_addr = IEEE802154_BROADCAST_ADDRESS;

		__ASSERT_NO_MSG(params->dst.pan_id == 0);
		params->dst.pan_id = IEEE802154_BROADCAST_PAN_ID;

		break;
	case IEEE802154_CFI_COORDINATOR_REALIGNEMENT:
		fcf->src_addr_mode = IEEE802154_ADDR_MODE_EXTENDED;
		/* TODO: ack_requested, dst addr mode and params: see section 7.5.10 */

		break;
	case IEEE802154_CFI_GTS_REQUEST:
		fcf->ar = 1U;
		fcf->src_addr_mode = IEEE802154_ADDR_MODE_SHORT;
		fcf->dst_addr_mode = IEEE802154_ADDR_MODE_NONE;
		/* TODO: params */

		break;
	default:
		return false;
	}

	if (fcf->pan_id_comp) {
		params->pan_id = params->dst.pan_id;
	}

	return true;
}

static inline uint8_t get_mac_command_length(enum ieee802154_cfi cfi)
{
	uint8_t length = 1U; /* cfi is at least present */

	switch (cfi) {
	case IEEE802154_CFI_ASSOCIATION_REQUEST:
	case IEEE802154_CFI_DISASSOCIATION_NOTIFICATION:
	case IEEE802154_CFI_GTS_REQUEST:
		length += 1U;
		break;
	case IEEE802154_CFI_ASSOCIATION_RESPONSE:
		length += 3U;
		break;
	case IEEE802154_CFI_COORDINATOR_REALIGNEMENT:
		length += 8U;
		break;
	default:
		break;
	}

	return length;
}

struct net_pkt *ieee802154_create_mac_cmd_frame(struct net_if *iface, enum ieee802154_cfi cfi,
						struct ieee802154_frame_params *params,
						struct ieee802154_command **p_cmd)
{
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	struct ieee802154_command *cmd;
	struct ieee802154_fcf *fcf;
	struct net_pkt *pkt = NULL;
	uint8_t remaining_length;
	uint8_t *cursor, *start;
	int progress;

	k_sem_take(&ctx->ctx_lock, K_FOREVER);

	/* It would be costly to compute the size when actual frames are never
	 * bigger than IEEE802154_MTU bytes less the FCS size, so let's allocate that
	 * size as buffer.
	 */
	pkt = net_pkt_alloc_with_buffer(iface, IEEE802154_MTU, AF_UNSPEC, 0, BUF_TIMEOUT);
	if (!pkt) {
		goto release_ctx;
	}

	start = net_pkt_data(pkt);
	cursor = start;
	remaining_length = net_buf_tailroom(pkt->buffer);

	/* see section 6.7.4.1 */
	fcf = (struct ieee802154_fcf *)cursor;
	if (!initialize_cmd_frame_fcf(ctx, cfi, params, fcf)) {
		goto release_pkt;
	}


	progress = write_fcf_and_seq(cursor, IEEE802154_FRAME_TYPE_MAC_COMMAND, &ctx->sequence,
				     params);
	if (!advance_cursor(progress, &cursor, &remaining_length)) {
		goto release_pkt;
	}

	progress = write_addressing_fields(cursor, params, ctx, fcf, NULL);
	if (!advance_cursor(progress, &cursor, &remaining_length)) {
		goto release_pkt;
	}

	cmd = ((struct ieee802154_command *)cursor);
	cmd->cfi = cfi;

	if (p_cmd) {
		*p_cmd = cmd;
	}

	progress = get_mac_command_length(cfi);
	if (!advance_cursor(progress, &cursor, &remaining_length)) {
		goto release_pkt;
	}

	net_buf_add(pkt->buffer, cursor - start);

	dbg_print_fcf(fcf);

	goto release_ctx;

release_pkt:
	net_pkt_unref(pkt);
	pkt = NULL;

release_ctx:
	k_sem_give(&ctx->ctx_lock);
	return pkt;
}
#endif /* CONFIG_NET_L2_IEEE802154_MGMT */

struct net_pkt *ieee802154_create_imm_ack_frame(struct net_if *iface, uint8_t seq)
{
	uint8_t remaining_length = IEEE802154_IMM_ACK_PKT_LENGTH;
	struct ieee802154_fcf *fcf;
	struct net_pkt *pkt;
	uint8_t *cursor;
	int progress;

	pkt = net_pkt_alloc_with_buffer(iface, IEEE802154_IMM_ACK_PKT_LENGTH, AF_UNSPEC, 0,
					BUF_TIMEOUT);
	if (!pkt) {
		return NULL;
	}

	cursor = net_pkt_data(pkt);
	if (!cursor) {
		goto release_pkt;
	}

	fcf = (struct ieee802154_fcf *)cursor;
	fcf->frame_version = IEEE802154_VERSION_802154_2006;
	fcf->ar = 0U;
	fcf->dst_addr_mode = IEEE802154_ADDR_MODE_NONE;
	fcf->src_addr_mode = IEEE802154_ADDR_MODE_NONE;

	progress = write_fcf_and_seq(cursor, IEEE802154_FRAME_TYPE_ACK, &seq, NULL);
	if (!advance_cursor(progress, &cursor, &remaining_length) || remaining_length != 0) {
		goto release_pkt;
	}

	net_buf_add(pkt->buffer, IEEE802154_IMM_ACK_PKT_LENGTH);

	return pkt;

release_pkt:
	net_pkt_unref(pkt);
	return NULL;
}

#ifdef CONFIG_NET_L2_IEEE802154_SECURITY
bool ieee802154_incoming_security_procedure(struct net_if *iface, struct net_pkt *pkt,
					    struct ieee802154_mpdu *mpdu)
{
	struct ieee802154_context *ctx = net_if_l2_data(iface);
	struct ieee802154_mhr *mhr = &mpdu->mhr;
	uint8_t ll_hdr_len, authtag_len, level;
	struct ieee802154_address *src_addr;
	uint32_t frame_counter;
	bool ret = false;

	if (!mhr->frame_control.security_enabled) {
		/* Section 9.2.5: Incoming frame security procedure, Security Enabled field is set
		 * to zero
		 *
		 * [...]
		 *
		 * a) Check for macSecurityEnabled. If macSecurityEnabled is set to FALSE, the
		 *    procedure shall [...] return with a Status of SUCCESS.
		 *
		 * TODO: b)-f) implement - currently we accept all frames that are not secured.
		 * The security feature MUST NOT be marked STABLE unless conditions b)-f) are
		 * properly implemented.
		 */
		ret = true;
		goto out;
	}

	/* Section 9.2.4: Incoming frame security procedure, Security Enabled field is set to one
	 *
	 * [...]
	 *
	 * a) Legacy security. If the Frame Version field of the frame to be unsecured is set to
	 *    zero, the procedure shall return with a Status of UNSUPPORTED_LEGACY.
	 */
	if (mhr->frame_control.frame_version == IEEE802154_VERSION_802154_2003) {
		NET_DBG("Incoming security procedure failed: Unsupported legacy.");
		goto out;
	}

	k_sem_take(&ctx->ctx_lock, K_FOREVER);

	level = ctx->sec_ctx.level;

	/* b) Check for macSecurityEnabled. If macSecurityEnabled is set to FALSE, the procedure
	 * shall return with a Status of UNSUPPORTED_SECURITY.
	 */
	if (level == IEEE802154_SECURITY_LEVEL_NONE) {
		NET_DBG("Incoming security procedure failed: Unsupported security.");
		goto release_ctx;
	}

	if (level == IEEE802154_SECURITY_LEVEL_RESERVED) {
		NET_DBG("Encryption-only security is deprecated since IEEE 802.15.4-2015.");
		goto release_ctx;
	}

	/* c) Parse Auxiliary Security Header field. The procedure shall set SecurityLevel [...]
	 *    to the Security Level field [...] of the frame to be unsecured. [...] If the resulting
	 *    SecurityLevel is zero, the procedure shall return with a Status of
	 *    UNSUPPORTED_SECURITY.
	 *
	 * TODO: d)-h) implement - currently we have a single key and a single frame counter for all
	 * devices. The security feature MUST NOT be marked STABLE unless conditions d)-h) are
	 * properly implemented.
	 */
	if (!mhr->aux_sec || mhr->aux_sec->control.security_level != level) {
		NET_DBG("Incoming security procedure failed: Unsupported security.");
		goto release_ctx;
	}

	/* i) Unsecure frame. [...] If the inverse transformation process fails, the procedure shall
	 * return with a Status of SECURITY_ERROR.
	 *
	 * TODO: Implement private/open payload field distinction.
	 */
	if (level > IEEE802154_SECURITY_LEVEL_ENC) {
		level -= 4U;
	}

	authtag_len = level_2_authtag_len[level];
	ll_hdr_len = (uint8_t *)mpdu->mac_payload - net_pkt_data(pkt);
	src_addr = mhr->frame_control.has_src_pan ? &mhr->src_addr->plain.addr
						  : &mhr->src_addr->comp.addr;
	mpdu->mac_payload_length -= authtag_len;

	frame_counter = sys_le32_to_cpu(mhr->aux_sec->frame_counter);
	if (!ieee802154_decrypt_auth(&ctx->sec_ctx, mhr->frame_control.frame_type,
				     net_pkt_data(pkt), ll_hdr_len, mpdu->mac_payload_length,
				     authtag_len, ctx->pan_id, src_addr,
				     mhr->frame_control.src_addr_mode, frame_counter)) {
		NET_DBG("Incoming security procedure failed: Security error.");
		goto release_ctx;
	}

	/* TODO: j)-o) implement - currently we have no specific IE security and no device/key
	 * specific security level. The security feature MUST NOT be marked STABLE unless conditions
	 * j)-o) are properly implemented.
	 */

	/* We remove tag size from buf's length, it is now useless. */
	pkt->buffer->len -= authtag_len;

	ret = true;

release_ctx:
	k_sem_give(&ctx->ctx_lock);
out:
	return ret;
}
#endif /* CONFIG_NET_L2_IEEE802154_SECURITY */
