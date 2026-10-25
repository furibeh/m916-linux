# Protocolo del Redragon M916-PRO-1K

Reconstruido por ingeniería inversa del configurador oficial de Windows
("Mouse Drive Beta" 1.0.0.4) y verificado sobre el ratón real con el receptor `3554:F5D5`.

Leyenda de confianza:
**[HW]** comprobado en el ratón · **[RE]** leído en el código (DLL/IL), sin probar en hardware ·
**[?]** hipótesis.

## Arquitectura del programa original

- `Mouse Drive Beta.exe`: interfaz WinForms en .NET Framework 4.8. La clase `DriverLib.UsbServer`
  solo hace P/Invoke a la DLL nativa.
- `hidusb.dll` / `hidusb32.dll`: C++ nativo, embebido en el .exe con Costura
  (ver `tools/extract_native.py`). Usa `SetupDi*`, `CreateFileW`, `WriteFile`/`ReadFile`
  y `HidD_SetFeature`. Toda la lógica del protocolo está aquí.
- `Config.ini`: valores en base64. VID `0x3554`; PID por cable `0xF55E`; PID del dongle
  `0xF55E,F55F`; sensor `3395` (PixArt PAW3395); MCU del ratón `CX52850P`; MCU del dongle
  `CX52650N`; `CH32V305`. El receptor que se probó usa **`0xF5D5`**, un PID que no aparece en
  `Config.ini` (la app también lee un campo `D4_PID`).

## Transporte  [HW]

- Interfaz HID 1 (`mi_01`), colección 5 (`col05`): página de fabricante `0xFF02`, report ID 8,
  16 bytes de entrada y 16 de salida. En Linux es un único `/dev/hidrawN` por interfaz; se
  identifica porque su descriptor contiene `85 08`.
- La app de Windows abre el mismo dispositivo dos veces, una para lectura y otra para escritura.
  En hidraw basta con `write()` + `read()` sobre el mismo nodo.
- La DLL deja ≥ 8 ms entre envíos y espera 1 s por respuesta, con hasta 5 reintentos.
- Comando 0x03 (online): **responde el receptor**; `data[0]=1` significa que el ratón está
  despierto y enlazado. Los demás comandos los responde el ratón: si está dormido no hay respuesta.

## Paquete  [HW]

```
08 CMD 00 ADDR_H ADDR_L LEN DATA[10] CHK          (17 bytes)
CHK = (0x55 - suma(bytes 0..15)) & 0xFF
```

La respuesta tiene el mismo formato y checksum, con el mismo `CMD`. En la escritura de flash
(0x07) el ratón devuelve el paquete idéntico byte a byte, y la DLL lo exige.

## Comandos  (`DriverLib.UsbCommandID`)

| ID | Nombre | Notas |
|---|---|---|
| 0x01 | EncryptionData | La DLL envía `LEN=8` y 4 bytes aleatorios. La UI principal no lo usa. [RE] |
| 0x02 | PCDriverStatus | `data[0]=1` al conectar la app. No hace falta para leer ni escribir. [RE] |
| 0x03 | DeviceOnLine | Respuesta `01 25 81 06`: online + 3 bytes sin identificar. [HW] |
| 0x04 | BatteryLevel | `nivel%, cargando, mV_hi, mV_lo` (p. ej. `64 00 10 48` = 100 %, 4168 mV). [HW] |
| 0x05/0x06 | DongleEnterPair / GetPairState | [RE] |
| 0x07 | WriteFlashData | `ADDR`, `LEN ≤ 10`, datos. [HW] |
| 0x08 | ReadFlashData | `ADDR`, `LEN ≤ 10`; los datos vuelven en `DATA`. [HW] |
| 0x09 | ClearSetting | Restablece valores de fábrica. [RE] |
| 0x0A | StatusChanged | **Notificación espontánea** del ratón (ver abajo). [HW] |
| 0x0B/0x0C | SetDeviceVidPid / SetDeviceDescriptorString | [RE] |
| 0x0D | EnterUsbUpdateMode | Actualización de firmware: no usar. [RE] |
| 0x0E/0x0F | Get/SetCurrentConfig | Perfil activo. [RE] |
| 0x10 | ReadCIDMID | [RE] |
| 0x11 | EnterMTKMode | [RE] |
| 0x12 | ReadVersionID | Respuesta `02 00`. [HW] |
| 0x14/0x15 | Set/Get4KDongleRGB | [RE] |
| 0x16/0x17 | Set/GetLongRangeMode | [RE] |
| 0xF0/0xF1 | Write/ReadKBCIdMID | [RE] |

### Notificación StatusChanged (0x0A)  [HW parcial]

