# Redragon M916-PRO-1K protocol

Reconstructed by reverse engineering the official Windows configurator
("Mouse Drive Beta" 1.0.0.4) and verified on the real mouse with the `3554:F5D5` receiver.

Confidence legend:
**[HW]** tested on the mouse · **[RE]** read in the code (DLL/IL), not tested on hardware ·
**[?]** hypothesis.

## Architecture of the original program

- `Mouse Drive Beta.exe`: WinForms interface on .NET Framework 4.8. The `DriverLib.UsbServer`
  class only does P/Invoke into the native DLL.
- `hidusb.dll` / `hidusb32.dll`: native C++, embedded in the .exe with Costura
  (see `tools/extract_native.py`). It uses `SetupDi*`, `CreateFileW`, `WriteFile`/`ReadFile`
  and `HidD_SetFeature`. All the protocol logic is here.
- `Config.ini`: base64 values. VID `0x3554`; cable PID `0xF55E` (checked [HW]); dongle PID
  `0xF55E,F55F`; sensor `3395` (PixArt PAW3395); mouse MCU `CX52850P`; dongle MCU
  `CX52650N`; `CH32V305`. The tested receiver uses **`0xF5D5`**, a PID that does not appear in
  `Config.ini` (the app also reads a `D4_PID` field).

## Transport  [HW]

- HID interface 1 (`mi_01`), collection 5 (`col05`): vendor page `0xFF02`, report ID 8,
  16 bytes of input and 16 of output. On Linux there is a single `/dev/hidrawN` per interface;
  it is the one whose descriptor contains `85 08`.
- The Windows app opens the same device twice, once for reading and once for writing.
  With hidraw, `write()` + `read()` on the same node is enough.
- The DLL waits ≥ 8 ms between sends and 1 s for a reply, with up to 5 retries.
- Command 0x03 (online): **the receiver answers it**; `data[0]=1` means the mouse is awake
  and linked. The receiver also answers 0x06. The mouse answers the rest (0x17 included):
  if it is asleep there is no reply. [HW]
- Read requests without parameters use `LEN=0` and zeroed data. Some replies carry valid data
  beyond `LEN` (0x03 returns `LEN=1` with 4 bytes; 0x04, `LEN=2` with 4). [HW]

## Packet  [HW]

```
08 CMD 00 ADDR_H ADDR_L LEN DATA[10] CHK          (17 bytes)
CHK = (0x55 - sum(bytes 0..15)) & 0xFF
```

The reply has the same format and checksum, with the same `CMD`. On flash writes (0x07) the
mouse echoes the packet byte for byte, and the DLL requires it.

## Commands  (`DriverLib.UsbCommandID`)

