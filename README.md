# m916-linux

[![tests](https://github.com/furibeh/m916-linux/actions/workflows/tests.yml/badge.svg)](https://github.com/furibeh/m916-linux/actions/workflows/tests.yml)

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

En Fedora, desde COPR. El paquete incluye la regla udev, la entrada del menú y el icono:

```sh
sudo dnf copr enable furibeh/m916-linux
sudo dnf install m916-linux
```

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
install -Dm644 m916/icons/io.github.furibeh.m916-linux.svg \
    ~/.local/share/icons/hicolor/scalable/apps/io.github.furibeh.m916-linux.svg
```

## Interfaz gráfica

Pestañas de DPI (niveles, valores y colores), botones, rendimiento (frecuencia de sondeo,
debounce, LOD, modo y opciones del sensor), luces (efecto, brillo y velocidad del LED de DPI,
apagado en reposo) y copias y perfiles. Cada botón puede ser un clic, una
función de DPI o scroll, disparo repetido, bloqueo de DPI, un atajo de teclado (se escribe o
se captura pulsándolo), una tecla multimedia o una macro, con un editor que permite grabar
pulsaciones de teclado y ratón con sus tiempos. Los cambios se editan en local y
**Aplicar** escribe solo los campos modificados, verificando cada uno. Muestra la batería y
sigue en vivo los cambios hechos desde el propio ratón, como el nivel de DPI. Los perfiles
JSON se guardan y se cargan desde la pestaña **Copias y perfiles**: al cargar uno, sus valores
aparecen como cambios sin aplicar.

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
                              # scroll-up|scroll-down|scroll-left|scroll-right|report-rate|disabled
m916 button 7 key:ctrl+c      # atajo de hasta 3 teclas, o tecla multimedia: key:volume-up
m916 button 7 fire:20:3       # disparo repetido: cada 20 ms, 3 disparos (0 = mientras se pulse)
m916 button 7 dpi-lock:400    # DPI fijo mientras se mantiene pulsado
m916 macro 8 copiar "+ctrl 10 +c 10 -c 10 -ctrl" --repeat 1
                              # +TECLA pulsa, -TECLA suelta, número = espera en ms;
                              # --repeat N (1-252), while-held, toggle o until-any
m916 keys                     # nombres de tecla disponibles
m916 option debounce 4        # opciones de un byte: info muestra su significado y
                              # option --help los valores (lod, sensor-mode, dpi-led…)
m916 restore copia.bin        # reescribe solo lo que cambió y verifica
m916 profile save juegos      # guarda la configuración como perfil JSON
m916 profile load juegos      # aplica un perfil (--dry-run para ver qué cambiaría)
m916 profile list             # perfiles guardados en ~/.config/m916/profiles
m916 watch                    # notificaciones en vivo (DPI, batería…)
```

Cada escritura se verifica volviendo a leer. Si el ratón está dormido, la herramienta pide
moverlo y espera hasta 10 s.

## Perfiles

Además de la copia completa, la configuración se puede guardar como un perfil JSON legible y
editable, por ejemplo uno para juegos y otro para oficina. Al cargar un perfil solo se
escriben los campos que cambian, y lo que el perfil no incluye se queda como está. Este perfil
parcial solo toca la frecuencia, dos niveles de DPI y tres botones:

```json
{
  "format": "m916-profile",
  "version": 1,
  "model": "Redragon M916-PRO-1K",
  "report_rate": 1000,
  "dpi": {
    "count": 2,
    "active": 1,
    "levels": [
      {"x": 800, "y": 800, "color": "#00ff00"},
      {"x": 1600, "y": 1600, "color": "#ff0000"}
    ]
  },
  "buttons": {
    "6": {"action": "dpi-lock", "dpi": 400},
    "7": {"action": "shortcut", "keys": "ctrl+c"},
    "8": {"action": "macro", "name": "copiar", "repeat": 1,
          "steps": ["+ctrl 10", "+c 10", "-c 10", "-ctrl"]}
  }
}
```

Los perfiles se guardan en `~/.config/m916/profiles/`: en la CLI basta con el nombre
(`m916 profile load juegos`), aunque también se puede dar cualquier ruta terminada en `.json`.
`m916 profile save` escribe un perfil completo. El formato completo (opciones, disparo
repetido, teclas multimedia, macros y validación) está en [docs/PROFILES.md](docs/PROFILES.md).

## Tests

```sh
python3 -m unittest discover -s tests
```

Los tests simulan el ratón con un volcado real de la flash (`tests/fixtures/m916_flash.bin`),
así que no hace falta hardware. Los de la interfaz se saltan si PySide6 no está instalado.

## Estructura

- `m916/protocol.py`: transporte HID y acceso a la flash.
- `m916/config.py`: modelo de la configuración (decodificar, codificar, validar y diferencias),
  incluidos los bloques de atajos y macros.
- `m916/profile.py`: perfiles JSON (guardar, validar y aplicar sobre el modelo).
- `m916/keys.py`: nombres de teclas, códigos HID y correspondencia con los códigos de Linux.
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
todas las funciones de botón (incluidos atajos, teclas multimedia y macros), opciones de un
byte, volcado/restauración, perfiles JSON, notificaciones e interfaz gráfica.

Pendiente: las funciones del receptor (largo alcance, emparejamiento) y confirmar en el
hardware el modo del sensor y los tiempos de reposo.

## Licencia

[GPL-3.0-or-later](LICENSE). Proyecto independiente, sin relación con Redragon. Los nombres
de producto se usan solo para identificar el hardware compatible.