`data[0]` es una máscara de bits (`DriverLib.DeviceStatusChanged`):

| bit | campo | bit | campo |
|---|---|---|---|
| 0 | isDPIChanged [HW] | 4 | isLogoLedChanged |
| 1 | isReportRateChanged | 5 | isLedBarChanged |
| 2 | isConfigChanged | 6 | isBatteryLevelChanged |
| 3 | isDPILedChanged | | |

## Mapa de la flash  (0x1B00 bytes, `DataParser.MAX_FLASH_SIZE`)

Casi todo valor va seguido de un byte de comprobación. Para un byte suelto es
`[v, 0x55 - v]`; para un bloque es `0x55 - suma(bloque)`. Si la comprobación falla, la DLL
usa un valor por defecto.

| Dirección | Contenido | Formato |
|---|---|---|
| 0x00 | Report rate: 1=1000 Hz, 2=500, 4=250, 8=125, 0x10=2000, 0x20=4000 [HW] | par |
| 0x02 | Número de niveles DPI (1–8) [HW] | par |
| 0x04 | Nivel DPI activo, empezando en 0 [HW] | par |
| 0x06 / 0x08 | xSpindown / ySpindown [RE] | par |
| 0x0A | silenceHeight (LOD): 1 = 1 mm, 2 = 2 mm [RE, lista `customComboBox_LOD`] | par |
| 0x0C + 4·i | DPI del nivel i: `[x, y, ex, chk]` [HW] | bloque |
| 0x2C + 4·i | Color del nivel i: `[R, G, B, chk]` [HW] | bloque |
| 0x4C, 4E, 50, 52 | LED de DPI: modo, brillo, velocidad, activado [RE] | pares |
| 0x54–0x5F | Bloque desconocido (`ff 00 ff 57 00 55 80 d5 03 52 00 55`), la DLL no lo lee [?] | |
| 0x60 + 4·k | Función del botón k: `[tipo, p1, p2, chk]` [HW] | bloque |
| 0xA0–0xA6 | Barra LED: `[modo, R, G, B, velocidad, brillo, chk]` [RE] | bloque |
| 0xA7 | Barra LED activada [RE] | par |
| 0xA9 | keyDebounceTime en ms [HW] | par |
| 0xAB | motionSyncEnable [RE] | par |
| 0xAD | allLedOffTime [RE] | par |
| 0xAF | linearCorrectionEnable [RE] | par |
| 0xB1 | rippleControlEnable [RE] | par |
| 0xB3 | moveOffLedEnable [RE] | par |
| 0xB5 | sensorCustomSleepTimeEnable [RE] | par |
| 0xB7 | sensorSleepTime [RE] | par |
| 0xB9 | sensorPowerSavingModeEnable [RE] | par |
| 0x100 + 0x20·k | Atajo de teclado del botón k [RE, formato confirmado en el volcado] | ver abajo |
| 0x300 + 0x180·k | Macro del botón k [RE, formato confirmado en el volcado] | ver abajo |

La escritura de la app original (`ProtocolDataCompareUpdate`) solo envía lo que cambió:
cada par en su dirección, los bloques DPI y de color de 4 en 4, y los atajos y macros en
trozos de 10 bytes.

### DPI del PAW3395  [HW]

Sale de `DPIControl.ValueToDPI`, en el IL de .NET:
`valor = dpi/50 - 1`; `x = y = valor & 0xFF`; `alto = valor >> 8`;
`ex = (alto << 2) | (alto << 6)`. Ejemplo: 26000 DPI → `x=y=0x07, ex=0x88`.
Los sensores que no son el 3395 usan las tablas de `driver_sensor.h`.

### Funciones de botón  (`DriverLib.KEY_CLASS`)

| tipo | función | p1 / p2 |
|---|---|---|
| 0 | Desactivado | |
| 1 | Botón de ratón | p1: 1 izq., 2 der., 4 central, 8 atrás, 0x10 adelante [HW] |
| 2 | DPI | p1: 1 ciclo, 2 subir, 3 bajar [HW] |
| 3 | Scroll horizontal | p1: 1 izquierda, 2 derecha [RE] |
| 4 | Disparo repetido | p1: intervalo en ms (10–255); p2: disparos (0–3, 0 = mientras se pulse) [RE] |
| 5 | Atajo de teclado o tecla multimedia | p1 = p2 = 0; los eventos van en el bloque de atajo del botón [RE] |
| 6 | Macro | p1: índice del propio botón (su bloque de macro); p2: repetición, ver abajo [RE] |
| 7 | Cambiar report rate | [HW] |
| 8 | Luces | p1: 0 todas, 3 barra LED, 4 cambiar efecto (la app no lo ofrece en este modelo) [RE] |
| 9 | Cambiar configuración | [RE] |
| 0xA | Bloqueo de DPI | p1 = dpi/50 − 1 [RE] |
| 0xB | Scroll vertical | p1: 1 arriba, 2 abajo [RE] |

