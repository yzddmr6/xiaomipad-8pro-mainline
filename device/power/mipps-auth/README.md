# Pinned MiPPS authentication

`source.json` locks the GPL-2.0-only script from
[blu-sharky/piano-mipps-auth](https://github.com/blu-sharky/piano-mipps-auth/tree/ccdc3ce7f5200ef736d7d552eb7b387ea3bb6fd9),
by bluseliu50, forked from ianchb's xiaomi-mipps-auth. Only the explicitly
licensed script is imported; the upstream tree has no root LICENSE.
`tools/build-device-debs.py --mipps-auth-only` verifies the source and patch
hashes, applies the patch with zero fuzz, checks the resulting hash, and builds
`piano-mipps-auth`. `--support-only` also builds this dependency.

The local patch selects `xiaomi-mca-usb`, refuses a charger whose current SVID
is not Xiaomi `0x2717`, but first requests the DFP data role when a PD source
is attached: the piano charger firmware reads the adapter identity only as DFP,
so checking the SVID before the role swap skipped every first attach. It
suppresses desktop notifications. The protocol,
public seed/key tables, command timeouts, one retry and adapter-ID recovery
remain the pinned community implementation. These protocol constants are
upstream input, not calibration or credentials extracted from this tablet.

For piano, the helper brackets authentication with `verify_process=1/0`, asks
for DFP data role through the MCA relay, queries version/voltage/temperature
(commands 1–3), sends the selected 16-byte seed (4) and a fresh 16-byte challenge
(5). It verifies the first 16 bytes of HMAC-SHA256(key, challenge || adapter ID),
recovers a missing/stale 16-bit ID by matching that digest, then sends remove
compensation (7) before verified (6), and writes `pd_verifed`. It does not send
reverse authentication (8) on piano. The misspelled `pd_verifed` is the existing
community/vendor-facing ABI. `ExecStopPost` attempts to clear an interrupted verify flag, ignoring errors because firmware rejects the write when no Xiaomi adapter is attached.

The kernel performs the PD/VDM transport and enforces source/length/value
limits. Userland does not select a voltage, current, battery float voltage or
SC8541 setting. Firmware may expose an additional/higher-current APDO after a
successful authentication. Fixed 9 V and ordinary PPS are independent kernel
policy paths; authentication alone does not promise 9 V, direct charging, 67 W,
or any charging rate. Upstream commit ccdc3ce7 reports a Xiaomi 120 W adapter
advertising 3.6–11 V / 6 A after authentication and a 36 W input test; that is
another unit's report, not a result for this tablet.

Vendor protocol cross-check: `unmoved21/android_kernel_xiaomi_onyx`, commit
`8276ba954d8076303a5ae0f7b15d4092d66bdff6`,
`drivers/power/supply/mca/mca_protocol/protocol_pd/qcom_tcpc/qcom_adsp_pd_protocol.c`
(GPL-2.0; source import by unmoved21). The exact 7→6/no-8 piano sequence and
ID recovery are the community's stock-batterysecret analysis, not independently
recovered from this unit's proprietary executable here.

The installed rules start a bounded oneshot on a Xiaomi online edge; the
kernel also emits an event when the SVID becomes available. Unknown SVID is
left untouched. The charging policy belongs to the single `xiaomi_mca` kernel
client. USBC_PAN ACKs belong to `pmic_glink_altmode`; no second owner is added.
Charging control is unverified; see [docs/STATUS.md](../../../docs/STATUS.md).
