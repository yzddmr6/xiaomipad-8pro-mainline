// SPDX-License-Identifier: MIT
// Preserve unfamiliar firmware and kernel messages verbatim.
const reasons = [
    [['detach', 'detached', 'physical disconnect', 'charger removed'], 'The charger was disconnected.', '充电器已断开。'],
    [['not-a-pps-adapter', 'no usable PPS APDO'], 'The charger has no usable PPS profile.', '充电器没有可用的 PPS 档位。'],
    [['serial-vooc-engaged', 'protocol upgrade', 'PPS handover', 'source protocol changed'], 'Charging switched to another protocol.', '已切换到另一种充电协议。'],
    [['vbat-cv', 'battery voltage reached', 'cell taper complete'], 'The cell reached its target voltage.', '电芯已达到目标电压。'],
    [['vbat-low'], 'Cell voltage is too low for fast charging.', '电芯电压低于快充范围。'],
    [['temp-out-of-window', 'battery temperature'], 'Battery temperature is outside the fast-charge range.', '电池温度超出快充范围。'],
    [['vbus-collapse', 'vbus-not-rising', 'contract-not-reached', 'QC failed to reach opening voltage'], 'The charger could not sustain the requested voltage.', '充电器未能维持请求的电压。'],
    [['contract-lost', 'PD reset'], 'The charger lost the negotiated voltage.', '充电器退出了协商电压档。'],
    [['pack-stall', 'no current', 'bus current too low', 'opening current unavailable'], 'Too little current reached the battery.', '充电电流不足，已退出快充。'],
    [['request refused', 'request-refused', 'request failed', 'prop-set-failed', 'start refused', 'stage refused', 'buck charger refused', 'UFCS initial request'], 'The charging request was refused.', '充电请求未获接受。'],
    [['source capabilities changed', 'source capabilities', 'qualified protocol retry'], 'The charger is renegotiating its output.', '充电器正在重新协商输出。'],
    [['battery limit', 'battery protection'], 'Battery protection limited charging.', '电池保护限制了充电。'],
    [['battery current too high', 'cell current too high', 'sustained battery overcurrent'], 'Battery current exceeded its limit.', '电池电流超过保护上限。'],
    [['bus current too high', 'bus voltage too high', 'VBUS outside limits', 'SC8547A OVP/OCP'], 'Input voltage or current exceeded the charging range.', '输入电压或电流超出充电范围。'],
    [['pump not ready', 'pump unavailable', 'stage turned itself off'], 'The charge pump is unavailable.', '电荷泵未能继续工作。'],
    [['stage temperature'], 'The charge pump reached its temperature limit.', '电荷泵已达到温度上限。'],
    [['gauge unavailable', 'stage read failed', 'read failed', 'pack temperature unavailable', 'charger type read failed', 'telemetry read failed', 'VBUS read failed'], 'A charging measurement is unavailable.', '无法读取充电所需的测量值。'],
    [['adapter authentication', 'authentication read failed', 'high current authorization inactive'], 'High-current charging authorization is unavailable.', '充电器未能维持大电流认证。'],
    [['profile float voltage not confirmed', 'float voltage not confirmed'], 'The target cell voltage was not confirmed.', '未能确认电芯目标电压。'],
    [['source voltage range exhausted', 'no high-voltage sink'], 'No suitable high-voltage profile is available.', '没有可用的高压充电档位。'],
    [['unknown source', 'UFCS source unavailable'], 'The charger is no longer recognized.', '无法识别当前充电器。'],
    [['system sleep', 'suspend timeout'], 'Charging changed as the tablet entered sleep.', '平板进入睡眠时切换了充电状态。'],
    [['pump watchdog deadline missed'], 'The charge pump timed out.', '电荷泵响应超时。'],
    [['policy disabled', 'policy stop', 'disabled', 'forced'], 'The charging controller ended fast charging.', '充电控制器已结束快充。'],
    [['source, policy or battery unavailable', 'buck FCC unavailable'], 'Charging conditions could not be confirmed.', '无法确认继续快充所需的条件。'],
    [['cleanup retry', 'pending stop', 'pending restore'], 'The controller is returning to normal charging.', '充电控制器正在恢复普通充电。'],
    [['ADSP restart', 'driver stopped', 'unbind'], 'The charging controller stopped.', '充电控制器已停止。'],
    [['USB source role'], 'The tablet switched to supplying power.', '平板已切换为对外供电。'],
];
export function exitReason(key, chinese) {
    const row = reasons.find(([keys]) => keys.includes(key));
    return row ? row[chinese ? 2 : 1] : key;
}
