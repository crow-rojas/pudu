# Contribuir a Pudú

Gracias por darte el tiempo. Pudú es chico a propósito, y la idea es que siga así.

## Principios

- **Sin dependencias.** El servidor usa solo la biblioteca estándar de Python y la interfaz es un
  único `index.html` sin build. Si un cambio necesita instalar algo, abre un issue antes.
- **GitHub entra solo por `gh`.** Pudú no guarda tokens ni credenciales: usa la sesión del CLI.
- **Todo local.** Nada sale de tu máquina salvo las consultas a GitHub.
- **Menos es más.** Antes de agregar una opción, pregúntate si alguien la va a usar hoy.

## Desarrollo

```bash
python3 server.py --test   # autotest
python3 server.py          # servidor en http://localhost:8765 (PORT=xxxx para cambiarlo)
```

Si tienes Pudú instalado como servicio, el puerto 8765 ya está ocupado: usa otro
(`PORT=8766 python3 server.py`) o desinstálalo con `./install.sh uninstall`.

Los cambios en `index.html` se ven con solo recargar. Los de `server.py` necesitan reiniciar el
servidor.

## Estilo

- Indentación de 2 espacios en Python y JavaScript.
- Los comentarios explican el **por qué**, no el qué, y empiezan con `why:`. Si el código se
  entiende solo, no lleva comentario.
- La interfaz está en español.

## Pull requests

1. Un cambio por PR, con una descripción de qué problema resuelve.
2. `python3 server.py --test` tiene que pasar; el CI lo corre en cada PR.
3. Si tocas la interfaz, adjunta una captura de antes y después (sin datos privados de tus repos).
4. Si cambias el formato del texto para LLMs (`md_line` / `inbox_md`), actualiza el autotest.

## Reportar un bug

Abre un issue con lo que esperabas, lo que pasó, tu versión de macOS, Python y `gh`, y las
últimas líneas de `~/Library/Logs/pudu.log` si aplica. Revisa que no incluyan nombres de repos o
PRs que no quieras hacer públicos.
