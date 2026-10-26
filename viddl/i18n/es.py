"""Las palabras en español.

Same keys as :mod:`viddl.i18n.en`, which is the catalogue this one is checked
against (U7). A key left out here falls back to English rather than showing a
key name to a person, so a partly finished translation is still usable.

Two rules for translators, both visible below:

* the ``{...}`` placeholders keep their English names, because the app fills
  them in and only their values are translated;
* the app's own screens are still English, so advice that points at a control
  quotes the control as the screen has it — “Set up ffmpeg”, not a translation
  of it. A hint that renames a button helps nobody find it.
"""

from __future__ import annotations

CODE = "es"
NAME = "Español"

MESSAGES = {
    # -- qué ha fallado, en el orden en que viddl.errors prueba las reglas -----
    "error.cookies_read": "Viddl no ha podido leer las cookies de tu navegador.",
    "error.cookies_read.hint": (
        "Inicia sesión en el sitio con ese navegador y luego ciérralo antes de "
        "volver a intentarlo: un navegador abierto puede mantener el almacén "
        "bloqueado. También puedes usar otro navegador: elige «{sign_in}» en la "
        "pantalla de calidades."
    ),
    "error.sign_in": "El sitio pide que inicies sesión para ver este vídeo.",
    "error.sign_in.hint": (
        "Los vídeos con restricción de edad, con verificación anti-robot y solo "
        "para miembros necesitan una sesión iniciada. Elige «{sign_in}» y Viddl "
        "leerá las cookies que ese navegador ya tiene."
    ),
    "error.private": "Ese vídeo es privado.",
    "error.private.hint": (
        "Solo puede descargarlo su dueño. ¿Has abierto por error una versión "
        "pública?"
    ),
    "error.unavailable": "Ese vídeo ya no está disponible.",
    "error.unavailable.hint": (
        "Puede que lo hayan borrado, que lo hayan hecho privado o que esté "
        "bloqueado en tu región."
    ),
    "error.members": "Ese vídeo es solo para miembros del canal.",
    "error.ffmpeg": (
        "Viddl necesita ffmpeg para esta descarga y todavía no está instalado."
    ),
    "error.ffmpeg.hint": (
        "Viddl puede descargarlo por ti: elige «Set up ffmpeg» en la pantalla "
        "anterior."
    ),
    "error.no_results": "No se encontró nada para esa búsqueda.",
    "error.no_results.hint": (
        "Prueba con menos palabras, con otra ortografía o pega el enlace en su "
        "lugar."
    ),
    "error.not_a_video": "No se encontró ningún vídeo en ese enlace.",
    "error.not_a_video.hint": (
        "Comprueba que el enlace abre un vídeo (no un canal, una página de "
        "búsqueda, una foto ni una publicación sin vídeo)."
    ),
    "error.site_changed": "El sitio devolvió algo que Viddl no esperaba.",
    "error.site_changed.hint": (
        "Los sitios cambian sus páginas a menudo. Esperar un día y volver a "
        "intentarlo suele arreglarlo."
    ),
    "error.not_found": "No se encontró esa página (404).",
    "error.not_found.hint": (
        "Puede que el enlace esté mal escrito o que la página se haya eliminado."
    ),
    "error.refused": "El sitio rechazó la solicitud (403).",
    "error.refused.hint": (
        "A menudo es un límite momentáneo y no un bloqueo real, así que merece "
        "la pena intentarlo otra vez. Si sigue ocurriendo, puede que el vídeo "
        "esté restringido."
    ),
    "error.site_error": "El sitio tuvo un problema propio.",
    "error.site_error.hint": (
        "Suele durar poco, así que merece la pena intentarlo de nuevo en un "
        "momento."
    ),
    "error.rate_limited": "El sitio está limitando las peticiones en este momento.",
    "error.rate_limited.hint": (
        "Esperar un momento y volver a intentarlo suele resolverlo."
    ),
    "error.offline": "No se pudo llegar a internet.",
    "error.offline.hint": (
        "Comprueba tu conexión Wi-Fi o por cable y vuelve a intentarlo."
    ),
    "error.connection": "Falló la conexión con el sitio.",
    "error.connection.hint": (
        "Normalmente es una red inestable o un cortafuegos. Volver a intentarlo "
        "suele funcionar."
    ),
    "error.disk_full": "El disco está lleno.",
    "error.disk_full.hint": (
        "Libera espacio en tu carpeta de Descargas y vuelve a intentarlo."
    ),
    "error.not_allowed": "Viddl no tiene permiso para escribir ese archivo.",
    "error.not_allowed.hint": "Comprueba los permisos de tu carpeta de Descargas.",
    "error.write_failed": "Hubo un problema al escribir el archivo en el disco.",
    "error.quality_gone": "Esa calidad ya no se ofrece para este vídeo.",
    "error.quality_gone.hint": (
        "Vuelve atrás y elige otra calidad de la lista: las opciones cambian "
        "según el vídeo."
    ),
    "error.bad_link": "Eso no parece un enlace web.",
    "error.bad_link.hint": (
        "Copia la dirección de la barra de direcciones de tu navegador y pégala "
        "otra vez."
    ),
    "error.stopped": "Se detuvo la descarga.",
    # -- los comodines, para una salida que ninguna regla reconoció ------------
    "error.stopped_early": "La descarga se detuvo de forma inesperada.",
    "error.stopped_early.hint": (
        "Volver a intentarlo suele ayudar. Si sigue fallando, prueba con otra "
        "calidad."
    ),
    "error.generic": "Algo salió mal.",
    "error.reported": "El descargador informó de un error.",
    "error.reported.advice": (
        "Normalmente es un cambio temporal en el sitio. Volver a intentarlo "
        "dentro de un rato o usar otro enlace suele funcionar."
    ),
    "error.unfinished": "La descarga no terminó.",
    # -- el consejo que la app añade por su cuenta a un fallo (F3) -------------
    "engine.stale_advice": (
        "El motor de vídeo tiene {days} días, que suele ser el motivo de que un "
        "enlace deje de funcionar. Ejecuta `viddl --update` para reemplazarlo."
    ),
}
