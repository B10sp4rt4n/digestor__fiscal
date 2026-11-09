"""Shared local fiscal catalog; syntax/catalog checks are not SAT certification."""

# c_RegimenFiscal — código → (descripción, aplica_física, aplica_moral)
REGIMENES: dict[str, tuple[str, bool, bool]] = {
    "601": ("General de Ley Personas Morales", False, True),
    "603": ("Personas Morales con Fines no Lucrativos", False, True),
    "605": ("Sueldos y Salarios e Ingresos Asimilados a Salarios", True, False),
    "606": ("Arrendamiento", True, False),
    "607": ("Régimen de Enajenación o Adquisición de Bienes", True, False),
    "608": ("Demás ingresos", True, False),
    "610": ("Residentes en el Extranjero sin Establecimiento Permanente en México", True, True),
    "611": ("Ingresos por Dividendos (socios y accionistas)", True, False),
    "612": ("Personas Físicas con Actividades Empresariales y Profesionales", True, False),
    "614": ("Ingresos por intereses", True, False),
    "615": ("Régimen de los ingresos por obtención de premios", True, False),
    "616": ("Sin obligaciones fiscales", True, False),
    "620": ("Sociedades Cooperativas de Producción que optan por diferir sus ingresos", False, True),
    "621": ("Incorporación Fiscal", True, False),
    "622": ("Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras", True, True),
    "623": ("Opcional para Grupos de Sociedades", False, True),
    "624": ("Coordinados", False, True),
    "625": ("Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas", True, False),
    "626": ("Régimen Simplificado de Confianza", True, True),
}
