# vertice-content-agent

Agente de contenido del blog de [VérticeDev](https://verticedev.es). Vive en el VPS como servicio de systemd y te habla por Telegram.

- **Investiga** cada lunes qué se busca: las consultas reales de Search Console (90 días) y el autocompletado de Google a partir de tus servicios, tus consultas principales y las semillas que le des.
- **Propone** tres temas por semana: mejoras de artículos que ya tienen impresiones y artículos nuevos con demanda y relación con un servicio. Cada propuesta lleva sus cifras y un ángulo que solo tú puedes dar.
- **Redacta** con Claude Code y tu suscripción, no con la API. Valida el borrador con las reglas de la web, lo escribe en el registro `Blog.php` y en su vista Blade y abre un PR.
- **Publica** cuando pulsas «Publicar»: rehace la rama sobre el `main` actual, espera a que CI esté en verde y fusiona. El despliegue (`deploy.sh`) lo sigues lanzando tú.
- **Aprende** midiendo cada artículo a los 14, 28 y 56 días en Search Console y de lo que apruebas, descartas y comentas.
- **Revisa tus servicios** el día 1 de cada mes: qué servicios tienen búsquedas, cuáles no y qué demanda no cubre ninguno.

Solo librería estándar de Python (3.9 o superior), SQLite y long polling de Telegram: sin dependencias y sin puertos abiertos.

## Cómo funciona

```
lunes 08:00 ─ investigar ─ Search Console + autocompletado → candidatas puntuadas
             └ planificar ─ Claude elige 3 temas con datos → Telegram [Redactar] [Ajustar] [Descartar]
Redactar ─── Claude escribe bloques (no HTML) → validación → vista Blade + Blog.php → PR → Telegram
             [Publicar] [Pedir cambios] [Descartar]
Publicar ─── rama sobre main con la fecha de hoy → CI en verde → merge → «lanza deploy.sh»
cada 15 min ─ ¿ya está en producción? → mide a los 14, 28 y 56 días → ajusta los pesos
día 1 ────── informe de servicios + memoria de aprendizajes
```

### Qué es «aprender» aquí

No se entrena ningún modelo. Hay un bucle de mejora con tres piezas:

1. **Puntuación con pesos.** Cada palabra clave tiene señales entre 0 y 1: impresiones, oportunidad de posición (4–20), demanda en el autocompletado, encaje con un servicio, si ya hay un artículo que la cubre, intención (comercial, comparación, local). La puntuación es la suma ponderada.
2. **Ajuste por resultados.** A los 28 y 56 días de publicar, el agente compara lo que predijo con lo que midió en Search Console y corrige los pesos por descenso de gradiente: las señales que acertaron ganan peso. Con el poco tráfico actual, los primeros ajustes útiles llegarán a los dos o tres meses.
3. **Tus decisiones.** Lo que apruebas y descartas por categoría, intención y tipo multiplica la puntuación. Los motivos de descarte y tus comentarios a los borradores entran en la memoria mensual que resume Claude, y esa memoria se usa al planificar.

`/aprendizaje` en Telegram enseña los pesos, tus preferencias y la memoria.

### Qué no hace

- No publica nada sin tu botón.
- No ejecuta `deploy.sh` ni toca `/var/www/verticedev`: trabaja en su propio clon.
- No cambia los servicios: solo te los comenta en el informe mensual.
- No inventa cifras ni experiencias: lo que necesita de ti lo pregunta en el borrador («Necesito que me confirmes»).

## Comandos de Telegram

| Comando | Qué hace |
|---|---|
| `/plan` | Propuestas y borradores abiertos |
| `/investigar` | Investigación y plan ahora |
| `/estado` | Qué está haciendo y qué tiene pendiente |
| `/aprendizaje` | Pesos, preferencias y memoria |
| `/informe` | Informe de servicios ahora |
| `/cancelar` | Olvida la respuesta que estaba esperando |

Solo atiende al chat de `VCA_TELEGRAM_CHAT_ID`.

## Puesta en marcha

### 1. En tu ordenador

1. **Bot de Telegram.** Crea uno con @BotFather y guarda el token. Escríbele cualquier cosa y saca tu chat id de `https://api.telegram.org/bot<TOKEN>/getUpdates` (`message.chat.id`).
2. **Claude Code.** `claude setup-token` genera un token de larga duración de tu suscripción. Guárdalo: es `CLAUDE_CODE_OAUTH_TOKEN`.
3. **Search Console.** Usa el cliente OAuth «Aplicación de escritorio» que ya tienes para el MCP `gsc` (el JSON de `GSC_OAUTH_CLIENT_SECRETS_FILE`) y ejecuta:

   ```bash
   python -m vca gsc-auth --client-secrets ruta/al/cliente.json
   ```

   Inicia sesión con la cuenta propietaria. Te imprime las tres variables `VCA_GSC_*`, con alcance de solo lectura.
4. **GitHub.** Crea un token fine-grained solo para `miguelmartt/verticedev` con Contents y Pull requests en lectura y escritura, y Checks, Commit statuses y Metadata en lectura.

### 2. En el VPS (como root)

```bash
git clone git@github.com:miguelmartt/vertice-content-agent.git /root/vertice-content-agent
bash /root/vertice-content-agent/deploy/install.sh
runuser -u vca -- env HOME=/var/lib/vertice-content-agent bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
nano /etc/vertice-content-agent/env      # pega los tokens
bash /root/vertice-content-agent/deploy/install.sh   # comprueba todo y arranca
journalctl -u vertice-content-agent -f
```

El instalador crea el usuario de sistema `vca`, copia el código a `/opt/vertice-content-agent`, guarda el estado en `/var/lib/vertice-content-agent` (SQLite y su clon de la web) y deja el servicio aislado: solo puede escribir en su carpeta de estado. No arranca hasta que `python -m vca check` pasa.

Para actualizar: `git -C /root/vertice-content-agent pull` y otra vez `install.sh`.

Para probar sin abrir PR, pon `VCA_DRY_RUN=1`: Telegram funciona, pero no sube ramas.

## Línea de órdenes

En el VPS, el instalador deja el atajo `vca`, que ejecuta como el usuario del servicio y con su configuración:

```bash
vca check        # configuración y conexiones
vca research     # candidatas en consola, sin Claude ni Telegram
vca plan         # plan ahora
vca learn        # medición y aprendizaje ahora
vca report       # informe de servicios ahora
vca status       # estado y pesos
```

En tu ordenador, lo mismo con `python -m vca <orden>` desde la carpeta del repositorio.

## Límites conocidos

- El autocompletado de Google no tiene API oficial: da señales de demanda, no volúmenes. El agente pide como mucho 300 consultas por semana, con pausas, y si Google le frena (429) lo deja hasta la semana siguiente.
- Claude Code gasta del cupo de tu suscripción. Hay un tope diario de llamadas (`VCA_LLM_MAX_CALLS_PER_DAY`); un plan semanal y un borrador son unas 2 o 3.
- Con el tráfico actual, el aprendizaje por resultados es lento. Hasta tener datos, mandan las señales por defecto y tus decisiones.

## Tests

```bash
python -m unittest discover -s tests -t .
```

Incluyen un recorrido completo con dobles de Telegram, GitHub y Search Console: plan, aprobación, borrador, PR, CI, merge, producción y medición.
