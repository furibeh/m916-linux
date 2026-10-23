# m916-linux

Herramienta para configurar en Linux el ratón **Redragon M916-PRO-1K** sin el programa
oficial de Windows. Habla con el ratón por `hidraw` con el mismo protocolo que el configurador
original, reconstruido por ingeniería inversa (ver [docs/PROTOCOL.md](docs/PROTOCOL.md)).

Probado con el receptor inalámbrico `3554:F5D5`.

## Requisitos

- Python 3 (sin dependencias externas).
- Acceso al nodo `hidraw` del ratón. Instala la regla udev una vez:

```sh
sudo cp udev/70-redragon-m916.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

## Uso

```sh
./m916.py info                     # estado, batería, DPI, opciones y botones
./m916.py dump copia.bin           # haz esto antes de cambiar nada
./m916.py dpi 3 3200               # DPI de un nivel (múltiplos de 50, hasta 26000; --y para Y)
./m916.py dpi-color 3 00ff00       # color del LED de un nivel
./m916.py dpi-levels 5             # niveles DPI activos
./m916.py dpi-active 2             # nivel en uso
./m916.py rate 1000                # 125 / 250 / 500 / 1000 Hz
./m916.py button 7 middle          # left|right|middle|back|forward|dpi-loop|dpi-up|dpi-down|
                                   # scroll-left|scroll-right|report-rate|disabled|fire:P1:P2
./m916.py option debounce 4        # opciones de un byte (valor en bruto)
./m916.py restore copia.bin        # reescribe solo lo que cambió y verifica
./m916.py watch                    # notificaciones en vivo (DPI, batería…)
```

Cada escritura se verifica volviendo a leer. Si el ratón está dormido, la herramienta pide
moverlo y espera hasta 10 s.

## Tests

```sh
python3 -m unittest discover -s tests
```

Los tests simulan el ratón con un volcado real de la flash (`tests/fixtures/m916_flash.bin`),
así que no hace falta hardware.

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
botones básicos, opciones de un byte, volcado/restauración y notificaciones.

Pendiente: atajos y macros en botones, significado de algunas opciones, LEDs y una interfaz
gráfica.
