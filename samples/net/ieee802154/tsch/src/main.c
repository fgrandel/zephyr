#include <zephyr/net/net_if.h>
#include <zephyr/net/net_l2.h>
#include <zephyr/net/ieee802154_mgmt.h>

static uint8_t broadcast_address_be[] = {0xff, 0xff};
static struct ieee802154_tsch_link link = {
	.slotframe_handle = 0,
	.timeslot = 1,
	.node_addr = {.addr = broadcast_address_be,
		      .len = IEEE802154_SHORT_ADDR_LENGTH,
		      .type = NET_LINK_IEEE802154},
	.rx = 1,
};


int main(void)
{
	struct net_if *iface = net_if_get_first_by_type(&NET_L2_GET_NAME(IEEE802154));
	uint16_t device_role;

	/* Basic configuration should have done by the net config framework (see
	 * ieee802154_settings.c):
	 *  * a default slotframe
	 *  * channel hopping and template timing - depending on the configured
	 *    PHY and channel page.
	 *  * for (PAN) coordinators only: a single beacon TX link, otherwise no
	 *    link
	 *
	 * We only assert that the interface is up initially.
	 */
	__ASSERT_NO_MSG(net_if_is_up(iface));

	net_mgmt(NET_REQUEST_IEEE802154_GET_DEVICE_ROLE, iface, &device_role, sizeof(device_role));
	link.handle = device_role == IEEE802154_DEVICE_ROLE_ENDDEVICE ? 0 : 1;
	net_mgmt(NET_REQUEST_IEEE802154_SET_TSCH_LINK, iface, &link, sizeof(void *));

	return 0;
}
