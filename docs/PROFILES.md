# Perfiles JSON

Un perfil es la configuración del ratón en un archivo JSON legible: DPI, botones (incluidos
atajos y macros), frecuencia de sondeo y opciones. Sirve para tener varias configuraciones,
por ejemplo una para juegos y otra para oficina, y cambiar entre ellas. No sustituye a la copia
completa (`m916 dump`), que guarda la memoria entera byte a byte.

```sh
m916 profile save juegos.json          # guarda la configuración actual
m916 profile load juegos.json --dry-run  # muestra cuántos campos cambiarían
m916 profile load juegos.json          # escribe solo lo que cambia y lo verifica
```

En la interfaz gráfica están en la pestaña **Copias y perfiles**. *Guardar perfil…* guarda lo
que se ve en pantalla, incluidos los cambios sin aplicar. *Cargar perfil…* rellena los
controles como cambios sin aplicar: revísalos y pulsa **Aplicar** o **Descartar cambios**.

La lógica está en `m916/profile.py`.

## Ejemplo completo

```json
{
  "format": "m916-profile",
  "version": 1,
  "model": "Redragon M916-PRO-1K",
  "description": "Juegos",
  "report_rate": 1000,
  "dpi": {
    "count": 5,
    "active": 2,
    "levels": [
      {"x": 1000, "y": 1000, "color": "#ff0000"},
      {"x": 2000, "y": 2000, "color": "#0000ff"},
      {"x": 4000, "y": 4000, "color": "#000000"},
      {"x": 8000, "y": 8000, "color": "#ff00ff"},
      {"x": 26000, "y": 26000, "color": "#ffff00"},
      {"x": 26000, "y": 26000, "color": "#ffff00"},
      {"x": 26000, "y": 26000, "color": "#ffff00"},
      {"x": 26000, "y": 26000, "color": "#ffff00"}
    ]
  },
  "options": {
    "lod": 1,
    "dpi-led-mode": 2,
    "dpi-led-brightness": 128,
    "dpi-led-speed": 3,
    "dpi-led": true,
    "debounce": 8,
    "motion-sync": true,
    "led-off-time": 6,
    "linear-correction": false,
    "ripple-control": false,
    "move-off-led": false,
    "full-performance": false,
    "full-performance-time": 6,
    "sensor-mode": 0
  },
  "buttons": {
    "1": "left",
    "2": "right",
    "3": "middle",
    "4": "back",
    "5": "forward",
    "6": "dpi-loop",
    "7": {"action": "shortcut", "keys": "ctrl+c"},
    "8": {
      "action": "macro",
      "name": "copiar",
      "repeat": 1,
      "steps": [
        "+ctrl 10",
        "+c 10",
        "-c 10",
        "-ctrl"
      ]
    },
    "9": "report-rate",
    "10": "dpi-up",
    "11": "dpi-down",
    "12": {"action": "fire", "interval": 10, "shots": 3},
    "13": {"action": "dpi-lock", "dpi": 400},
    "14": {"action": "media", "key": "volume-up"},
    "15": {"raw": [8, 1, 0]},
    "16": "disabled"
  }
}
```

## Reglas

### Cabecera

`format` (`"m916-profile"`), `version` (`1`) y `model` (`"Redragon M916-PRO-1K"`) son
obligatorios. Si la versión o el modelo no coinciden, el perfil se rechaza. `description` es
opcional y se ignora al cargar.

### Perfiles parciales

Todas las demás secciones son opcionales: **lo que falta no se toca**. Esto vale para
`report_rate`, `dpi`, `options` y `buttons`; dentro de `dpi`, para `count`, `active` y `levels`;
y para cada clave de `options` y de `buttons`. Por ejemplo, este perfil solo cambia el DPI
del nivel 1 y el botón 7:

```json
{
  "format": "m916-profile", "version": 1, "model": "Redragon M916-PRO-1K",
  "dpi": {"levels": [{"x": 800}]},
  "buttons": {"7": {"action": "shortcut", "keys": "ctrl+shift+t"}}
}
```

`levels` puede tener menos de 8 entradas (se aplican desde el nivel 1), y una entrada `null`
deja ese nivel como está.

