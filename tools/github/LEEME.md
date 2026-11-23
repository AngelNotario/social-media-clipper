# Plantillas de GitHub

Mueve el contenido de esta carpeta a `.github/` en la raíz del repositorio (crea la carpeta si no existe):

- `workflows/ci.yml`: corre las pruebas del backend y compila el frontend en cada push y pull request.
- `ISSUE_TEMPLATE/`: formularios para reportar fallos y proponer ideas.
- `PULL_REQUEST_TEMPLATE.md`: lista de verificación para los pull requests.

En PowerShell, desde la raíz del proyecto:

    Move-Item tools\github .github
    Remove-Item .github\LEEME.md
