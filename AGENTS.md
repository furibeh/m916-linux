# AGENTS.md

Guía para agentes de código que trabajen en este repositorio. Para el uso, ver `README.md`. El
protocolo completo está en `docs/PROTOCOL.md`, que es la referencia: actualízalo cuando
descubras algo.

## Qué es

Configurador en Linux para el ratón Redragon M916-PRO-1K (receptor `3554:F5D5`). Habla por
`hidraw` con el protocolo del configurador oficial de Windows ("Mouse Drive Beta"),
reconstruido por ingeniería inversa. Python 3.9 o superior. La CLI no tiene dependencias y la
GUI usa PySide6 (Qt 6).

## Estructura

- `m916/protocol.py`: transporte HID (paquetes de 17 bytes con checksum), lectura y escritura
  de flash, `MouseAsleep` / `ProtocolError`.
- `m916/config.py`: modelo de la configuración. Decodifica y codifica la imagen de flash,
  valida y calcula qué campos cambiaron (`changed_fields`) y qué escribir al restaurar
  (`restore_writes`). Las dos interfaces se apoyan en él: la lógica va aquí, no en la CLI ni
  en la GUI.
- `m916/profile.py`: perfiles JSON (`docs/PROFILES.md`) sobre el modelo de `config.py`:
  `dumps`/`save` y `apply`, que valida y acepta perfiles parciales; los nombres sin ruta van a
  `profiles_dir()` (XDG). En los tests, apunta `XDG_CONFIG_HOME` a un directorio temporal. Si añades un campo al
  modelo, añádelo también aquí y en la documentación del formato.
- `m916/keys.py`: nombres de tecla, usos HID y códigos evdev de Linux.
- `m916/cli.py`: `python3 -m m916 …`.
- `m916/gui.py`: `python3 -m m916.gui`. Un único hilo (`DeviceWorker`) es dueño del dispositivo.
  La ventana trabaja sobre una copia en memoria y **Aplicar** escribe la diferencia.
- `m916/fake.py`: ratón simulado sobre un volcado de flash. Debe imitar el firmware real,
  incluidas sus rarezas (ver más abajo).
- `tests/`: `unittest`, con `tests/fixtures/m916_flash.bin` (volcado real).
- `tools/`: utilidades de ingeniería inversa. El instalador oficial **no** está en el repo.
- `m916/icons/`: icono de la aplicación (SVG). `APP_ID` en `gui.py`, el `.desktop` de `data/`
  y el nombre del SVG deben coincidir.
- `.github/workflows/tests.yml`: tests en Fedora con PySide6 y en Python 3.9 sin PySide6.

## Comandos

```sh
python3 -m unittest discover -s tests       # todos los tests (~40 s; la GUI va offscreen)
python3 -m m916.gui --fake tests/fixtures/m916_flash.bin   # GUI sin hardware
python3 -m m916 info                         # estado del ratón real (solo lee)
```

Para capturas de la GUI sin pantalla: `QT_QPA_PLATFORM=offscreen` y
`python3 -m m916.gui --fake … --screenshot DIR`.

## Trabajar con el ratón real

- Leer (`info`, `dump`) es seguro. **Escribir modifica la flash del ratón del usuario**:
  pide confirmación antes, salvo que el usuario lo haya pedido explícitamente, y haz antes
  una copia con `m916 dump`.
- Si el ratón está dormido, solo responde el receptor (comando 0x03). Pide al usuario que lo
  mueva y reintenta.
- Toda escritura se verifica volviendo a leer. No quites esa verificación.

## Particularidades del firmware (comprobadas en el hardware)

- Los valores de un byte se guardan como `[v, 0x55 - v]` y los bloques terminan en
  `0x55 - suma`. Si un checksum no cuadra, el ratón usa un valor por defecto.
- **Bloques de macro (0x300 + 0x180·k):** el firmware solo guarda el bloque si se escribe desde
  su inicio como una macro válida con checksum. Cualquier otra escritura se confirma pero se
  descarta. Para borrar una macro se escribe `EMPTY_MACRO`. Por eso la restauración compara
  las macros por contenido y no byte a byte.
- Orden de escritura (`changed_fields`): primero el bloque de atajo o de macro y después la
  función del botón que lo usa. Al cambiar el número de niveles de DPI, el nivel activo nunca
  puede apuntar fuera de los niveles habilitados.
- Los atajos guardan teclas físicas (usos HID), no caracteres: la distribución de teclado la
  aplica el ordenador.
- Solo se leen los bloques de atajo y de macro de los botones que los usan; el resto de la
  imagen queda en `0xFF` (desconocido).

Si descubres otra regla del firmware, impleméntala también en `m916/fake.py` y añade un test.

## Ingeniería inversa

- Etiqueta cada dato de `docs/PROTOCOL.md` con su nivel de confianza: **[HW]** probado en el
  ratón, **[RE]** leído en el código, **[?]** hipótesis. Indica también el origen (método .NET
  o función de la DLL).
- El código de la interfaz está en el IL de .NET (`Mouse Drive Beta.exe`, clases
  `Mouse_Drive_Beta.*`, `DriverLib.*`). El protocolo de bajo nivel está en la DLL nativa
  embebida (`tools/extract_native.py` + Ghidra).
- `Language/*.xml` del instalador contiene las listas de funciones y opciones con sus valores.
  Merece la pena mirarlo antes de desensamblar.
- Algunos métodos de la DLL de .NET no se usan (p. ej. `KeyFunMapDriver.Set*Key`). Antes de
  dar un valor por bueno, comprueba quién escribe de verdad el campo.

## Convenciones

- Código, identificadores, comentarios, mensajes de la CLI y mensajes de commit en **inglés**.
  Textos de la GUI y documentación (`README.md`, `docs/`) en **español**.
- Cabecera `# SPDX-License-Identifier: GPL-3.0-or-later` en cada archivo Python.
- Estilo del código existente: funciones pequeñas, docstrings breves, sin dependencias nuevas
  sin motivo. La CLI debe seguir funcionando sin PySide6.
- Cada cambio de comportamiento lleva su test, contra el ratón simulado.
- Commits pequeños y temáticos: un título en imperativo y una lista de puntos en el cuerpo.
  Haz commit o push solo cuando el usuario lo pida.