Los slots 1–16 son posiciones en la flash. La correspondencia física solo está confirmada
para los 5 primeros.

Origen [RE]: IL de `CustomKeyFunction.customSubKeyFunction_Main_ButtonSelect`,
`customSubKeyFunction_Sub_ButtonSelect` y `UpdateMacro`, y la lista `KeyFunction` /
`SubKeyFunction` de `Language/*.xml`, donde `value` es un número de 16 bits del que p1 es el byte
bajo y p2 el alto (`LanguageFile.SetLanguage`). Las funciones `KeyFunMapDriver.Set*Key` de la
DLL de .NET no se usan.

**p2 de las macros** (`FormMain.customButton_Save_Click`): 1–252 = número de veces,
253 = hasta volver a pulsar el botón, 254 = mientras se mantenga pulsado, 255 = hasta pulsar
cualquier botón.

### Atajos de teclado  (32 bytes)  [RE + volcado]

```
[n]  n×[estado|tipo, código, código2]  chk        n = 2..6 acciones
estado: 0x80 = pulsar, 0x40 = soltar      tipo (nibble bajo) = HID_CODE_TYPE
HID_CODE_TYPE: 0 modificador (bitmask), 1 tecla normal (uso HID), 2 multimedia,
               3 power, 4 ratón, 5 mover XY
chk = 0x55 - suma(bytes 0..3n)
```
Ejemplo del volcado (Ctrl+C): `04 80 01 00 81 06 00 41 06 00 40 01 00 c1`.

La app (`ValueConvert.App_AddShortCutKey`) admite hasta 3 teclas: las pulsa en orden y las
suelta en orden inverso. Una tecla multimedia (`App_AddMultiMedia`) son dos eventos de tipo 2
con el uso de la página Consumer en `código` (byte bajo) y `código2` (alto), p. ej. Silenciar:
`02 82 e2 00 42 e2 00 cb`.

### Macros  (0x180 bytes)  [RE + volcado]

```
[0]      longitud del nombre (1..30)
[1..30]  nombre (ASCII)
[0x1F]   n = número de pasos (2..70)
[0x20..] n×[estado|tipo, código, código2, retardo_hi, retardo_lo]   (retardo en ms, big-endian)
chk      0x55 - suma(bytes 0x1F .. 0x1F+5n)
```
Ejemplo del volcado ("jitter"): `84 01 00 00 0a` = pulsar botón izquierdo y esperar 10 ms;
`44 01 00 00 0a` = soltar y esperar 10 ms.

**Escritura** [HW]: el firmware solo guarda un bloque de macro si se escribe desde su inicio
y forma una macro con checksum correcto. Cualquier otra escritura en esa zona (a mitad de
bloque, o un bloque todo `0xFF`) se confirma pero se descarta. Para borrar una macro, la app
escribe la forma vacía `00` + 30×`ff` + `00 55` (longitud 0, 0 pasos); los bytes de pasos
anteriores quedan detrás, pero el firmware los ignora.

## Listas de opciones de la app  [RE, `Language/0-English.xml`]

Valores de los desplegables; falta confirmar en el IL a qué dirección escribe cada uno.

- `customComboBox_PowerSaveTime` (apagado de luces en reposo, probablemente `allLedOffTime`):
  1 = 10 s, 3 = 30 s, 6 = 1 min, 30, 60, 90, 120, 150, 180, 210, 240 (unidades de 10 s).
- `customComboBox_FullPerformance` (tiempo en alto rendimiento, probablemente
  `sensorSleepTime`): 3 = 30 s, 6 = 1 min, 12, 30, 60, 90 (unidades de 10 s).
- `customComboBox_ModeSelect`: 0 LP (bajo consumo), 1 HP (alto rendimiento), 0x10 con cable.
- `customComboBox_DPIEffect` (LED de DPI): 0 apagado, 1 fijo, 2 parpadeo.
- `customComboBox_LEDMode`: 0 apagado, 1 arcoíris en movimiento, 2 color fijo, 3 arcoíris
  fijo, 4 neón, 5 arcoíris parpadeante, 6 multicolor fijo.

## Pendiente

- Confirmar a qué campo escribe cada desplegable de la lista anterior.
- Bloque 0x54–0x5F y los 3 bytes extra de la respuesta online.
- Comando 0x01 (EncryptionData): ¿es necesario en algún caso?
