# Lectura local de CSF escaneada

El lector mantiene la extracción directa de PDFs con texto. Si una página contiene un escaneo o una capa de texto insuficiente, rasteriza esa página y llama a Tesseract instalado en el mismo servicio. No utiliza APIs de IA, PAC ni servicios OCR externos. El PDF original se conserva íntegro.

## Alcance

- Reconoce español e inglés, con corrección de orientación 90/180/270 grados cuando el texto inicial no resulta reconocible.
- Admite páginas con texto e imágenes en el mismo documento y detecta escaneos con pequeñas capas de texto superpuestas.
- Guarda procedencia, páginas y confianza de lectura en el JSON de `field_validation`, incluyendo un registro `kind=extraction_metadata`; no requiere migración.
- `GET /csf/{id}.review` expone `extraction` y `fieldWarnings`. Los valores dudosos se señalan para revisión humana; no se sustituyen caracteres ni se inventan datos faltantes.
- La confianza de Tesseract mide reconocimiento de palabras; no es una probabilidad calibrada de exactitud fiscal. No acredita autenticidad ni situación ante SAT.
- Las revisiones exactas incluyen esta evidencia en su hash. Se mantienen confirmación explícita y controles por tenant.

## Límites y errores

Por defecto: PDF de hasta 8 páginas (máximo 5 con OCR), presupuesto de 45 segundos por documento, procesos de máximo 12 segundos y rasterización de hasta aproximadamente 10 millones de píxeles/4200 de lado. La API de Cotiza conserva su límite de 4 MiB por archivo.

`CSF_OCR_ENABLED`, `CSF_OCR_LANGUAGE`, `CSF_OCR_TIMEOUT_SECONDS`, `CSF_PDF_MAX_PAGES`, `CSF_OCR_MAX_PAGES` y `CSF_OCR_MIN_CONFIDENCE` permiten ajustar el lector dentro de límites definidos. Los errores `OCR_UNAVAILABLE`, `OCR_TIMEOUT`, `OCR_UNREADABLE`, `PDF_LIMIT`, `PDF_ENCRYPTED` y `PDF_INVALID` se comunican sin exponer detalles internos. Si la lectura no identifica una CSF, el usuario puede cargar una copia más clara o continuar con captura manual.

`railpack.json` añade Tesseract e idiomas spa/eng/osd al runtime, extendiendo los paquetes del proveedor con `...`. No cambia variables ni credenciales del servicio.

## Pruebas reproducibles

`tests/csf_scan_fixtures.py` genera documentos ficticios marcados **SINTETICO / SIN VALIDEZ FISCAL**, rasterizados sin texto oculto. No requiere constancias reales. Cubre texto nativo, imagen limpia, cuatro orientaciones, varias páginas, documentos mixtos, desenfoque, baja legibilidad, campos faltantes y capas superpuestas. Las pruebas verifican también PDF dañado/con contraseña, lector ausente, timeout, límites y propagación de avisos a la revisión.

La prueba HTTP recorre upload → job → worker real → SQLite temporal → revisión OCR → confirmación y auditoría, sin red, IA o PAC. Los casos reales del SAT se mantienen como validación adicional pendiente; las pruebas sintéticas no prueban todos los diseños ni calidades de escaneo.

Requisitos locales/CI: Tesseract y modelos español, inglés y orientación. Ejecutar:

```bash
DATABASE_URL=sqlite:///:memory: DB_SSL_REQUIRE=false .venv/bin/python -m pytest -q tests
```

Referencias primarias: [Tesseract TSV y CLI](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage), [orientación y calidad](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html), [paquetes de runtime en Railpack](https://railpack.com/guides/installing-packages).