| ID | Name | Notes |
|---|---|---|
| 0x01 | EncryptionData | The DLL sends `LEN=8` and 4 random bytes in `data[0..3]` (`UsbServer_ReadEncryption`). The main UI does not use it. [RE] The mouse answers with `LEN=8`: `data[0..3]` is a deterministic function of the 4 bytes sent (`00 00 00 00` → `17 05 00 00`, `01 00 00 00` → `18 05 00 01`, `00 00 00 01` → `17 05 01 04`), and `data[4..5]` is always `17 05`. It looks like a challenge and response. It is **not needed** to read or write the flash, nor for 0x0F or 0x16. [HW] |
| 0x02 | PCDriverStatus | `data[0]=1` when the app connects. Not needed to read or write. [RE] |
| 0x03 | DeviceOnLine | Reply `LEN=1`, `01 25 81 06`: online + 3 receiver bytes. The 3 bytes do not change with the mouse asleep or when the receiver is unplugged and plugged in again; their meaning is still unclear. [HW] |
| 0x04 | BatteryLevel | `level%, charging, mV_hi, mV_lo` (e.g. `64 00 10 48` = 100 %, 4168 mV). [HW] |
| 0x05 | DongleEnterPair | `LEN=2`, `[cid, mid]`; the app sends `00 00` (`UsbServer_EnterDonglePairWithCidMid`). The reply echoes the packet; the receiver drops the mouse and waits ~20 s. To link it again: left + right + middle at once until the LED blinks yellow. [HW] |
| 0x06 | GetPairState | Request `LEN=0` (`UsbServer_ReadDonglePairStatus`). The receiver answers it, also with the mouse asleep: `LEN=2`, `[state, seconds]`. `00 00` no pairing since it was plugged in; `01 nn` pairing, with nn = seconds left (starts at 0x13 and counts down once per second); `02 00` paired, and it stays so. A failure has not been seen. [HW] |
| 0x07 | WriteFlashData | `ADDR`, `LEN ≤ 10`, data. [HW] |
| 0x08 | ReadFlashData | `ADDR`, `LEN ≤ 10`; the data comes back in `DATA`. [HW] |
| 0x09 | ClearSetting | Request `LEN=0` (`UsbServer_SetClearSetting`); the reply echoes the packet. Resets **only the header 0x00–0xBF of the active slot** to the factory values (identical to the one of never used slots). It does not touch the shortcut and macro blocks, the other slots, long range or pairing. [HW] |
| 0x0A | StatusChanged | **Spontaneous notification** from the mouse (see below). [HW] |
| 0x0B/0x0C | SetDeviceVidPid / SetDeviceDescriptorString | [RE] |
| 0x0D | EnterUsbUpdateMode | Firmware update: do not use. [RE] |
| 0x0E | GetCurrentConfig | Active slot (onboard profile, see "One image per slot"). Request `LEN=0` (`UsbServer_ReadConfig`); reply `LEN=1`, `data[0]` = slot 0–3 (`00` = slot 1). The mouse answers it, not the receiver: asleep, there is no reply. [HW] The app offers 4 (`customComboBox_Config`, values 0–3). `Mouse.slot()` |
| 0x0F | SetCurrentConfig | `LEN=1`, `data[0]` = slot 0–3 (`UsbServer_SetCurrentConfig`). The reply echoes the packet; the change applies at once and 0x01 is not needed. [HW] Then the mouse sends a 0x0A with `isConfigChanged` (`data[0]=04`) within 0.5 s, through the receiver and through the cable [HW]. `Mouse.set_slot()` verifies it with 0x0E. |
| 0x10 | ReadCIDMID | [RE] |
| 0x11 | EnterMTKMode | [RE] |
| 0x12 | ReadVersionID | Reply `LEN=2`, `02 00`. [HW] |
| 0x14/0x15 | Set/Get4KDongleRGB | [RE] |
| 0x16 | SetLongRangeMode | `LEN=10`, `data[0]` = 0 off, 1 long range (`UsbFinder_SetLongRangeMode`); the reply echoes the packet. The mouse keeps working normally. **It survives unplugging the receiver and is not in the flash image of the slot**, so `restore` does not undo it. [HW] `Mouse.set_long_range()` verifies it with 0x17; `m916 long-range on\|off`. |
| 0x17 | GetLongRangeMode | Request `LEN=0`; reply `LEN=10`, `data[0]` = 0/1. The mouse answers it: if it is asleep, there is no reply. [HW] `Mouse.long_range()`; `m916 long-range` and `info`. Neither JSON profiles nor dumps include it, and the GUI applies it at once, outside **Apply**. |
| 0xF0/0xF1 | Write/ReadKBCIdMID | [RE] |
| 0xB3 | GetSlaveVersion | Read `LEN=0`; version in `data[0..1]` (`UsbFinder_GetSlaveVersion`). Missing from `UsbCommandID`. [RE] |

### StatusChanged notification (0x0A)  [HW partial]

`data[0]` is a bit mask (`DriverLib.DeviceStatusChanged`):

| bit | field | bit | field |
|---|---|---|---|
| 0 | isDPIChanged [HW] | 4 | isLogoLedChanged |
| 1 | isReportRateChanged | 5 | isLedBarChanged |
| 2 | isConfigChanged: slot change, with 0x0F or with function 9 [HW] | 6 | isBatteryLevelChanged |
| 3 | isDPILedChanged [HW] | | |

