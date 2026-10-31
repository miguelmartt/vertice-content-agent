# vertice-content-agent

[Read in English](README.md)

Agente de contenido para el blog de una web Laravel. Vive en tu VPS como servicio de systemd, investiga qué busca la gente, redacta artículos con Claude Code y te pide permiso por Telegram antes de publicar nada.

Lo hice para el blog de [VérticeDev](https://verticedev.es) y lo publico tal cual, con licencia MIT.

- **Investiga** cada lunes qué se busca: las consultas reales de Search Console (90 días) y el autocompletado de Google a partir de tus servicios, tus consultas principales y las semillas que le des.
- **Propone** tres temas por semana: mejoras de artículos que ya tienen impresiones y artículos nuevos con demanda y relación con un servicio. Cada propuesta lleva sus cifras y un ángulo que solo tú puedes dar.
- **Redacta** con Claude Code y tu suscripción, no con la API. Valida el borrador con las reglas de la web, lo escribe en el registro `Blog.php` y en su vista Blade y abre un PR.
- **Publica** cuando pulsas «Publicar»: rehace la rama sobre el `main` actual, espera a que la CI esté en verde y fusiona. El despliegue lo sigues lanzando tú.
- **Aprende** midiendo cada artículo a los 14, 28 y 56 días en Search Console y de lo que apruebas, descartas y comentas.
- **Revisa tus servicios** el día 1 de cada mes: qué servicios tienen búsquedas, cuáles no y qué demanda no cubre ninguno.

Solo librería estándar de Python (3.9 o superior), SQLite y long polling de Telegram: sin dependencias y sin puertos abiertos.

## Cómo funciona

```
lunes 08:00 ─ investigar ─ Search Console + autocompletado → candidatas puntuadas
             └ planificar ─ Claude elige 3 temas con datos → Telegram [Redactar] [Ajustar] [Descartar]
Redactar ─── Claude escribe bloques (no HTML) → validación → vista Blade + Blog.php → PR → Telegram
             [Publicar] [Pedir cambios] [Descartar]
Publicar ─── rama sobre main con la fecha de hoy → CI en verde → merge del commit revisado
cada 15 min ─ ¿ya está en producción? → mide a los 14, 28 y 56 días → ajusta los pesos
día 1 ────── informe de servicios + memoria de aprendizajes
```

### Qué es «aprender» aquí

No se entrena ningún modelo. Hay un bucle de mejora con tres piezas:

1. **Puntuación con pesos.** Cada palabra clave tiene señales entre 0 y 1: impresiones, oportunidad de posición (4–20), demanda en el autocompletado, encaje con un servicio, si ya hay un artículo que la cubre e intención (comercial, comparación, local). La puntuación es la suma ponderada.
2. **Ajuste por resultados.** A los 28 y 56 días de publicar, el agente compara lo que predijo con lo que midió en Search Console y corrige los pesos por descenso de gradiente: las señales que acertaron ganan peso. Con poco tráfico, los primeros ajustes útiles llegan a los dos o tres meses.
3. **Tus decisiones.** Lo que apruebas y descartas por categoría, intención y tipo multiplica la puntuación. Los motivos de descarte y tus comentarios a los borradores entran en una memoria mensual que resume Claude y que se usa al planificar.

`/aprendizaje` en Telegram enseña los pesos, tus preferencias y la memoria.

### Qué no hace

- No publica nada sin tu botón.
- No despliega ni toca la carpeta de la web en producción: trabaja en su propio clon.
- No cambia tus servicios: solo te los comenta en el informe mensual.
- No inventa cifras ni experiencias: lo que necesita de ti lo pregunta en el borrador («Necesito que me confirmes»).

## Seguridad

El agente trata como no fiable todo lo que escribe la IA y todo lo que lee de Google: un texto puede intentar colarse en el artículo. Por eso:

- **La IA no escribe HTML ni Blade.** Devuelve bloques que el agente convierte en HTML. Cualquier `@`, `{` o `}` del texto se guarda como entidad HTML: el lector ve lo mismo y Blade no puede compilar nada. Al final, una comprobación rechaza el borrador si queda sintaxis de Blade fuera de la plantilla.
- **Claude no tiene herramientas**: sin acceso a archivos, a la red ni a comandos, en una carpeta vacía y solo con su token.
- **Cada proceso recibe lo mínimo.** PHP y git no ven los tokens del agente. Claude no recibe nunca una clave de API, así que siempre usa tu suscripción.
- **Solo tú** puedes usar el bot: tu usuario, en tu chat privado con él.
- **Nada se fusiona sin ti.** Cada botón «Publicar» va ligado a su borrador, y la fusión solo se hace sobre el commit cuya CI ha pasado.
- **Servicio aislado con systemd**: solo escribe en su carpeta de estado, no puede ejecutar nada de ella salvo Claude Code y no puede modificar ese binario.

Modelo de amenazas completo y cómo informar de un fallo: [SECURITY.md](SECURITY.md).

## Comandos de Telegram

| Comando | Qué hace |
|---|---|
| `/plan` | Propuestas y borradores abiertos |
| `/investigar` | Investigación y plan ahora |
| `/estado` | Qué está haciendo y qué tiene pendiente |
| `/aprendizaje` | Pesos, preferencias y memoria |
| `/informe` | Informe de servicios ahora |
| `/cancelar` | Olvida la respuesta que estaba esperando |

## Qué espera de tu web

Está hecho para una web Laravel que guarda el blog como la de VérticeDev:

- un registro de artículos en `app/Support/Blog.php`: un método privado `posts()` que devuelve un array, una constante `CATEGORIES` y los registros `Services` y `Portfolio` junto a él;
- una vista por artículo en `resources/views/blog/<slug>.blade.php` que extiende `layouts.article`;
- CI con GitHub Actions que renderiza las páginas, para que un artículo roto no llegue a `main`.

Si tu web es distinta, lo que hay que adaptar está en `vca/site.py` y `vca/render.py`. Lo demás (investigación, aprendizaje, Telegram y GitHub) no depende de Laravel. Más detalle en [CONTRIBUTING.md](CONTRIBUTING.md).

## Puesta en marcha

### 1. En tu ordenador

1. **Bot de Telegram.** Créalo con @BotFather y guarda el token. Escríbele en privado y saca tu id de `https://api.telegram.org/bot<TOKEN>/getUpdates` (`message.chat.id`).
2. **Claude Code.** `claude setup-token` genera un token de larga duración de tu suscripción: es `CLAUDE_CODE_OAUTH_TOKEN`.
3. **Search Console.** Crea en Google Cloud un cliente OAuth de tipo «Aplicación de escritorio», con la API de Search Console activada y el estado de publicación en «En producción» (en «Prueba», el token caduca a los 7 días). Después:

   ```bash
   python -m vca gsc-auth --client-secrets ruta/al/cliente.json
   ```

   Inicia sesión con la cuenta propietaria. Te imprime las tres variables `VCA_GSC_*`, con permiso de solo lectura.
4. **GitHub.** Crea un token fine-grained solo para el repositorio de tu web, con Contents y Pull requests en lectura y escritura, y Actions, Commit statuses y Metadata en lectura. Los tokens fine-grained no admiten Checks: el agente lee la CI a través de Actions.

### 2. En el VPS (como root)

```bash
git clone https://github.com/miguelmartt/vertice-content-agent.git /root/vertice-content-agent
bash /root/vertice-content-agent/deploy/install.sh
runuser -u vca -- env HOME=/var/lib/vertice-content-agent bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
nano /etc/vertice-content-agent/env      # tus datos y tokens
bash /root/vertice-content-agent/deploy/install.sh   # comprueba todo y arranca
vca check --claude                       # opcional: una llamada real a Claude
journalctl -u vertice-content-agent -f
```

El instalador crea el usuario de sistema `vca`, copia el código a `/opt/vertice-content-agent`, guarda el estado en `/var/lib/vertice-content-agent` (SQLite y su clon de la web) y deja el servicio aislado. No arranca hasta que `vca check` pasa.

Para actualizar: `git -C /root/vertice-content-agent pull` y otra vez `install.sh`.

Para probar sin abrir PR, pon `VCA_DRY_RUN=1`: Telegram funciona, pero no sube ramas.

## Línea de órdenes

En el VPS, el instalador deja el atajo `vca`, que ejecuta como el usuario del servicio y con su configuración:

```bash
vca check            # configuración y conexiones (--claude: también una llamada real)
vca research         # candidatas en consola, sin Claude ni Telegram
vca plan             # plan ahora
vca learn            # medición y aprendizaje ahora
vca report           # informe de servicios ahora
vca status           # estado y pesos
```

En tu ordenador, lo mismo con `python -m vca --env-file mi.env <orden>` desde la carpeta del repositorio.

## Límites conocidos

- El autocompletado de Google no tiene API oficial: da señales de demanda, no volúmenes. El agente pide como mucho 300 consultas por semana, con pausas, y si Google le frena (429) lo deja hasta la semana siguiente.
- Claude Code gasta del cupo de tu suscripción. Hay un tope diario de llamadas (`VCA_LLM_MAX_CALLS_PER_DAY`); un plan semanal y un borrador son unas 2 o 3.
- Con poco tráfico, el aprendizaje por resultados es lento. Hasta tener datos, mandan las señales por defecto y tus decisiones.

## Tests

```bash
python -m unittest discover -s tests -t .
```

Incluyen un recorrido completo con dobles de Telegram, GitHub y Search Console (plan, aprobación, borrador, PR, CI, merge, producción y medición) y los casos de seguridad: inyección de Blade, autorización del bot, botones antiguos, secretos en errores y en subprocesos.

## Licencia

[MIT](LICENSE) © 2026 VerticeDev
