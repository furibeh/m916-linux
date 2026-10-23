# m916-linux

Herramienta para configurar en Linux el ratón **Redragon M916-PRO-1K** sin el programa
oficial de Windows. Habla con el ratón por `hidraw` con el mismo protocolo que el configurador
original, reconstruido por ingeniería inversa (ver [docs/PROTOCOL.md](docs/PROTOCOL.md)).

Probado con el receptor inalámbrico `3554:F5D5`.

## Requisitos

- Python 3. La línea de comandos no tiene dependencias externas; la interfaz gráfica usa
  Qt 6 (PySide6): `sudo dnf install python3-pyside6` en Fedora.
- Acceso al nodo `hidraw` del ratón. Instala la regla udev una vez:

```sh
sudo cp udev/70-redragon-m916.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

## Instalación

Desde el repositorio, sin instalar nada:

```sh
python3 -m m916.gui                # interfaz gráfica
python3 -m m916 info               # línea de comandos
```

O instalado para tu usuario, con los comandos `m916` y `m916-gui` y una entrada en el menú:

```sh
pip install --user .
install -Dm644 data/io.github.furibeh.m916-linux.desktop \
    ~/.local/share/applications/io.github.furibeh.m916-linux.desktop
```

## Interfaz gráfica

Pestañas de DPI (niveles, valores y colores), botones, rendimiento (frecuencia de sondeo,
debounce y opciones del sensor) y copia de seguridad. Los cambios se editan en local y
**Aplicar** escribe solo los campos modificados, verificando cada uno. Muestra la batería y
sigue en vivo los cambios hechos desde el propio ratón, como el nivel de DPI.

Para probarla sin el ratón: `python3 -m m916.gui --fake tests/fixtures/m916_flash.bin`.

## Línea de comandos

```sh
m916 info                     # estado, batería, DPI, opciones y botones
m916 dump copia.bin           # haz esto antes de cambiar nada
m916 dpi 3 3200               # DPI de un nivel (múltiplos de 50, hasta 26000; --y para Y)
m916 dpi-color 3 00ff00       # color del LED de un nivel
m916 dpi-levels 5             # niveles DPI activos
m916 dpi-active 2             # nivel en uso
m916 rate 1000                # 125 / 250 / 500 / 1000 Hz
m916 button 7 middle          # left|right|middle|back|forward|dpi-loop|dpi-up|dpi-down|
                              # scroll-left|scroll-right|report-rate|disabled|fire:P1:P2
m916 option debounce 4        # opciones de un byte (valor en bruto)
m916 restore copia.bin        # reescribe solo lo que cambió y verifica
m916 watch                    # notificaciones en vivo (DPI, batería…)
```

Cada escritura se verifica volviendo a leer. Si el ratón está dormido, la herramienta pide
moverlo y espera hasta 10 s.

## Tests

```sh
python3 -m unittest discover -s tests
```

Los tests simulan el ratón con un volcado real de la flash (`tests/fixtures/m916_flash.bin`),
así que no hace falta hardware. Los de la interfaz se saltan si PySide6 no está instalado.

## Estructura

- `m916/protocol.py`: transporte HID y acceso a la flash.
- `m916/config.py`: modelo de la configuración (decodificar, codificar, validar y diferencias).
- `m916/cli.py` y `m916/gui.py`: las dos interfaces, sobre el mismo modelo.
- `m916/fake.py`: ratón simulado para tests y desarrollo.

## Ingeniería inversa

`tools/` contiene lo necesario para repetir el análisis sobre el instalador oficial, que
**no** se incluye en este repositorio:

- `extract_native.py`: extrae `hidusb.dll` / `hidusb32.dll` embebidas en `Mouse Drive Beta.exe`.
- `dotnet_constants.py`: vuelca enums y constantes del ensamblado .NET.
- `ghidra/make_flatpak_shim.sh`: permite usar el Ghidra de flatpak en modo headless.
- `ghidra/DecompileAll.java`: decompila todas las funciones de la DLL a un único `.c`.

```sh
tools/extract_native.py "Mouse Drive Beta.exe" /tmp/m916-re
tools/ghidra/make_flatpak_shim.sh
~/.local/share/ghidra-flatpak-shim/support/analyzeHeadless /tmp/m916-re/proj m916 \
    -import /tmp/m916-re/hidusb.dll -scriptPath tools/ghidra \
    -postScript DecompileAll.java /tmp/m916-re/hidusb.c
```

## Estado

Funciona: lectura completa, DPI, colores, número de niveles y nivel activo, report rate,
botones básicos, opciones de un byte, volcado/restauración, notificaciones e interfaz gráfica.

Pendiente: atajos y macros en botones, significado de algunas opciones y LEDs.

## Licencia

[GPL-3.0-or-later](LICENSE). Proyecto independiente, sin relación con Redragon. Los nombres
de producto se usan solo para identificar el hardware compatible.
