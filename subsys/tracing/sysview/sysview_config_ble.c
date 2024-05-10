#include <zephyr/kernel.h>
#include <tracing_sysview_ble.h>

SEGGER_SYSVIEW_MODULE BLEModule = {
  "M=BLE," \
  "0 ProdPkt seq=%u len=%u," \
  "1 SchedPkt pdu_plc=%u node_plc=%u," \
  "2 Tx len=%u," \
  "3 Enqueue plc=%u," \
  "4 Dequeue plc=%u," \
  "5 Ack plc=%u",              /* module description */
  6,                             /* num events in module */
  0,                             /* event offset, set by SEGGER_SYSVIEW_RegisterModule() */
  NULL,                          /* no module description callback */
  NULL,                          /* pointer to the next module, set by SEGGER_SYSVIEW_RegisterModule() */
};


void tracing_sysview_ble_start(void)
{
	/* Configure readable names for ISRs */
	SEGGER_SYSVIEW_SendSysDesc(
		"I#16=0-CLK/PWR/GPIO,I#17=1-RADIO,I#27=11-RTC0,I#29=13-RNG,I#33=17-RTC1,I#40=24-EGU4");

	SEGGER_SYSVIEW_RegisterModule(&BLEModule);
}