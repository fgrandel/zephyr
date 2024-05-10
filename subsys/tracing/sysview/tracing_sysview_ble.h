#ifndef ZEPHYR_TRACE_SYSVIEW_BLE_H
#define ZEPHYR_TRACE_SYSVIEW_BLE_H

#include <SEGGER_SYSVIEW.h>

#define SEGGER_SYSVIEW_BLE_PRODUCE_PKT  (BLEModule.EventOffset)
#define SEGGER_SYSVIEW_BLE_SCHEDULE_PKT (BLEModule.EventOffset + 1)
#define SEGGER_SYSVIEW_BLE_TX           (BLEModule.EventOffset + 2)
#define SEGGER_SYSVIEW_BLE_TX_ENQUEUE   (BLEModule.EventOffset + 3)
#define SEGGER_SYSVIEW_BLE_TX_DEQUEUE   (BLEModule.EventOffset + 4)
#define SEGGER_SYSVIEW_BLE_TX_ACK       (BLEModule.EventOffset + 5)

extern SEGGER_SYSVIEW_MODULE BLEModule;

/**
 * This function needs to be called exactly once by your application after
 * SystemView tracing has been initialized, e.g. in main().
 */
void tracing_sysview_ble_start(void);
#endif /* ZEPHYR_TRACE_SYSVIEW_BLE_H */
