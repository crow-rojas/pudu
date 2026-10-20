# Pudú

Bandeja local de PRs: lo que espera tu review y en qué estado están los tuyos. Corre en
`http://localhost:8765` y lee todo desde GitHub a través del CLI `gh`, con tu sesión. No hay
tokens que configurar ni servicios externos: solo un servidor Python con la biblioteca estándar
y una página HTML.

## Requisitos

- `python3` (3.9 o superior)
- macOS, solo para `install.sh` (servicio de launchd y app). El servidor corre en cualquier
  sistema con `python3 server.py`.
- [`gh`](https://cli.github.com/) con sesión iniciada (`gh auth login`)

## Instalar

```bash
./install.sh
```

El script revisa los requisitos, corre el autotest y deja dos cosas listas:

- **Un agente de launchd** (`local.pudu`) que levanta el servidor cuando inicias sesión y
  lo reinicia si se cae.
- **`~/Applications/Pudú.app`**, que abre Pudú en una pestaña de tu navegador por defecto.
  Búscala con Spotlight o arrástrala al Dock.

Para desinstalar ambas cosas: `./install.sh uninstall`.

## Uso diario

| Qué | Cómo |
|---|---|
| Abrir | `Pudú.app`, o `open http://localhost:8765` |
| Ver logs | `tail -f ~/Library/Logs/pudu.log` |
| Reiniciar tras editar `server.py` | `./install.sh` (no hace falta para cambios en `index.html`, que se lee en cada request) |
| Correrlo a mano | `./install.sh uninstall && python3 server.py` |
| Autotest | `python3 server.py --test` |

Si el puerto 8765 está ocupado al correrlo a mano (`Address already in use`), es porque el
agente de launchd ya lo está sirviendo.

## Elegir repos

El botón de repos, arriba a la derecha, lista los repos donde tienes PRs: los que te pidieron
revisar, los tuyos abiertos y los mergeados esta semana. Pudú no recorre carpetas; los repos
salen de esas mismas búsquedas en GitHub. Desmarca uno y sus PRs desaparecen de todas partes:
la página, el número de la pestaña, las notificaciones y `/inbox.md`. Un repo nuevo aparece
marcado, para que no se te pierda una review.

La selección se guarda en `~/.config/pudu/config.json` (o en la ruta de `PUDU_CONFIG`).

## Con Claude Code u otra CLI

Pudú entrega la bandeja como texto pensado para un LLM: una línea por PR con URL, autor, rama,
CI, estado de review, conflictos, hilos sin resolver, tamaño y antigüedad.

- **Desde la terminal:** `curl -s localhost:8765/inbox.md`. Para algunas secciones:
  `?s=review,returned` (ids: `review`, `returned`, `approved`, `waiting`, `draft`, `merged`,
  `bots`). Con `&force=1` salta la caché. En Claude Code, `! curl -s localhost:8765/inbox.md` lo
  deja directo en la conversación, o basta con pedirle a Claude que lo lea.
- **Desde la UI:** el botón **Copiar** de cada sección, **Copiar todo** arriba, `y` para el PR
  seleccionado y `Y` para todo. Copian exactamente lo que ves, con los filtros aplicados.

## Archivos

- `server.py`: servidor HTTP y consulta GraphQL a `gh`. Guarda la respuesta en caché 20 s para
  que varias pestañas compartan una sola llamada.
- `index.html`: toda la interfaz.
- `favicon.svg` / `pudu.icns`: ícono de la pestaña y de la app.
- `install.sh`: instalación del agente y de la app.

## Contribuir

Lee [CONTRIBUTING.md](CONTRIBUTING.md). En corto: sin dependencias, sin build y todo local.

## Licencia

[MIT](LICENSE).
