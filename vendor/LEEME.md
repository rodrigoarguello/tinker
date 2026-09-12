# vendor/

Copias versionadas de cosas cuya fuente de verdad vive fuera del proyecto.
Existen para que `compose.dev.yaml` construya en cualquier maquina.

## diseno/tokens/onda.css

Copia de `/srv/01-infra/diseno/tokens/onda.css` del servidor de onda.

En produccion NO se usa este archivo: `compose.yaml` monta el directorio real
como contexto adicional de build, y por eso tinker no figura en
`consumidores.txt` del sistema de diseno. Si los tokens cambian alla, esta
copia queda vieja sin que nada avise — actualizarla a mano.
