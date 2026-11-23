# Seguridad

## Versiones con soporte

Solo la última versión de la rama `main` recibe correcciones de seguridad.

## Reportar una vulnerabilidad

No abras un issue público. Usa **Security → Report a vulnerability** en GitHub (aviso privado) con:

- qué parte de la app se ve afectada y cómo reproducirlo;
- el impacto que crees que tiene.

Recibirás respuesta lo antes posible y aviso cuando esté corregido.

## Buenas prácticas para quien usa la app

- La app está pensada para correr en tu propia PC (`localhost`). No la expongas a internet sin protección.
- Tus credenciales OAuth (`client_secret*.json`) y la carpeta `data/` nunca deben subirse a un repositorio.
- Autoriza solo las carpetas que la app necesita leer.