### Campos

| Campo | Valor |
|---|---|
| `report_rate` | Hz: 125, 250, 500, 1000, 2000 o 4000. Si el ratón tiene un código desconocido, al guardar se omite (con un aviso). |
| `dpi.count` | Niveles habilitados, 1–8. Si cambia sin `active` y el nivel activo queda fuera, pasa al último habilitado. |
| `dpi.active` | Nivel activo, empezando en 1 (como en `m916 info`). |
| `dpi.levels[].x`, `.y` | DPI en múltiplos de 50, de 50 a 26000. `y` es opcional (igual que `x`). X e Y deben estar los dos por debajo o los dos por encima de 12800 (y de 25600). |
| `dpi.levels[].color` | `"#rrggbb"`, opcional. |
| `options` | Claves de `m916 option` con el valor en bruto (tiempos en unidades de 10 s, brillo 16–255, etc.; ver `m916 option --help`). Las opciones de sí/no (`dpi-led`, `motion-sync`, `linear-correction`, `ripple-control`, `move-off-led`, `full-performance`) usan `true`/`false`. |
| `buttons` | Claves `"1"`…`"16"`, las posiciones de `m916 info`. |

### Botones

| Valor | Función |
|---|---|
| `"left"`, `"right"`, `"middle"`, `"back"`, `"forward"`, `"dpi-loop"`, `"dpi-up"`, `"dpi-down"`, `"scroll-up"`, `"scroll-down"`, `"scroll-left"`, `"scroll-right"`, `"report-rate"`, `"disabled"` | Acción simple. |
| `{"action": "fire", "interval": 20, "shots": 3}` | Disparo repetido: intervalo de 10 a 255 ms, de 0 a 3 disparos (0 = mientras se pulse). |
| `{"action": "dpi-lock", "dpi": 400}` | DPI fijo mientras se pulsa: múltiplo de 50, de 50 a 12800. |
| `{"action": "shortcut", "keys": "ctrl+shift+t"}` | Atajo de hasta 3 teclas (nombres de `m916 keys`). |
| `{"action": "shortcut", "events": ["+ctrl", "+a", "-ctrl", "-a"]}` | Atajo cuyos eventos no son una combinación simple (hasta 6, sin esperas). |
| `{"action": "media", "key": "volume-up"}` | Tecla multimedia. |
| `{"action": "macro", "name": "…", "repeat": 1, "steps": […]}` | Macro (ver abajo). |
| `{"raw": [tipo, p1, p2]}` | Función que el programa no sabe interpretar. Se guarda en bruto para no perderla y al cargar se escribe tal cual. |

Los atajos guardan teclas físicas: la distribución de teclado la aplica el ordenador.

### Macros

- `name`: de 1 a 30 caracteres Latin-1.
- `repeat`: número de veces (1–252), `"while-held"` (mientras se pulse), `"toggle"` (hasta
  volver a pulsar) o `"until-any"` (hasta pulsar otro botón). Por defecto, 1.
- `steps`: de 1 a 70 pasos, uno por cadena, con la misma sintaxis que `m916 macro`: `+TECLA`
  pulsa, `-TECLA` suelta y un número detrás es la espera en ms tras ese paso (`"+c 10"`).
  También se aceptan los movimientos del puntero que graba el programa original
  (`"+move(5,-3)"`, de -128 a 127) y los códigos sin nombre (`"+key:0x99"`,
  `"-media:0x0123"`).

Cada macro ocupa el bloque de su propio botón. El firmware solo guarda un bloque de macro si
se escribe entero desde el principio, así que al cargar se reescribe el bloque completo y
después la función del botón.

## Validación

Al cargar se comprueba todo el perfil y se muestran todos los problemas a la vez, con su
posición en el archivo:

```
error: dpi.levels.3.color: must be "#rrggbb", not "red"
  buttons.7.steps: unknown key name 'ctlr'
```

Los campos desconocidos también son un error, para detectar erratas como `"repot_rate"`.
La CLI comprueba el formato antes de abrir el ratón. Si hay errores, no se escribe nada.