### Cable connection  [HW]

With the USB cable plugged in, the mouse shows up as another device, `3554:F55E` ("Compx 3-Mode
Mouse", three HID interfaces). Only the one with `85 08` in its descriptor speaks the
protocol. Of the other two, one does not answer and the other gives `EPIPE` on write. While the
cable is plugged in, the mouse **only talks through it**:

| Command | Receiver (`F5D5`) | Cable (`F55E`) |
|---|---|---|
| 0x03 | `LEN=1 00 25 81 06`: not linked | `LEN=1 01 25 81 06`: always online |
| 0x06 | the receiver answers it as usual | byte 2 = `01`, `LEN=0` (rejected?) |
| 0x04, 0x08, 0x0E, 0x12, 0x17… | no reply | same replies as over the air |

- Over the cable, reading and writing the flash works as over the air, with the same
  verification. Tested by changing and restoring the polling rate; the image was identical.
- Long range (0x16/0x17) works over the cable as over the air: 0x16 is echoed and 0x17 reads
  the new value back. Turned on and off over the cable and then over the receiver with
  `m916 long-range`; a dump of the active slot before and after was identical, so 0x16 does
  not touch its flash image.
- Over the cable the mouse does not go idle: it sat still for 190 s and kept answering 0x03
  and 0x04. The battery reports that it is charging.
- On plugging in, the receiver loses the link at once (it may come back for a moment during
  the switch). The cable `hidraw` node takes between ~1 and ~6 s to appear.
- On unplugging, if the mouse switch is on 2.4G, the receiver gets the link back in
  ~1.2–1.4 s. In the cable-only position, the mouse turns off.
- That is why the tool probes each device with 0x03, the cable first, and uses the first one
  that answers "online". The GUI switches connection when the current one stops seeing it.
- In 150 s of listening on the cable (charging, at 95–100 %) no 0x0A arrived, not even when
  changing the active DPI level from the PC (0x04) and setting it back. That is, changing the
  flash from the PC does not send a notification. [HW] The exception is 0x0F: switching slots
  does send 0x0A `isConfigChanged`, also over the cable. [HW]
- The 0x0A from the buttons does arrive over the cable: with `m916 watch` on the cable, each
  press of the DPI button gave `dpi` (`data[0]=01`). [HW]

### Idle and sleep  [HW]

With the mouse still and the values of this unit (`led-off-time` = 1 min,
`full-performance` = 0, LP mode): after ~4 s, 0x03 starts returning `00` and the mouse links
up for 1 s out of every 5. After ~55 s it stops linking up (deep sleep) until it moves. With
`led-off-time` = 10 s, the LEDs turn off after 10 s and there is no periodic link-up after
~3 s: the periodic link-up lasts as long as the LEDs are on (tested only with those two
values). Polling 0x03 every second does not keep it awake.

The mouse also writes to the flash by itself: the DPI button saves the active level (0x04)
and the Lights button, the DPI LED enable (0x52). Both notify it with 0x0A.

## Flash map  (0x1B00 bytes, `DataParser.MAX_FLASH_SIZE`)

**One image per slot** [HW]: the mouse has 4 onboard profiles, which the tool calls *slots*
so as not to confuse them with JSON profiles. With 0x0F (or with a button set to function 9)
flash reads and writes go to the image of another slot, at the same addresses. The number of
bytes that differ between two slots depends on how each one has been used; on this unit,
slot 2 had other colors and another brightness, whose change shows on the LED.

Use in the tool:

- `info` shows the active slot and `m916 slot [N]` reads or changes it. `dump`, `restore` and
  `profile save/load` work on the active one or, with `--slot N`, switch to N and go back to
  the original when done, also if something fails (`protocol.on_slot`).
- A dump is the image as is and does not say which slot it comes from. Since all slots use
  the same addresses, it can be restored to another slot to copy it.
- Each slot change, with 0x0F or with a button set to function 9, is notified with 0x0A
  `isConfigChanged` [HW]. With 0x0F it was checked through the receiver and through the
  cable; with function 9, through the receiver and through the cable. Function 9 cycles through the 4 slots
  (4 → 1 → 2 → 3 → 4 → 1) [HW].
- The GUI queries 0x0E on a 0x0A with `isConfigChanged` and on every load; it does not poll
  it. Before writing (`apply`, `restore`) it queries it again and does not write if it has
  changed. The notification of its own 0x0F does not trigger another load.
- Polling 0x0E every 3 s (with 0x03 every second) **does not prevent idling** [HW]. With
  `led-off-time` = 1 min and LP mode, through the receiver, the mouse goes to the periodic
  link-up (1 s out of every 5) and to deep sleep ~57 s after the last movement, the same as
  polling only 0x03. A 0x0E query that falls at the end of a periodic link-up gets no reply.
  The GUI does not need it, but the data point is useful if polling is ever needed.
- Over the cable, `m916 slot 2`/`slot 1` and `dump --slot 2` work and are verified with 0x0E
  [HW].

Almost every value is followed by a checksum byte. For a single byte (a *pair*) it is
`[v, 0x55 - v]`; for a *block* it is `0x55 - sum(block)`. If the checksum fails, the DLL uses
a default value.

**Factory values** [HW] (slots 3 and 4 never used, and slot 1 after 0x09): 1000 Hz, 5 levels
(the 2nd active): 1000, 2000, 4000, 8000 and 26000 DPI, with the colors `ff0000`, `0000ff`,
`00ff00`, `ff00ff` and `ffff00`; DPI LED breathing, brightness 0x80, speed 3, enabled; LOD 1;
debounce 8 ms; motion sync 1; lights off after 1 min; the other options at 0;
Peak Performance 1 min; LP; buttons as in the dump in `tests/fixtures` (button 12 is rapid
fire 10 ms × 3). 0x54–0x5F = `ff 00 ff 57 00 55 80 d5 03 52 00 55`, 0xBB = 10, 0xBD not
written.

| Address | Content | Format |
|---|---|---|
| 0x00 | Report rate: 1=1000 Hz, 2=500, 4=250, 8=125, 0x10=2000, 0x20=4000 [HW] | pair |
| 0x02 | Number of DPI levels (1–8) [HW] | pair |
| 0x04 | Active DPI level, starting at 0. The mouse saves it to the flash when its DPI button is pressed [HW] | pair |
| 0x06 / 0x08 | xSpindown / ySpindown (read by `MouseConfigParser`; the app does not show them). With xSpindown = 3 or 50 nothing is noticeable and the raw reports (hidraw, report ID 1) do not change: with circles drawn by hand, X/Y = 1.17 with 50 and 1.13 with 0, and there is no inertia tail. Meaning unknown [?] | pair |
| 0x0A | silenceHeight (LOD): 1 = 1 mm, 2 = 2 mm; with 2 the sensor tracks at a greater height [HW] | pair |
| 0x0C + 4·i | DPI of level i: `[x, y, ex, chk]` [HW] | block |
| 0x2C + 4·i | Color of level i: `[R, G, B, chk]` [HW] | block |
| 0x4C, 4E, 50, 52 | DPI LED: mode, brightness, speed, enabled [HW] | pairs |
| 0x54–0x5F | Unknown; the DLL does not read it. It has the shape of the DPI LED: `[R, G, B, chk]` = `ff 00 ff 57` (the checksum is right) and the pairs 0x58 `00`, 0x5A `80`, 0x5C `03`, 0x5E `00`, as mode, brightness, speed and enabled. The logo LED (bit `isLogoLedChanged` of 0x0A)? [?] It does not change when the DPI button is pressed, and writing 0x5E = 1 turns nothing on: the M916-PRO-1K only has the DPI LED [HW] | |
| 0x60 + 4·k | Function of button k: `[type, p1, p2, chk]` [HW] | block |
| 0xA0–0xA6 | LED bar: `[mode, R, G, B, speed, brightness, chk]` [RE] | block |
| 0xA7 | LED bar enabled [RE] | pair |
| 0xA9 | keyDebounceTime in ms [HW] | pair |
| 0xAB | motionSyncEnable. With 0 the movement feels less smooth (subjective observation) [HW] | pair |
| 0xAD | allLedOffTime, ×10 s. With 1, the LED turns off after 10 s; the mouse stops linking up at the same time (see "Idle and sleep") [HW] | pair |
| 0xAF | linearCorrectionEnable. With 1 the movement snaps to straight lines [HW] | pair |
| 0xB1 | rippleControlEnable. With 1 the movement is smoother (filtered) [HW] | pair |
| 0xB3 | moveOffLedEnable. With 1 it is saved, but no effect is seen: the LED does not change when moving or lifting the mouse (tested with the LED breathing) [HW: saved; effect not observed] | pair |
| 0xB5 | sensorCustomSleepTimeEnable ("Peak Performance"). With 1 and 0xB7 = 3 (30 s), after letting go of the mouse it stayed linked for 12 s in a row and deep sleep came after ~69 s instead of ~55 s. A single measurement [HW, provisional] | pair |
| 0xB7 | sensorSleepTime, ×10 s. It is saved; its exact relation to the observed time (~14 s more with 30 s) is still unclear [HW: saved] | pair |
| 0xB9 | sensorPowerSavingModeEnable: 0 = LP, 1 = HP. It is saved; in HP light sleep started after ~6 s instead of ~4 s, and deep sleep after ~56 s, the same as in LP: no clear difference [HW: saved; effect unclear] | pair |
| 0xBB | Unknown pair: `0a 4b` (10) in both slots and in the dump; `MouseConfigParser` (DLL) ends at 0xB9 and does not read it [?] | pair |
| 0xBD | Unknown pair: `01 54` (1) in slot 2, `ff ff` (not written) in slot 1; `MouseConfigParser` does not read it either [?] | pair |
| 0x100 + 0x20·k | Keyboard shortcut of button k [RE, format confirmed in the dump] | see below |
| 0x300 + 0x180·k | Macro of button k [RE, format confirmed in the dump] | see below |

The original app's write (`ProtocolDataCompareUpdate`) only sends what changed: each pair at
its address, the DPI and color blocks 4 bytes at a time, and shortcuts and macros in chunks of
10 bytes.

### PAW3395 DPI  [HW]

From `DPIControl.ValueToDPI`, in the .NET IL:
`value = dpi/50 - 1`; `x = y = value & 0xFF`; `high = value >> 8`;
`ex = (high << 2) | (high << 6)`. Example: 26000 DPI → `x=y=0x07, ex=0x88`.
Sensors other than the 3395 use the tables in `driver_sensor.h`.

### Button functions  (`DriverLib.KEY_CLASS`)

| type | function | p1 / p2 |
|---|---|---|
| 0 | Disabled | |
| 1 | Mouse button | p1: 1 left, 2 right, 4 middle, 8 back, 0x10 forward [HW] |
| 2 | DPI | p1: 1 cycle, 2 up, 3 down [HW] |
| 3 | Horizontal scroll | p1: 1 left, 2 right. One wheel step (±120 in libinput) per press; it does not repeat when held [HW] |
| 4 | Rapid fire | `BTN_LEFT` clicks. p1: ms **down and then up** for each click (10–255), so a shot takes 2·p1; p2: shots (0–3; 0 = while held, and it stops on release). Measured with libinput: `fire:100:3` → 3 clicks of 100 + 100 ms; `10:1`, `255:2` and `50:0` likewise [HW] |
| 5 | Keyboard shortcut or media key | p1 = p2 = 0; the events are in the button's shortcut block. The press events go out when the button is pressed and the release events when it is released, with 20 ms between events [HW] |
| 6 | Macro | p1: index of the button itself (its macro block); p2: repeat, see below [HW] |
| 7 | Switch report rate | [HW] |
| 8 | Lights | p1: 0 all, 3 LED bar, 4 switch effect (the app does not offer it on this model) [RE]. With p1 = 0, each press toggles the DPI LED: **the mouse rewrites 0x52 in the flash** and sends the `dpi-led` notification (bit 3) [HW]. With p1 = 4 it does nothing, neither on the LED nor in the flash [HW]. The tool offers p1 = 0 as `lights`: written with `m916 button 4 lights`, 4 presses over the cable gave 4 `dpi-led` notifications, one per press, and 0x52 ended at 1 [HW] |
| 9 | Switch config | p1 = p2 = 0: one press moves to the next slot and notifies it with 0x0A `isConfigChanged`; it cycles through the 4 (from 4 back to 1). Tested with function 9 on the same button of the 4 slots: each slot has its own buttons. The app does not offer it (`ConfigSelect`: "Switch config is not supported"); the tool offers it as `next-slot`: written with `m916 button 5 next-slot`, one press over the cable moved from slot 1 to 2 and sent `isConfigChanged` [HW] |
| 0xA | DPI lock | p1 = dpi/50 − 1 (50–12800 DPI). Only while held; on release it goes back to the DPI of the level and the LED does not change. Tested with 400 and 12800 (p1 = 255) [HW] |
| 0xB | Vertical scroll | p1: 1 up, 2 down. One wheel step per press; it does not repeat when held [HW] |

Buttons 1–16 are positions in the flash. The physical mapping is only confirmed for the first
5.

Source [RE]: IL of `CustomKeyFunction.customSubKeyFunction_Main_ButtonSelect`,
`customSubKeyFunction_Sub_ButtonSelect` and `UpdateMacro`, and the `KeyFunction` /
`SubKeyFunction` list in `Language/*.xml`, where `value` is a 16-bit number whose low byte is
p1 and high byte is p2 (`LanguageFile.SetLanguage`). The `KeyFunMapDriver.Set*Key` functions
of the .NET DLL are not used.

**Macro p2** (`FormMain.customButton_Save_Click`): 1–252 = number of times,
253 = until the button is pressed again, 254 = while held, 255 = until any button is pressed.
[HW] Each mode was tested with `+a 50 -a 200`, and in all of them the macro stops at the end
of a step, without leaving keys pressed. In mode 255, the click that stops it **does not reach
the computer** (with a right click no `BTN_RIGHT` shows up).

### Keyboard shortcuts  (32 bytes)  [HW]

```
[n]  n×[state|type, code, code2]  chk        n = 2..6 actions
state: 0x80 = press, 0x40 = release      type (low nibble) = HID_CODE_TYPE
HID_CODE_TYPE: 0 modifier (bitmask), 1 normal key (HID usage), 2 media,
               3 power, 4 mouse, 5 move XY
chk = 0x55 - sum(bytes 0..3n)
```
Example from the dump (Ctrl+C): `04 80 01 00 81 06 00 41 06 00 40 01 00 c1`.

The app (`ValueConvert.App_AddShortCutKey`) allows up to 3 keys: it presses them in order and
releases them in reverse order. A media key (`App_AddMultiMedia`) is two events of type 2
with the Consumer page usage in `code` (low byte) and `code2` (high), e.g. Mute:
`02 82 e2 00 42 e2 00 cb`.

Checked on the hardware [HW]: `shift+a` (`04 80 02 00 81 04 00 41 04 00 40 02 00 c3`) types
"A"; `ctrl+alt+shift` presses ctrl, alt and shift and releases them in reverse order. The keys
come from a keyboard device of the receiver, separate from the mouse. While the button is
held, the keys stay pressed until it is released: libinput does not see the releases until
then. Volume up (`e9 00`) and Mute (`e2 00`) work; when Volume up is held, the system repeats
it. libinput does not show Consumer page keys.

### Macros  (0x180 bytes)  [HW]

```
[0]      name length (1..30)
[1..30]  name (ASCII)
[0x1F]   n = number of steps (2..70)
[0x20..] n×[state|type, code, code2, delay_hi, delay_lo]   (delay in ms, big-endian)
chk      0x55 - sum(bytes 0x1F .. 0x1F+5n)
```
Example from the dump ("jitter"): `84 01 00 00 0a` = press the left button and wait 10 ms;
`44 01 00 00 0a` = release and wait 10 ms.
The delays are kept to the millisecond (measured with libinput): `+a 50 -a 200` gives a
250 ms cycle. [HW]

**Writing** [HW]: the firmware only saves a macro block if it is written from its start and
forms a macro with a correct checksum. Any other write to that area (in the middle of a
block, or an all-`0xFF` block) is acknowledged but discarded. To clear a macro, the app
writes the empty form `00` + 30×`ff` + `00 55` (length 0, 0 steps); the bytes of earlier steps
stay behind, but the firmware ignores them.

## Single-byte options

What each control of the app writes (handlers `_3In1.customComboBox_*`,
`FormMain.customComboBox_*` and `customCheckBox_*`) and the values of its lists in
`Language/*.xml`. The list values are decimal unless they have `0x`
(`ValueConvert.StringToInt`).

| Control | Field | Values |
|---|---|---|
| LOD | 0x0A `silenceHeight` | 1 = 1 mm, 2 = 2 mm [HW] |
| Mode (`ModeSelect`) | 0xB9 `sensorPowerSavingModeEnable` | 0 = LP (low power), 1 = HP (high performance); the "wired" option (10) is not written |
| `FullPerformance` checkbox | 0xB5 `sensorCustomSleepTimeEnable` | 0/1; enables the next list |
| `FullPerformance` list | 0xB7 `sensorSleepTime` | ×10 s: 3, 6, 12, 30, 60, 90 (30 s … 15 min) |
| `PowerSaveTime` (lights when idle) | 0xAD `allLedOffTime` | ×10 s: 1, 3, 6, 30, 60, 90, 120, 150, 180, 210, 240 (10 s … 40 min) |
| DPI LED effect | 0x52 enabled, 0x4C mode | off: enabled = 0 and the mode does not change; 1 steady, 2 "Flicker" in the app, which is actually **breathing** (fades up and down smoothly) [HW] |
| DPI LED brightness | 0x4E | levels 1–10 → 16, 30, 60, 90, 128, 150, 180, 210, 230, 255 (`DPIIndexToBrightness`) [HW] |
| DPI LED speed | 0x50 | 1–5: the higher the value, the faster the breathing (3 ≈ 1 cycle every 5 s, 5 ≈ 2 every 5 s) [HW] |
| `LEDMode` (LED bar; the M916-PRO-1K does not have one) | 0xA7 enabled, 0xA0 mode | 0 off, 1 moving rainbow, 2 steady color, 3 steady rainbow, 4 neon, 5 blinking rainbow, 6 steady multicolor |

The app only shows the brightness with the steady effect and the speed with breathing
(`ValueConvert.SetDPIEffect`).

## Pending

- xSpindown/ySpindown (0x06/0x08): no measurable effect; is it used with other sensors?
- Peak Performance (0xB5/0xB7): repeat the measurement with other times to see the relation
  with 0xB7.
- Sensor mode (0xB9): see whether the power draw changes with a long battery measurement.
- Block 0x54–0x5F, pairs 0xBB and 0xBD and the 3 extra bytes of the online reply.
- Command 0x01 (EncryptionData): not needed for anything tested; the algorithm is still
  unknown.
- Long range (0x16): the range improvement has not been measured.
- Pairing: does state 02 arrive on pairing or at the end of the 20 s window? A failure has not
  been seen.
- Buttons 6–16: the physical mapping is still unconfirmed.
