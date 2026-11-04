# m916-linux

[![tests](https://github.com/furibeh/m916-linux/actions/workflows/tests.yml/badge.svg)](https://github.com/furibeh/m916-linux/actions/workflows/tests.yml)

[English](README.md) · **Español**

Herramienta para configurar en Linux el ratón **Redragon M916-PRO-1K** sin el programa
oficial de Windows. Habla con el ratón por `hidraw` con el mismo protocolo que el configurador
original, reconstruido por ingeniería inversa (ver [docs/PROTOCOL.md](docs/PROTOCOL.md), en
inglés).

Probado con el receptor inalámbrico `3554:F5D5` y por el cable USB (`3554:F55E`). Mientras el
cable está puesto, el ratón solo responde por él, así que la herramienta usa el cable y vuelve
al receptor al quitarlo. `-d /dev/hidrawN` fuerza un dispositivo.

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
debounce, LOD, modo y opciones del sensor, y modo de largo alcance), luces (efecto, brillo y
velocidad del LED de DPI, apagado en reposo) y copias y perfiles. Cada botón puede ser un clic, una
función de DPI o scroll, disparo repetido, bloqueo de DPI, un atajo de teclado (se escribe o
se captura pulsándolo), una tecla multimedia o una macro, con un editor que permite grabar
pulsaciones de teclado y ratón con sus tiempos. Los cambios se editan en local y
**Aplicar** escribe solo los campos modificados, verificando cada uno. Muestra la batería y
sigue en vivo los cambios hechos desde el propio ratón, como el nivel de DPI. El selector
**Ranura del ratón** de la cabecera cambia entre las cuatro ranuras del ratón (ver más abajo).
Los perfiles
JSON se guardan y se cargan desde la pestaña **Copias y perfiles**: al cargar uno, sus valores
aparecen como cambios sin aplicar.

Para probarla sin el ratón: `python3 -m m916.gui --fake tests/fixtures/m916_flash.bin`.

## Idioma

La interfaz gráfica y la línea de comandos usan el idioma del sistema: hay traducción al
español y, para el resto de idiomas, se muestran en inglés. Para elegir otro idioma,
usa la variable `LANGUAGE`:

```sh
LANGUAGE=en m916-gui        # en inglés
LANGUAGE=es m916 --help     # en español
```

Las palabras fijas de la ayuda de la CLI («usage:», «options») las pone Python y salen en
inglés. Los nombres de acciones, opciones y teclas (`dpi-loop`, `debounce`, `ctrl`…) no se
traducen, porque son los valores que se escriben en los comandos y en los perfiles.

Para traducir el programa a otro idioma, ver [Translations](README.md#translations).

## Línea de comandos

```sh
m916 info                     # estado, batería, ranura activa, DPI, opciones y botones
m916 dump copia.bin           # haz esto antes de cambiar nada
m916 slot                     # ranura activa del ratón (1-4); «m916 slot 2» activa la 2
m916 dpi 3 3200               # DPI de un nivel (múltiplos de 50, hasta 26000; --y para Y)
m916 dpi-color 3 00ff00       # color del LED de un nivel
m916 dpi-levels 5             # niveles DPI activos
m916 dpi-active 2             # nivel en uso
m916 rate 1000                # 125 / 250 / 500 / 1000 Hz
m916 button 7 middle          # left|right|middle|back|forward|dpi-loop|dpi-up|dpi-down|
                              # scroll-up|scroll-down|scroll-left|scroll-right|report-rate|
                              # lights|next-slot|disabled
m916 button 7 key:ctrl+c      # atajo de hasta 3 teclas, o tecla multimedia: key:volume-up
m916 button 7 fire:20:3       # disparo repetido: 3 clics de 20 ms pulsado + 20 ms suelto (0 = mientras se pulse)
m916 button 7 dpi-lock:400    # DPI fijo mientras se mantiene pulsado
m916 macro 8 copiar "+ctrl 10 +c 10 -c 10 -ctrl" --repeat 1
                              # +TECLA pulsa, -TECLA suelta, número = espera en ms;
                              # --repeat N (1-252), while-held, toggle o until-any
m916 keys                     # nombres de tecla disponibles
m916 option debounce 4        # opciones de un byte: info muestra su significado y
                              # option --help los valores (lod, sensor-mode, dpi-led…)
m916 restore copia.bin        # reescribe solo lo que cambió y verifica
m916 dump --slot 2 r2.bin     # --slot N: trabaja sobre la ranura N y vuelve a la activa
m916 profile save juegos      # guarda la configuración como perfil JSON
m916 profile load juegos      # aplica un perfil (--dry-run para ver qué cambiaría)
m916 profile list             # perfiles guardados en ~/.config/m916/profiles
m916 long-range               # modo de largo alcance: on u off; «m916 long-range on» lo activa
m916 watch                    # notificaciones en vivo (DPI, batería…)
```

Cada escritura se verifica volviendo a leer. Si el ratón está dormido, la herramienta pide
moverlo y espera hasta 10 s.

## Ranuras del ratón

El ratón guarda cuatro configuraciones independientes, que aquí llamamos *ranuras* (en inglés,
*slots*; «configs» en el protocolo de la aplicación oficial). Cada una es una copia completa
de la memoria: DPI, botones, macros, luces y opciones. El ratón usa la activa, y un botón con
la función `next-slot` («Siguiente ranura» en la interfaz gráfica) pasa a la siguiente, de la 4
a la 1. Como cada ranura tiene sus propios botones, da a ese botón la misma función en todas
las ranuras entre las que quieras saltar; si no, `m916 slot N` o el selector te devuelven.
`info`, los comandos de configuración y la interfaz gráfica trabajan sobre la ranura activa.

- `m916 slot` muestra la ranura activa y `m916 slot N` la cambia.
- `dump`, `restore`, `profile save` y `profile load` admiten `--slot N`: cambian a la ranura
  N, hacen su trabajo y vuelven a la que estaba activa, también si algo falla.
- Una copia (`.bin`) no guarda de qué ranura es; la interfaz gráfica propone un nombre como
  `m916-slot2-20261103-1200.bin`. Se puede restaurar en cualquier ranura, así que esto copia la
  ranura 2 en la 3: `m916 dump --slot 2 r2.bin && m916 restore --slot 3 r2.bin`.
- En la interfaz gráfica, al elegir una ranura pasa a ser la activa en el ratón y se carga
  (si hay cambios sin aplicar, pregunta antes). Si el ratón cambia de ranura por su cuenta, la
  ventana la sigue al momento.
- El modo de largo alcance (`m916 long-range`, o la casilla de la pestaña **Rendimiento**) no
  pertenece a ninguna ranura: el ratón lo guarda aparte, vale para todas y cambiar de ranura no
  lo cambia. Ni las copias de seguridad, ni `restore`, ni los perfiles JSON lo incluyen. En la
  interfaz gráfica se aplica en cuanto pulsas la casilla, sin **Aplicar**. No se ha medido si
  aumenta el alcance.

## Perfiles

Además de la copia completa, la configuración se puede guardar como un perfil JSON legible y
editable, por ejemplo uno para juegos y otro para oficina. Al cargar un perfil solo se
escriben los campos que cambian, y lo que el perfil no incluye se queda como está. Un perfil
no pertenece a ninguna ranura: se aplica a la activa o a la indicada con `--slot`. Este perfil
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
repetido, teclas multimedia, macros y validación) está en [docs/PROFILES.md](docs/PROFILES.md)
(en inglés).

## Tests

```sh
python3 -m unittest discover -s tests
```

Los tests simulan el ratón con un volcado real de la flash (`tests/fixtures/m916_flash.bin`),
así que no hace falta hardware. Los de la interfaz se saltan si PySide6 no está instalado.
`tests/test_i18n.py` comprueba que todos los textos estén traducidos y que los catálogos
compilados estén al día.

## Estructura

- `m916/protocol.py`: transporte HID y acceso a la flash.
- `m916/config.py`: modelo de la configuración (decodificar, codificar, validar y diferencias),
  incluidos los bloques de atajos y macros.
- `m916/profile.py`: perfiles JSON (guardar, validar y aplicar sobre el modelo).
- `m916/keys.py`: nombres de teclas, códigos HID y correspondencia con los códigos de Linux.
- `m916/cli.py` y `m916/gui.py`: las dos interfaces, sobre el mismo modelo.
- `m916/fake.py`: ratón simulado para tests y desarrollo.
- `m916/i18n.py`, `po/` y `m916/locale/`: traducciones (gettext); `tools/i18n.sh` las
  actualiza.

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
byte, volcado/restauración, las cuatro ranuras del ratón, perfiles JSON, modo de largo
alcance, notificaciones e interfaz gráfica.

Pendiente: el emparejamiento, medir si el modo de largo alcance aumenta el alcance y confirmar
en el hardware el modo del sensor y los tiempos de reposo.

## Licencia

[GPL-3.0-or-later](LICENSE). Proyecto independiente, sin relación con Redragon. Los nombres
de producto se usan solo para identificar el hardware compatible.
